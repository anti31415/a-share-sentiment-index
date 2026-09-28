# -*- coding: utf-8 -*-
"""
One command runs the whole day: fetch data -> score -> append to history -> print a report.
    python asi/run_daily.py

Replaces v1's workflow of "someone manually pastes a few hundred numbers into
build_inputs.py's source every day."
"""
import bisect
import csv
import os
import sys

import confirm
import erp as erp_mod
import fetch
import history
import iv as iv_mod
import margin
import market_stats
import series
from compute import compute, InsufficientData
from net import days_between

HERE = os.path.dirname(os.path.abspath(__file__))

# A daily input older than this many calendar days is treated as missing
# rather than carried forward (the below-book series publishes with ~1 day lag).
MAX_STALE_DAYS = 4
POOL_DAYS = 25


def _latest_on_or_before(series_by_date, keys, d):
    j = bisect.bisect_right(keys, d) - 1
    if j < 0 or days_between(keys[j], d) > MAX_STALE_DAYS:
        return None
    return series_by_date[keys[j]]


def today_values(recent_n=15):
    """Fetches every scored input for the latest trading day, plus the same
    inputs for the preceding `recent_n` days.

    Nothing here is sampled: the below-book-value rate and the limit-up /
    limit-down / streak readings are whole-market aggregates read directly
    (market_stats.py), the rest are index-, margin-, bond- and options-level
    series. No per-stock bars are fetched -- the ~900-stock scrape this used
    to run each morning was the source of every rate-limit failure, and the
    one dimension that genuinely needed it (market breadth) was removed from
    the model (see indicators.py).

    Returns (date, today's values, today's index-panel row,
    (n_below_book, n_listed), recent_panel) -- recent_panel is
    [{date, close, ...V2 inputs}, ...], oldest first, which check_signal.py
    uses to rebuild the recent ASI series without depending on when
    asi_history.csv was last committed. Limit-pool readings only exist for
    the ~20 trading days East Money still serves; older days in the window
    come back None and are renormalized away.
    """
    idx = fetch.index_daily()
    ip_all = series.index_panel(idx)
    ip = ip_all[-1]
    d = ip["date"]
    recent = ip_all[-recent_n:]
    recent_dates = [r["date"] for r in recent]

    counts = market_stats.below_book_counts()
    count_keys = sorted(counts)
    below_book = market_stats.below_book_series(counts=counts)
    listed = {x: c[1] for x, c in ((x, _latest_on_or_before(counts, count_keys, x))
                                   for x in recent_dates) if c}
    # East Money serves only ~20 trading days of limit pools; don't ask for more.
    pools = market_stats.limit_pools(recent_dates[-POOL_DAYS:], listed)

    margin_by_date = margin.chg5_series(recent_dates)
    margin_buy_by_date = margin.buy_ratio_series(recent_dates)
    erp_by_date = erp_mod.daily_series(recent_dates)
    iv_by_date = iv_mod.daily_series(recent_dates)

    recent_panel = []
    for rec in recent:
        x = rec["date"]
        pool = pools.get(x, {})
        recent_panel.append({
            "date": x,
            "close": rec["close"],
            "broken_net_rate": _latest_on_or_before(below_book, count_keys, x),
            "erp": erp_by_date.get(x),
            "vol_temp": rec.get("vol_temp"),
            "iv_temp": iv_by_date.get(x),
            "rsi14": rec.get("rsi14"),
            "bias60": rec.get("bias60"),
            "drawdown": rec.get("drawdown"),
            "margin_chg5": margin_by_date.get(x),
            "margin_buy_ratio": margin_buy_by_date.get(x),
            "limit_up_rate": pool.get("limit_up_rate"),
            "limit_down_rate": pool.get("limit_down_rate"),
            "max_consec_limit": pool.get("max_consec_limit"),
        })

    today = dict(recent_panel[-1])
    today.pop("date")
    today.pop("close")
    bb_today = _latest_on_or_before(counts, count_keys, d) or (0, 0)
    return d, today, ip, bb_today, recent_panel


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
    # Column order must match history.COLS exactly -- kept as an explicit
    # list (rather than a dict-driven writer) so a mismatch is visible on
    # sight; see tests/test_compute.py for a length/order check against
    # history.COLS.
    with open(history.OUT, "a", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow([
            date, round(ip["close"], 2), _r(vals.get("broken_net_rate"), 3), "",
            _r(vals.get("erp"), 3), "", "", "",
            _r(ip.get("vol_ratio"), 3), _r(ip.get("ret5")),
            _r(vals["vol_temp"]), _r(vals.get("iv_temp")),
            _r(vals["rsi14"]), _r(vals["bias60"]), _r(vals["drawdown"]),
            _r(vals.get("margin_chg5"), 3), _r(vals.get("margin_buy_ratio"), 3),
            _r(vals.get("limit_up_rate"), 3), _r(vals.get("limit_down_rate"), 3),
            vals.get("max_consec_limit") if vals.get("max_consec_limit") is not None else "",
            "", res["score"], res["coverage"], res["zone"], "", "", "", ""])
    print("Appended a row to asi_history.csv")


def _r(x, n=2):
    return "" if x is None else round(x, n)


if __name__ == "__main__":
    main()
