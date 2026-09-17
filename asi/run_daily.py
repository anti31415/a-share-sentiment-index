# -*- coding: utf-8 -*-
"""
One command runs the whole day: fetch data -> score -> append to history -> print a report.
    python asi/run_daily.py

Replaces v1's workflow of "someone manually pastes a few hundred numbers into
build_inputs.py's source every day."
"""
import csv
import os
import sys

import confirm
import erp as erp_mod
import fetch
import history
import limitboard
import margin
import series
from compute import compute, InsufficientData

HERE = os.path.dirname(os.path.abspath(__file__))

# Filled by today_values(): how much of the stock sample actually loaded, so
# check_signal.py can report a thin cross-section instead of hiding it.
LAST_RUN = {}


def today_values(n_sample=900, workers=12, recent_n=15, with_bps=False):
    """Fetches every indicator's raw value for the latest trading day. The
    below-book-value rate is [measured across the full market] -- no
    sampling, no hardcoded common-sense constant.

    The sampled stocks' daily bars are prefetched concurrently (see the
    prewarm below); everything downstream then reads them straight out of the
    disk cache, so the numbers produced are identical to a serial run.

    Returns (date, today's values, today's index-panel row, (n_below, n_all),
    recent_panel). Every cross-sectional reading below is already computed for
    the whole `recent_n`-day window rather than for today alone, so
    `recent_panel` -- [{date, ...the same input keys}, ...], oldest first --
    costs no extra network. check_signal.py uses it to rebuild the recent ASI
    series without depending on when asi_history.csv was last committed.
    Reconstructing a *past* day's below-book-value rate does need the sampled
    stocks' annual-report BPS, so pass with_bps=True when the caller intends
    to score days other than today.
    """
    idx = fetch.index_daily()
    ip_all = series.index_panel(idx)
    ip = ip_all[-1]

    u = fetch.universe()
    # Denominator = names with a PB reading. Sina's listing has one for every
    # name; East Money's (the failover) leaves some blank, and counting those
    # as "not below book" would bias the rate down.
    n_all = sum(1 for r in u if r[3] is not None)
    n_bn = sum(1 for r in u if r[3] is not None and (r[3] <= 0 or r[3] < 1.0))
    bn = n_bn / n_all * 100.0

    # Breadth / new-low still need a cross-section, reconstructed from a sample
    sample = fetch.sample_universe(n_sample)
    recent_dates = [r["date"] for r in ip_all[-recent_n:]]  # small recent window, cheap to recompute daily

    # Fetch the sampled stocks' bars concurrently, once. Only the last ~400
    # bars are needed (252-day new-low window + up to a 90-day re-score
    # window), and fetch.stock_recent() goes to Tencent first with Sina as
    # failover: 900 full-history requests at 32 workers against Sina alone
    # drew HTTP 456 blocks on 2026-09-11 and 09-15 and crashed the run. A
    # stock that still fails on both sources is simply left out of the
    # cross-section below (bulk() returns None for it).
    bars = fetch.bulk(lambda x: fetch.stock_recent(fetch.sina_symbol(x[0], x[1])),
                      sample, workers=workers)
    kline_by_code = {row[0]: k for row, k in zip(sample, bars) if k}
    LAST_RUN.update(sample_n=len(sample), sample_loaded=len(kline_by_code))
    if with_bps:
        fetch.bulk(lambda x: fetch.stock_bps(x[0], x[1]), sample, workers=workers)

    # with_bps defaults to False: today's own below-book-value rate comes from
    # `bn` above (measured across the full market), so the per-stock BPS fetch
    # -- about half of a cold run's network work -- is dead weight unless past
    # days are being scored too.
    cs = series.cross_section(sample, recent_dates, with_bps=with_bps,
                              kline_by_code=kline_by_code)
    d = ip["date"]
    br = [cs[x]["breadth_raw"] for x in sorted(cs) if cs[x]["breadth_raw"] is not None][-5:]

    # Leverage sentiment (margin balance 5-day change) and speculation
    # extremity (limit-up/down reconstruction) -- added 2026-09, must be
    # included here too or the live daily signal silently falls back to the
    # original 4-dimension model instead of the full 6-dimension one used in
    # the backtest.
    margin_by_date = margin.chg5_series(recent_dates)
    lb = limitboard.cross_section(sample, recent_dates, kline_by_code)
    erp_by_date = erp_mod.daily_series(recent_dates)

    # Same assembly as series.build_panel() + history.build(), restricted to
    # the recent window: breadth is the 5-day mean of the raw reading, and the
    # below-book-value rate is the sample-based reconstruction (the column
    # asi_history.csv stores), not today's full-market snapshot -- so a day
    # scored from here is comparable with the committed history.
    br_sm = series.smooth([cs[x]["breadth_raw"] for x in recent_dates], 5)
    recent_panel = []
    for i, x in enumerate(recent_dates):
        rec = ip_all[-recent_n:][i]
        recent_panel.append({
            "date": x,
            "close": rec["close"],
            "broken_net_rate": cs[x]["broken_net_rate"],
            "erp": erp_by_date.get(x),
            "breadth": br_sm[i],
            "new_low": cs[x]["new_low"],
            "vol_temp": rec.get("vol_temp"),
            "rsi14": rec.get("rsi14"),
            "bias60": rec.get("bias60"),
            "drawdown": rec.get("drawdown"),
            "margin_chg5": margin_by_date.get(x),
            "limit_up_rate": lb[x]["up_rate"],
            "limit_down_rate": lb[x]["down_rate"],
            "max_consec_limit": lb[x]["max_consec"] if lb[x]["up_rate"] is not None else None,
        })

    return d, {
        "broken_net_rate": round(bn, 2),
        "erp": erp_by_date.get(d),
        "breadth": round(sum(br) / len(br), 2) if br else None,
        "new_low": round(cs[d]["new_low"], 2) if cs[d]["new_low"] is not None else None,
        "vol_temp": ip.get("vol_temp"),
        "rsi14": ip.get("rsi14"),
        "bias60": ip.get("bias60"),
        "drawdown": ip.get("drawdown"),
        "margin_chg5": margin_by_date.get(d),
        "limit_up_rate": lb[d]["up_rate"],
        "limit_down_rate": lb[d]["down_rate"],
        "max_consec_limit": lb[d]["max_consec"] if lb[d]["up_rate"] is not None else None,
    }, ip, (n_bn, n_all), recent_panel


