# -*- coding: utf-8 -*-
"""
================================================================================
 A-Share Sentiment Index (ASI) -- v1, the ORIGINAL version kept for comparison
--------------------------------------------------------------------------------
 0   = extreme panic, blood in the streets
 100 = extreme greed, the fat tail of the rally

 Design goal: answer "is now the time to be greedy" with one 0-100 number.
 Design principles:
   1. Use only indicators that are stable and extreme at historical bottoms;
      avoid indicators that just track the current move.
   2. Anchor with absolute thresholds (matched to real historical panic
      readings), not rolling percentiles -- rolling percentiles keep
      resetting in a one-way decline and lose their meaning.
   3. Every indicator's direction is explicit: positive (higher = hotter) or
      negative (higher = colder).
   4. Built-in "asymmetric confirmation" mechanism to address the panic
      threshold's "unstable bottom-calling" problem.
================================================================================

NOTE (2026-09): this is the legacy v1 model, kept ONLY so the project's v2
history file can score both models against the same inputs for comparison.
It carries three known bugs (see the v2 code review in README.md) that are
deliberately left in place here, unfixed, so the comparison is meaningful.
Do not use this module for anything but that comparison -- use asi/compute.py
(v2) for real scoring.
"""

import argparse
import json
import sys
from datetime import datetime

# ==============================================================================
# 1. Indicator definitions
# ==============================================================================
# direction : "pos" = higher indicator value -> hotter sentiment;
#             "neg" = higher indicator value -> colder sentiment
#             (semantic label only, not used in the calculation)
# anchors   : [(raw value, final score), ...], must be sorted ascending by raw
#             value, linearly interpolated.
#             Key point: the scores in `anchors` are ALREADY the final
#             sentiment score (0 = coldest, 100 = hottest) with direction
#             baked in -- never flipped again during scoring. This keeps the
#             anchors readable, checkable, and immune to a hot/cold sign flip.

INDICATORS = [
    {
        "key": "broken_net_rate",
        "name": "Below-book-value rate",
        "unit": "%",
        "weight": 25,
        "direction": "neg",
        "desc": "Share of all A-shares trading below price-to-book 1.0. The "
                "single strongest feature of A-share historical major bottoms.",
        "anchors": [
            (0.5, 95), (2.0, 75), (4.0, 55), (7.0, 38),
            (10.0, 22), (13.0, 10), (16.0, 0),
        ],
        "why": "16.5% at the 998 low / 14.8% at 1664 / 15.6% at 2635 / 15.8% "
               "in Sept 2024 (a 20-year high); but only 2.5% at the 2638 low "
               "in 2016 -- it's the best ruler for telling a genuine major "
               "bottom apart from an ordinary correction.",
        "source": "tool_filter(preset=LowPB, max_pb=1).totalStocks / total A-share count",
    },
    {
        "key": "breadth",
        "name": "Market breadth",
        "unit": "%",
        "weight": 20,
        "direction": "pos",
        "desc": "Market-wide share of advancing stocks. Reflects whether a "
                "move is broad-based or structural.",
        "anchors": [
            (20, 0), (30, 15), (40, 32), (50, 50),
            (62, 70), (75, 86), (88, 100),
        ],
        "why": "A panic bottom's signature is indiscriminate selling -- "
               "advancers share falls below 20%; a structural correction "
               "usually still has 30-40% of names rising, meaning money "
               "hasn't fully left.",
        "source": "data_sector(mode=ranking, kind=industry), aggregated upCount numerator/denominator by industry",
    },
    {
        "key": "volume_ratio",
        "name": "Volume temperature",
        "unit": "x",
        "weight": 15,
        "direction": "pos",
        "desc": "Today's turnover / trailing 60-day average turnover. Rock-bottom volume, rock-bottom prices.",
        "anchors": [
            (0.50, 0), (0.70, 18), (0.90, 38), (1.10, 55),
            (1.50, 75), (2.00, 88), (3.00, 100),
        ],
        "why": "The average volume contraction across 7 historical major "
               "bottoms was -52% (roughly 0.48x); turnover-to-market-cap "
               "breaking 1.5% is a right-side confirmation signal at a major bottom.",
        "source": "data_kline(period=day, limit=70), computed in-house",
    },
    {
        "key": "rsi14",
        "name": "RSI(14)",
        "unit": "",
        "weight": 15,
        "direction": "pos",
        "desc": "14-day relative strength index. The classic overbought/oversold reading.",
        "anchors": [
            (20, 0), (30, 14), (40, 34), (50, 50),
            (60, 66), (70, 82), (80, 95), (90, 100),
        ],
        "why": "RSI<30 is oversold. But in an A-share one-way decline RSI "
               "can go numb in the 20-30 range for weeks, so it has to be "
               "paired with a valuation-despair indicator like the "
               "below-book-value rate.",
        "source": "data_kline, computed in-house (Wilder smoothing)",
    },
    {
        "key": "bias60",
        "name": "BIAS(60)",
        "unit": "%",
        "weight": 15,
        "direction": "pos",
        "desc": "(close - MA60) / MA60 x 100. Measures how far price has drifted from its medium-term average.",
        "anchors": [
            (-20, 0), (-12, 15), (-6, 32), (0, 50),
            (6, 68), (12, 82), (20, 95), (30, 100),
        ],
        "why": "A direct source of oversold-rebound signals. BIAS60 has "
               "reached -15% to -20% during severe Shanghai Composite "
               "sell-offs historically; Huatai's A-share sentiment index "
               "also lists it as a primary market-momentum indicator.",
        "source": "data_kline, computed in-house",
    },
    {
        "key": "drawdown",
        "name": "Drawdown depth",
        "unit": "%",
        "weight": 10,
        "direction": "neg",
        "desc": "Drawdown from the trailing 252-day high (negative number).",
        "anchors": [
            (-65, 0), (-50, 8), (-40, 18), (-30, 32),
            (-20, 50), (-10, 65), (0, 85),
        ],
        "why": "Average drawdown across historical major bottoms is roughly "
               "60% (-72.8% in 2008, -56% in 2005, -53% in 2018). Given the "
               "lowest weight because it's a 'result,' not 'sentiment' -- it "
               "mainly serves as a sanity check.",
        "source": "data_kline, computed in-house (252-day rolling high)",
    },
]

