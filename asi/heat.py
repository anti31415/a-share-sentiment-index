# -*- coding: utf-8 -*-
"""
Short-term overheating ("heat") -- the sell-side companion to the ASI.

Every market top of the last ten years (60-day high followed by a >=10% fall
within 120 days: 2018-01, 2019-04, 2020-01, 2021-09, 2022-06, 2023-05,
2024-05, 2026-05) was a burst of short-term overheating, not a valuation
extreme: RSI(14) sat at or above its 87th 3-year percentile at all eight,
BIAS(60) at or above the 79th, the financing buy ratio at or above the 66th,
and index volume at or above the 77th at seven of eight. The ASI, being
valuation-led, read only 27-43 at three of them (2019-04, 2020-01, 2024-05).

heat(date) = mean of the four inputs' point-in-time percentiles against their
own trailing ~3 years (750 trading days, at least 250), each ranked only
against days strictly before it -- no lookahead. Inputs, all available daily
without per-stock data:

  rsi14, bias60       Shanghai Composite, series.index_panel()
  index_vol_20d       20-day mean of the index's daily volume
  margin_buy_ratio    financing buy amount / prior financing balance (margin.py)
"""
import bisect

import fetch
import margin
import series

PCT_WINDOW = 750
PCT_MIN = 250
VOL_N = 20
KEYS = ("rsi14", "bias60", "index_vol_20d", "margin_buy_ratio")


def rolling_pct(vals):
    """Percentile of each value among the up-to-PCT_WINDOW non-missing values
    strictly before it; None until PCT_MIN of them exist."""
    out, ordered, fifo = [], [], []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        out.append(bisect.bisect_left(ordered, v) / len(ordered) if len(ordered) >= PCT_MIN else None)
        bisect.insort(ordered, v)
        fifo.append(v)
        if len(fifo) > PCT_WINDOW:
            old = fifo.pop(0)
            del ordered[bisect.bisect_left(ordered, old)]
    return out


def trailing_mean(vals, n):
    out, s = [], 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def from_inputs(dates, inputs):
    """{date: heat in [0, 1] or None}. `inputs` maps each of KEYS to a list
    aligned with `dates`."""
    pcts = [rolling_pct(inputs[k]) for k in KEYS]
    out = {}
    for i, d in enumerate(dates):
        vals = [p[i] for p in pcts if p[i] is not None]
        out[d] = sum(vals) / len(vals) if vals else None
    return out


def series_by_date(idx=None):
    """{date: heat} for every trading day of the Shanghai Composite."""
    idx = idx if idx is not None else fetch.index_daily()
    panel = series.index_panel(idx)
    dates = [r["date"] for r in panel]
    buy = margin.buy_ratio_series(dates)
    inputs = {
        "rsi14": [r.get("rsi14") for r in panel],
        "bias60": [r.get("bias60") for r in panel],
        "index_vol_20d": trailing_mean([float(v) for _d, _c, v in idx], VOL_N),
        "margin_buy_ratio": [buy.get(d) for d in dates],
    }
    return from_inputs(dates, inputs)