def render(date, res, meta):
    L = ["=" * 74,
         "  A-Share Sentiment Index (ASI) v2",
         "=" * 74,
         "  Data date: %s   Shanghai Composite %.2f" % (date, meta["close"]),
         "",
         "   [ Sentiment score ]  %.1f / 100      coverage %.0f%%%s"
         % (res["score"], res["coverage"] * 100,
            "   [DEGRADED READING -- use with caution]" if res["degraded"] else ""),
         "   [ Zone ]  %s -- %s" % (res["zone"], res["zone_tag"]),
         "   [ What it means ]  %s" % res["zone_desc"],
         "",
         "   " + "-" * 68,
         "   %-24s %5s %7s   %s" % ("Dimension", "Wt", "Score", "Components"),
         "   " + "-" * 68]
    for d in res["dims"]:
        if d["missing"]:
            L.append("   %-24s %4d%%      -   missing" % (d["name"], d["weight"]))
            continue
        ms = "  ".join("%s %.2f%s->%.0f" % (m["name"], m["raw"], m["unit"], m["score"])
                       for m in d["members"] if not m["missing"])
        L.append("   %-24s %4d%%  %5.1f   %s" % (d["name"], d["weight"], d["score"], ms))
    L.append("   " + "-" * 68)
    L.append("")
    L.append("   [ Confirmation ] %s" % meta["confirm"]["msg"])
    L.append("")
    L.append("   Below-book-value rate denominator (measured): %d / %d stocks" % meta["bn"])
    L.append("=" * 74)
    return "\n".join(L)


def main():
    date, vals, ip, bn, _panel = today_values()
    try:
        res = compute(vals)
    except InsufficientData as e:
        print("Refused to score: %s" % e)
        sys.exit(2)

    hist = history.load() if os.path.exists(history.OUT) else []
    seq = [r["asi_v2"] for r in hist if r["asi_v2"] is not None] + [res["score"]]
    st = confirm.state(seq)

    print(render(date, res, {"close": ip["close"], "confirm": st, "bn": bn}))

    # append-only write, matching the full history.COLS schema
    if hist and hist[-1]["date"] == date:
        return
    with open(history.OUT, "a", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow([
            date, round(ip["close"], 2), vals["broken_net_rate"], "",
            _r(vals.get("erp"), 3), vals["breadth"], vals["new_low"],
            _r(ip.get("vol_ratio"), 3), _r(ip.get("ret5")),
            _r(vals["vol_temp"]), _r(vals["rsi14"]), _r(vals["bias60"]),
            _r(vals["drawdown"]), _r(vals.get("margin_chg5"), 3),
            _r(vals.get("limit_up_rate"), 3), _r(vals.get("limit_down_rate"), 3),
            vals.get("max_consec_limit") if vals.get("max_consec_limit") is not None else "",
            "", res["score"], res["coverage"], res["zone"], "", "", ""])
    print("Appended a row to asi_history.csv")


def _r(x, n=2):
    return "" if x is None else round(x, n)


if __name__ == "__main__":
    main()