# Zone boundaries (aligned to Huatai's A-share sentiment index's five-tier framework)
ZONES = [
    (0, 15, "Ice-cold", "Blood in the streets", "Historic-bottom territory. Panic is already fully priced in; "
     "the question is 'when does it confirm,' not 'could it get cheaper.'"),
    (15, 30, "Pessimistic", "Watch from the left side", "Cold but not yet extreme. Can start scaling in, but keep reserves for later."),
    (30, 70, "Neutral", "Not extreme", "Normal sentiment noise. The index has no timing value in this range -- go back to fundamentals and single stocks."),
    (70, 85, "Optimistic", "Hold, mostly", "Hot. A-shares run in one-way trends, so this is often mid/late in the main "
     "up-leg rather than the end of it."),
    (85, 100, "Greed", "Fat-tail risk", "Peak sentiment. A-shares lack an easy way to short, so above 90 can persist "
     "for weeks -- don't rush to get out."),
]

W_TOTAL = sum(i["weight"] for i in INDICATORS)


# ==============================================================================
# 2. Core algorithm
# ==============================================================================
def interpolate(value, anchors):
    """Piecewise-linear interpolation: maps a raw value to a 0-100 sentiment score. Clamped at both ends."""
    if value <= anchors[0][0]:
        return float(anchors[0][1])
    if value >= anchors[-1][0]:
        return float(anchors[-1][1])
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return float(y0)
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return 50.0


def score_indicator(ind, raw):
    """Scores a single indicator. Direction is already baked into the
    anchors (the score is already the final sentiment score) -- just interpolate."""
    return interpolate(raw, ind["anchors"])


def compute(values):
    """Takes a dict of raw values, returns the full result.

    KNOWN BUG (kept intentionally for the v1/v2 comparison -- see README.md):
    `contrib = s * weight / W_TOTAL`, but W_TOTAL is a CONSTANT (always 100).
    A missing indicator simply isn't added in -- the denominator never
    shrinks to match -- which is mathematically equivalent to scoring it 0
    (extreme panic). v2 fixes this by normalizing over the effective weight
    of only the indicators actually present.
    """
    rows, total = [], 0.0
    for ind in INDICATORS:
        raw = values.get(ind["key"])
        if raw is None:
            rows.append({"key": ind["key"], "name": ind["name"], "missing": True})
            continue
        s = score_indicator(ind, float(raw))
        contrib = s * ind["weight"] / W_TOTAL
        total += contrib
        rows.append({
            "key": ind["key"], "name": ind["name"], "unit": ind["unit"],
            "raw": round(float(raw), 2),
            "weight": ind["weight"],
            "direction": "positive (higher = hotter)" if ind["direction"] == "pos" else "negative (higher = colder)",
            "score": round(s, 1),
            "contrib": round(contrib, 1),
            "missing": False,
        })

    score = round(total, 1)
    zone = next((z for lo, hi, z, tag, _ in ZONES if lo <= score < hi), ZONES[-1][2])

    # Asymmetric confirmation mechanism: addresses "unstable bottom-calling" at the panic threshold
    prev = values.get("_prev_score")
    confirm = None
    if prev is not None:
        if score < 15:
            confirm = ("Panic has fired, but is [NOT confirmed]. Historically, "
                       "A-share margin-financing / snowball / DMA leverage "
                       "feedback loops mean sentiment often keeps grinding "
                       "lower for 1-3 months after breaking below 15. Wait "
                       "for the index to [retake 15] before confirming.")
        elif prev < 15 <= score:
            confirm = ("[CONFIRMED] The index has retaken 15 from below the "
                       "ice-cold zone -- selling pressure appears to be "
                       "exhausting. This is a steadier left-side entry than "
                       "'buy the moment it breaks below 15.'")
        elif prev >= 85 > score:
            confirm = ("Sentiment is pulling back from the greed zone. "
                       "A-share one-way trends commonly show a 'top-"
                       "divergence' numbing pattern -- the first break below "
                       "85 doesn't call for exiting everything; consider "
                       "trimming once it breaks below 70.")
        else:
            confirm = "No special signal, treat as a routine reading."

    return {"score": score, "zone": zone, "rows": rows, "confirm": confirm}


