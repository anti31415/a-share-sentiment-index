# -*- coding: utf-8 -*-
"""
ERP (equity risk premium) -- code review section 6.4: the single item flagged
as the easiest, highest-value addition in the whole review.

    ERP = 1/PE_TTM(CSI300, cap-weighted) x 100  -  10-year China government bond yield

Data sources (all free public endpoints):
  * 10-year government bond yield: China Central Depository & Clearing (the
    same underlying feed akshare's `bond_china_yield` forwards), fetched in
    yearly chunks, daily, from 2021-09-01.
  * Index PE: legulegu's index-basic-pe endpoint, using the [cap-weighted TTM
    PE (addTtmPe)] rather than the plain arithmetic mean (that column gets
    inflated by high-PE small caps -- on 2021-02-26 the plain average gives
    44.66 while the weighted figure gives 15.67, and the latter is the
    market-recognized CSI300 TTM PE level), monthly, from 2005-04.

Two limitations that must be stated up front:
  1. Uses [CSI300] in place of [the Shanghai Composite]. The Shanghai
     Composite's PE is rarely published directly in the industry, because
     the large number of loss-making/thin-margin SOEs among its constituents
     distorts a simple aggregate; CSI300 is the most common substitute (the
     equity-risk-premium notes from brokerages such as Shenwan Hongyuan and
     Ping An Securities use the same convention).
  2. The government-bond-yield history only reaches back 5 years (from
     2021-09) -- repeated attempts at an earlier CCDC history-query endpoint
     never returned reliably (it would randomly come back with an empty
     array, suggesting it may require an interactive browser session). So
     ERP can only serve as a [recent-5-year] auxiliary indicator, and its
     anchors are percentiles within that 5-year sample, not 21-year absolute
     anchors the way the below-book-value rate has -- a reading "near the
     in-sample mean" cannot be read against historical bottoms/tops the way
     the other indicators can.
"""
import bisect
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
Y10_CSV = os.path.join(HERE, "data", "cn10y_5y.csv")
PE_CSV = os.path.join(HERE, "data", "hs300_pe_5y.csv")

START = "2021-09-01"

# In-sample percentile anchors over the 5-year window (provisional -- see
# limitation 2 above). Higher ERP = stocks cheaper relative to bonds =
# sentiment should read colder (more of a reason to be greedy buying), so
# like the below-book-value rate this is direction="neg": a larger raw value
# maps to a smaller score.
ANCHORS = [(4.6, 90), (5.3, 75), (5.7, 60), (6.2, 50),
           (6.6, 35), (6.9, 20), (7.8, 5)]


def _load_csv(path, key, val):
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r[val]:
                out[r[key]] = float(r[val])
    return out


def _ffill(date, table, sorted_keys):
    j = bisect.bisect_right(sorted_keys, date) - 1
    return table[sorted_keys[j]] if j >= 0 else None


def daily_series(dates):
    """Given a list of trading days, returns {date: erp} -- PE is forward-
    filled from its monthly cadence, the bond yield keeps its daily raw value."""
    if not (os.path.exists(Y10_CSV) and os.path.exists(PE_CSV)):
        return {}
    y10 = _load_csv(Y10_CSV, "date", "y10")
    pe = _load_csv(PE_CSV, "date", "pe_ttm")
    y10_keys, pe_keys = sorted(y10), sorted(pe)
    out = {}
    for d in dates:
        if d < START:
            continue
        p = _ffill(d, pe, pe_keys)
        y = y10.get(d) or _ffill(d, y10, y10_keys)
        if p and y is not None and p > 0:
            out[d] = round(100.0 / p - y, 3)
    return out
