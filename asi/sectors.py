# -*- coding: utf-8 -*-
"""
Per-sector ASI (code review section 6.3) -- the review's own conclusion was
that the 2026-08-28 move was "a structural rotation out of high-flying
sectors," and a single market-wide score is exactly the kind of thing that
smooths that divergence away. This builds 5 lines per the user's request:
Shanghai Composite / Shenzhen Component / ChiNext / STAR 50 / CSI 2000.

Warning: sector membership is approximated by [code prefix], not an official
constituent list (there's no free source for a daily historical constituent
list):
    Shanghai Composite: 600/601/603/605/688 (includes STAR board; the
                         official index composition does too)
    Shenzhen Component: 000/001/002/003 (Shenzhen main + SME board, an
                         approximation of "Shenzhen ex-ChiNext")
    ChiNext:             300/301
    STAR 50:             688
    CSI 2000:            can't be approximated by code prefix -- it's
                         "the smallest-cap tranche left after removing
                         CSI300 / CSI500 / CSI1000," and there's no market-cap
                         data to run that filter. This line only uses the
                         [real index price] to drive the price-momentum and
                         volume-price-temperature dimensions; the valuation
                         and breadth dimensions are left empty (coverage will
                         honestly read low rather than faking data).

Only runs the most recent 5 years (from 2021-09), because: (1) runtime;
(2) the user explicitly said "the last 5 years is enough"; (3) the code-prefix
sector approximation is itself biased, and a longer sample period would
amplify that bias.
"""
import os
import sys

import fetch
import series
from compute import compute, InsufficientData

HERE = os.path.dirname(os.path.abspath(__file__))
START = "2021-09-02"
N_SAMPLE = 300      # stocks sampled per sector, balancing accuracy against runtime

SECTORS = [
    {"key": "sh", "name": "Shanghai Composite", "index_sym": "sh000001",
     "prefix": ["600", "601", "603", "605", "688"]},
    {"key": "sz", "name": "Shenzhen Component", "index_sym": "sz399001",
     "prefix": ["000", "001", "002", "003"]},
    {"key": "cyb", "name": "ChiNext", "index_sym": "sz399006",
     "prefix": ["300", "301"]},
    {"key": "kc50", "name": "STAR 50", "index_sym": "sh000688",
     "prefix": ["688"]},
    {"key": "zz2000", "name": "CSI 2000", "index_sym": "sz399303",
     "prefix": None},          # can't be approximated -- see module note above
]


def build_one(sec, n_sample=N_SAMPLE, seed=20260902):
    if sec["prefix"]:
        sample = fetch.sample_universe(n_sample, seed=seed, code_prefix=sec["prefix"])
    else:
        sample = []
    if sample:
        # Important: pre-warm the disk cache with a thread pool first --
        # series.cross_section reads sequentially in a single thread, so
        # without pre-warming, every not-yet-cached stock waits on its own
        # network round trip one at a time.
        fetch.bulk(lambda x: fetch.stock_daily(fetch.sina_symbol(x[0], x[1])),
                   sample, workers=10, label="%s/kline" % sec["key"])
        fetch.bulk(lambda x: fetch.stock_bps(x[0], x[1]),
                   sample, workers=10, label="%s/bps" % sec["key"])
    panel = series.build_panel(
        n_sample=0, start=START, index_sym=sec["index_sym"],
        sample=sample, min_cross_start="2019-01-01")
    return panel


def score_panel(panel):
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
                     "coverage": r["coverage"] if r else None,
                     "zone": r["zone"] if r else None})
    return rows


def build_all(n_sample=N_SAMPLE):
    out = {}
    for sec in SECTORS:
        print("[sector] %s (%s) ..." % (sec["name"], sec["index_sym"]), flush=True)
        panel = build_one(sec, n_sample)
        rows = score_panel(panel)
        out[sec["key"]] = rows
        print("      %d days, latest %s ASI=%s coverage=%s"
              % (len(rows), rows[-1]["date"] if rows else "-",
                 rows[-1]["asi"] if rows else "-",
                 rows[-1]["coverage"] if rows else "-"), flush=True)
    return out


def save(out):
    import csv
    for key, rows in out.items():
        p = os.path.join(HERE, "data", "sector_%s.csv" % key)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close", "asi", "coverage", "zone"])
            for r in rows:
                w.writerow([r["date"], round(r["close"], 2),
                            r["asi"] if r["asi"] is not None else "",
                            round(r["coverage"], 3) if r["coverage"] is not None else "",
                            r["zone"] or ""])


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_SAMPLE
    out = build_all(n)
    save(out)
    print("Wrote data/sector_*.csv")
