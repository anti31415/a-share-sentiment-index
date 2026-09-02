# -*- coding: utf-8 -*-
"""
Survivorship-bias / ST-exclusion robustness check (a side issue from code
review section 3.3, plus a user follow-up request on 2026-09-02).

The sampling pool is drawn from stocks [currently] listed; delisted stocks
aren't in the pool, so the early-years below-book-value rate may be
understated -- this can't be fully corrected (there's no year-by-year
historical constituent list). What can be done: exclude stocks whose
[current name] carries ST/*ST, resample, and compare against the default
(ST-included) sample over just the last 5 years (from 2021-09-02).
"""
import os
import sys

import fetch
import series
from compute import compute, InsufficientData

HERE = os.path.dirname(os.path.abspath(__file__))
START = "2021-09-02"


def build(n_sample, exclude_st, seed=20260902):
    sample = fetch.sample_universe(n_sample, seed=seed, exclude_st=exclude_st)
    fetch.bulk(lambda x: fetch.stock_daily(fetch.sina_symbol(x[0], x[1])),
               sample, workers=10, label="kline")
    fetch.bulk(lambda x: fetch.stock_bps(x[0], x[1]), sample, workers=10, label="bps")
    idx = [r for r in fetch.index_daily() if r[0] >= "2004-01-01"]
    ip = series.index_panel(idx)
    dates_all = [r["date"] for r in ip]
    dates_recent = [d for d in dates_all if d >= "2019-01-01"]   # extra runway for the rolling calcs
    cs = series.cross_section(sample, dates_recent)
    br_raw = [cs.get(d, {}).get("breadth_raw") for d in dates_recent]
    br = series.smooth(br_raw, 5)
    br_map = dict(zip(dates_recent, br))
    out = []
    for rec in ip:
        d = rec["date"]
        if d < START or d not in cs:
            continue
        rec = dict(rec)
        rec["breadth"] = br_map.get(d)
        rec["new_low"] = cs[d]["new_low"]
        rec["broken_net_rate"] = cs[d]["broken_net_rate"]
        rec["n_sample"] = cs[d]["n"]
        out.append(rec)
    return out, len(sample)


def score(panel):
    rows = []
    for rec in panel:
        vals = {k: rec.get(k) for k in
                ("broken_net_rate", "breadth", "new_low", "vol_temp",
                 "rsi14", "bias60", "drawdown")}
        try:
            r = compute(vals, require_valuation=False)
        except InsufficientData:
            r = None
        rows.append({"date": rec["date"], "close": rec["close"],
                     "asi": r["score"] if r else None,
                     "broken_net_rate": rec.get("broken_net_rate"),
                     "n_sample": rec.get("n_sample")})
    return rows


def attach_fwd(rows):
    c = [r["close"] for r in rows]
    n = len(rows)
    for i, r in enumerate(rows):
        r["fwd120"] = (c[i + 120] / c[i] - 1) * 100 if i + 120 < n else None
    return rows


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    print("Building the ST-included sample (n=%d) ..." % n, flush=True)
    p_all, n_all = build(n, exclude_st=False)
    print("Building the ST-excluded sample ...", flush=True)
    p_clean, n_clean = build(n, exclude_st=True)

    r_all = attach_fwd(score(p_all))
    r_clean = attach_fwd(score(p_clean))

    print("\n" + "=" * 90)
    print("  ST-exclusion robustness check : %s -> %s" % (START, r_all[-1]["date"]))
    print("=" * 90)
    print("  ST-included pool: %d stocks (same seed, unfiltered)" % n_all)
    print("  ST-excluded pool: %d stocks (same seed; filtering shrinks the pool, "
          "so the specific stocks drawn differ)" % n_clean)

    def summarize(rows, label):
        ok = [r for r in rows if r["asi"] is not None]
        bn = [r["broken_net_rate"] for r in ok if r["broken_net_rate"] is not None]
        low = [r for r in ok if r["asi"] < 30]
        print("\n  [%s]" % label)
        print("    %d valid days; below-book-value rate mean %.2f%% (min %.2f%% / max %.2f%%)"
              % (len(ok), sum(bn) / len(bn), min(bn), max(bn)))
        print("    ASI<30 (pessimistic or colder): %d days (%.1f%%)"
              % (len(low), len(low) / len(ok) * 100))
        if low:
            f = [r["fwd120"] for r in low if r["fwd120"] is not None]
            if f:
                print("    that bucket's mean forward-120-day return: %.2f%%" % (sum(f) / len(f)))
        return ok

    a = summarize(r_all, "ST-included (default)")
    b = summarize(r_clean, "ST-excluded")

    print("\n  Day-by-day comparison (ASI difference between the two samples on the same day)")
    ma = {r["date"]: r["asi"] for r in a if r["asi"] is not None}
    mb = {r["date"]: r["asi"] for r in b if r["asi"] is not None}
    common = sorted(set(ma) & set(mb))
    diffs = [mb[d] - ma[d] for d in common]
    print("    %d common trading days; ASI diff (ST-excluded - ST-included) mean %+.2f, "
          "std dev %.2f, max absolute diff %.2f"
          % (len(diffs), sum(diffs) / len(diffs),
             (sum((x - sum(diffs) / len(diffs)) ** 2 for x in diffs) / len(diffs)) ** 0.5,
             max(abs(x) for x in diffs)))
    print("=" * 90)


if __name__ == "__main__":
    main()
