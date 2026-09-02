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


def today_values(n_sample=900):
    """Fetches every indicator's raw value for the latest trading day. The
    below-book-value rate is [measured across the full market] -- no
    sampling, no hardcoded common-sense constant."""
    idx = fetch.index_daily()
    ip_all = series.index_panel(idx)
    ip = ip_all[-1]

    u = fetch.universe()
    n_all = len(u)
    n_bn = sum(1 for r in u if r[3] is not None and (r[3] <= 0 or r[3] < 1.0))
    bn = n_bn / n_all * 100.0

    # Breadth / new-low still need a cross-section, reconstructed from a sample
    sample = fetch.sample_universe(n_sample)
    recent_dates = [r["date"] for r in ip_all[-15:]]   # small recent window, cheap to recompute daily
    cs = series.cross_section(sample, recent_dates)
    d = ip["date"]
    br = [cs[x]["breadth_raw"] for x in sorted(cs) if cs[x]["breadth_raw"] is not None][-5:]

    # Leverage sentiment (margin balance 5-day change) and speculation
    # extremity (limit-up/down reconstruction) -- added 2026-09, must be
    # included here too or the live daily signal silently falls back to the
    # original 4-dimension model instead of the full 6-dimension one used in
    # the backtest.
    margin_by_date = margin.chg5_series(recent_dates)
    kline_by_code = {}
    for code, mkt, name, _pb in sample:
        k = fetch.stock_daily(fetch.sina_symbol(code, mkt))
        if k:
            kline_by_code[code] = k
    lb = limitboard.cross_section(sample, recent_dates, kline_by_code)
    erp_today = erp_mod.daily_series(recent_dates).get(d)

    return d, {
        "broken_net_rate": round(bn, 2),
        "erp": erp_today,
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
    }, ip, (n_bn, n_all)


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
    date, vals, ip, bn = today_values()
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