# ==============================================================================
# 3. Market-derived indicators (for use with daily-bar data)
# ==============================================================================
def rsi_wilder(closes, n=14):
    """Wilder-smoothed RSI. closes in chronological order (old -> new).

    KNOWN BUG (kept intentionally -- see README.md): when al == 0 this
    always returns 100, even when ag is also 0 (a halt, flat trading, or
    duplicated data) -- the correct answer there is 50, not 100. v2 fixes
    this in asi/series.py.
    """
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def from_kline(closes_asc, amounts_asc, lookback_dd=252):
    """
    closes_asc / amounts_asc : closing prices and turnover, chronological order (old -> new)
    Returns a dict of derived indicators ready to feed into compute().

    KNOWN BUG (kept intentionally -- see README.md): `a[-61:-1]` is meant to
    be "the 60 days before today," but when len(a) == 60 that slice only
    has 59 elements while still dividing by 60.0 -- the average volume is
    systematically understated by about 1.7%. v2 fixes this in asi/series.py.
    """
    c = list(closes_asc)
    a = list(amounts_asc)
    out = {}
    if len(c) >= 61:
        ma60 = sum(c[-60:]) / 60.0
        out["bias60"] = (c[-1] - ma60) / ma60 * 100.0
    if len(a) >= 60:
        ma_amt = sum(a[-61:-1]) / 60.0   # intended: the 60-day average excluding today
        if ma_amt > 0:
            out["volume_ratio"] = a[-1] / ma_amt
    out["rsi14"] = rsi_wilder(c)
    win = c[-lookback_dd:] if len(c) >= lookback_dd else c
    hi = max(win)
    if hi > 0:
        out["drawdown"] = (c[-1] / hi - 1.0) * 100.0
    return out


# ==============================================================================
# 4. Output
# ==============================================================================
def render(res, date_str=None, show_detail=True):
    L = []
    L.append("=" * 74)
    L.append("  A-Share Sentiment Index (ASI) -- v1 legacy")
    L.append("=" * 74)
    if date_str:
        L.append("  Data date: %s" % date_str)

    z = next(z for z in ZONES if z[2] == res["zone"])
    L.append("")
    L.append("   [ Sentiment score ]  %.1f  /  100" % res["score"])
    L.append("   [ Zone ]  %s  -- %s" % (res["zone"], z[3]))
    L.append("   [ What it means ]  %s" % z[4])

    if show_detail:
        L.append("")
        L.append("   " + "-" * 70)
        L.append("   %-24s %10s %6s %8s %8s %s"
                 % ("Indicator", "Raw", "Weight", "Score", "Contrib", "Direction"))
        L.append("   " + "-" * 70)
        for r in res["rows"]:
            if r.get("missing"):
                L.append("   %-24s %10s" % (r["name"], "-- missing --"))
                continue
            L.append("   %-24s %9.2f%s %5d%% %8.1f %8.1f  %s"
                     % (r["name"], r["raw"], r["unit"], r["weight"],
                        r["score"], r["contrib"], r["direction"].split(" (")[0]))
        L.append("   " + "-" * 70)

    if res["confirm"]:
        L.append("")
        L.append("   [ Confirmation ]")
        for line in _wrap(res["confirm"], 62):
            L.append("     " + line)

    L.append("")
    L.append("=" * 74)
    return "\n".join(L)


def _wrap(text, width):
    out, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(description="A-Share Sentiment Index (ASI) v1 legacy")
    ap.add_argument("--json", help="input JSON file (raw indicator values)")
    ap.add_argument("--kline", help="input daily-bar JSON file {closes:[], amounts:[]} (chronological order)")
    ap.add_argument("--date", default=None, help="data date")
    ap.add_argument("--prev", type=float, default=None, help="previous day's sentiment score (for the confirmation mechanism)")
    args = ap.parse_args()

    values = {}
    # load the kline-derived values first, then manual values -- manual
    # values take priority (can override the 252-day drawdown, etc.)
    if args.kline:
        k = json.load(open(args.kline, encoding="utf-8"))
        values.update(from_kline(k["closes"], k["amounts"]))
    if args.json:
        values.update(json.load(open(args.json, encoding="utf-8")))
    if args.prev is not None:
        values["_prev_score"] = args.prev

    if not values:
        ap.print_help()
        sys.exit(1)

    print(render(compute(values), args.date or datetime.now().strftime("%Y-%m-%d")))


if __name__ == "__main__":
    main()
