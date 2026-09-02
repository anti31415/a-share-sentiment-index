# -*- coding: utf-8 -*-
"""
Indicator and dimension definitions (pure configuration — no data, no fetch logic).

Structural changes relative to v1 (per the code review, sections 3.1 / 3.2):
  * Six flat indicators -> four dimensions, composited within a dimension first,
    then weighted across dimensions.
    In v1, RSI / BIAS60 / drawdown were pairwise correlated 0.97-1.00 with a
    nominal 40% combined weight -- effectively the single signal "how much has
    price fallen" got three votes. They're now merged into one [Price momentum]
    dimension worth 25%.
  * Volume-temperature switched from a monotonic mapping to a [volume-price
    pairing]: a heavy-volume decline is panic, not euphoria.
  * Market breadth gained a "% of stocks at a new 52-week low" member,
    alongside the advance/decline ratio, forming the breadth dimension.
"""

# anchors: [(raw value, sentiment score)], must be strictly increasing by raw
# value; the score already encodes direction.
INDICATORS = {
    "broken_net_rate": {
        "name": "Below-book-value rate", "unit": "%", "direction": "neg",
        "anchors": [(0.5, 95), (2.0, 75), (4.0, 55), (7.0, 38),
                    (10.0, 22), (13.0, 10), (16.0, 0)],
        "desc": "Share of all A-shares trading below price-to-book 1.0. Main "
                "component of the valuation-despair dimension (21-year absolute anchors).",
    },
    "erp": {
        "name": "Equity risk premium", "unit": "%", "direction": "neg",
        "anchors": [(4.6, 90), (5.3, 75), (5.7, 60), (6.2, 50),
                    (6.6, 35), (6.9, 20), (7.8, 5)],
        "desc": "1/PE (CSI300, weighted TTM) minus the 10-year government bond "
                "yield. Added per review section 6.4. Warning: only has data "
                "from 2021-09 onward; anchors are percentiles within that "
                "5-year sample, not absolute historical anchors -- see the "
                "note at the top of erp.py.",
    },
    "vol_temp": {
        "name": "Volume-price temperature", "unit": "pts", "direction": "pos",
        "anchors": None,                      # scored directly by vol_temp_score()
        "desc": "2-D paired reading of volume multiple x price direction "
                "(see vol_temp_score).",
    },
    "rsi14": {
        "name": "RSI(14)", "unit": "", "direction": "pos",
        "anchors": [(20, 0), (30, 14), (40, 34), (50, 50),
                    (60, 66), (70, 82), (80, 95), (90, 100)],
        "desc": "14-day relative strength index, Wilder-smoothed.",
    },
    "bias60": {
        "name": "BIAS(60)", "unit": "%", "direction": "pos",
        "anchors": [(-20, 0), (-12, 15), (-6, 32), (0, 50),
                    (6, 68), (12, 82), (20, 95), (30, 100)],
        "desc": "(close - MA60) / MA60.",
    },
    "drawdown": {
        "name": "Drawdown depth", "unit": "%", "direction": "neg",
        "anchors": [(-65, 0), (-50, 8), (-40, 18), (-30, 32),
                    (-20, 50), (-10, 65), (0, 85)],
        "desc": "Drawdown from the trailing 252-day high.",
    },
    "breadth": {
        "name": "Advancers share", "unit": "%", "direction": "pos",
        "anchors": [(20, 0), (30, 15), (40, 32), (50, 50),
                    (62, 70), (75, 86), (88, 100)],
        "desc": "Market-wide share of advancing stocks, 5-day smoothed "
                "(removes single-day noise).",
    },
    "new_low": {
        "name": "New-low share", "unit": "%", "direction": "neg",
        "anchors": [(0.0, 82), (0.5, 70), (1.5, 55), (3.0, 40),
                    (6.0, 25), (12.0, 10), (20.0, 0)],
        "desc": "Share of stocks making a new 52-week low that day. A direct "
                "measure of indiscriminate selling.",
    },
    "margin_chg5": {
        "name": "Margin balance 5-day change", "unit": "%", "direction": "pos",
        "anchors": [(-29, 0), (-7, 15), (-2.5, 35), (0, 50),
                    (2, 65), (5, 85), (10, 95), (16, 100)],
        "desc": "Change in combined SSE+SZSE margin (financing) balance vs. "
                "5 trading days earlier. Anchors are pinned to real historical "
                "extremes: -29% matches 2015-07-08 (the deepest point of the "
                "deleveraging stampede); -7% matches 2024-02-08 (the "
                "snowball/DMA crisis low). Data starts 2012-09; the dimension "
                "is missing before that.",
    },
    "limit_up_rate": {
        "name": "Limit-up share", "unit": "%", "direction": "pos",
        "anchors": [(0, 30), (0.3, 42), (1.3, 50), (3.4, 62),
                    (5.4, 72), (8.9, 85), (20, 93), (95, 100)],
        "desc": "Market-wide share of stocks hitting their daily limit-up "
                "that day (reconstructed from unadjusted prices, see "
                "limitboard.py). 8.9% matches 2015-06-12, the leverage-bull peak.",
    },
    "limit_down_rate": {
        "name": "Limit-down share", "unit": "%", "direction": "neg",
        "anchors": [(0, 60), (0.3, 52), (0.8, 48), (1.8, 38),
                    (3.8, 25), (10, 15), (28, 8), (95, 0)],
        "desc": "Market-wide share of stocks hitting their daily limit-down "
                "that day. 95% matches 2015-08-24 (Black Monday, nearly the "
                "entire market limit-down); 28% matches the 2024-02-05 crisis low.",
    },
    "max_consec_limit": {
        "name": "Longest limit-up streak", "unit": "days", "direction": "pos",
        "anchors": [(1, 40), (2, 50), (5, 63), (9, 75),
                    (14, 85), (18, 92), (30, 100)],
        "desc": "Longest active consecutive-limit-up chain in the sample that "
                "day. 20 days matches 2015-06-12.",
    },
}

DIMENSIONS = [
    {"key": "valuation", "name": "Valuation despair", "weight": 35, "required": True,
     "members": ["broken_net_rate", "erp"], "agg": "mean",
     "note": "How cheap is the market. ERP was added 2026-09 (review 6.4) but "
             "only has ~5 years of data -- before 2021-09 this dimension is "
             "still just the below-book-value rate on its own. That's "
             "deliberate: the below-book-value rate has 21-year absolute "
             "anchors and its weight shouldn't be diluted too much by ERP, "
             "so the two are averaged with equal weight rather than giving "
             "ERP its own separate weight band."},
    {"key": "cooling", "name": "Trading cooldown", "weight": 20, "required": False,
     "members": ["vol_temp"], "agg": "mean",
     "note": "Is anyone still trading, and in which direction."},
    {"key": "momentum", "name": "Price momentum", "weight": 20, "required": False,
     "members": ["rsi14", "bias60", "drawdown"], "agg": "median",
     "note": "How far from normal has price fallen. Three collinear "
             "indicators, take the median -- counts as one vote."},
    {"key": "breadth", "name": "Market breadth", "weight": 10, "required": False,
     "members": ["breadth", "new_low"], "agg": "mean",
     "note": "Is this indiscriminate selling or not."},
    {"key": "leverage", "name": "Leverage sentiment", "weight": 10, "required": False,
     "members": ["margin_chg5"], "agg": "mean",
     "note": "Added 2026-09. The direction of the margin balance reflects "
             "whether leveraged money is adding or being forcibly liquidated "
             "-- collapse-style deleveraging is a common thread across every "
             "A-share crisis (2015, 2018, 2024-02), but data only exists from "
             "2012-09, so the weight is kept modest (10%); before 2012 this "
             "dimension is missing and handled by effective-weight "
             "renormalization."},
    {"key": "speculation", "name": "Speculation extremity", "weight": 5, "required": False,
     "members": ["limit_up_rate", "limit_down_rate", "max_consec_limit"], "agg": "mean",
     "note": "Added 2026-09, reconstructed from unadjusted prices (see "
             "limitboard.py), can be backfilled across the full 21 years. "
             "Weight is deliberately kept very low (5%) -- limit-up/streak "
             "samples are small and extremely volatile, easily dominated by "
             "one-off extreme events, so this is a supporting signal only, "
             "not a primary one."},
]

W_TOTAL = sum(d["weight"] for d in DIMENSIONS)


# ZONES boundaries went through one data-driven recalibration (2026-09-02).
# The original boundaries (0,15,30,70,85,100) were set by intuition. Scanning
# a rolling 150-day window of forward 120-day returns against ASI across all
# 5,262 trading days in 2005-2026 found that the zone with genuinely no edge
# and negative forward returns actually sits at ASI 60-80 (the old version
# split this across the lower half of "optimistic 70-85" and the upper half
# of "neutral"), while the old "greed zone 85-100" actually has *positive*
# forward 120-day returns (momentum continuation -- no top forms within the
# 120-day window) and is merely the deepest interim-drawdown zone: it's a
# "high volatility" zone, not a "sell" zone.
# Per-bucket stats under the new boundaries (full sample, 5,262 days):
#   0-15   Ice-cold    n=32    fwd120=+22.3%  win 90.6%  worst drawdown -4.9%
#   15-30  Pessimistic n=332   fwd120=+13.7%  win 84.4%  worst drawdown -5.9%
#   30-60  Neutral     n=3339  fwd120= +5.3%  win 57.7%  worst drawdown -8.6%
#   60-80  Optimistic  n=1337  fwd120= +1.8%  win 45.5%  worst drawdown-12.6%  <- genuinely "no edge"
#   80-100 Greed       n=222   fwd120=+12.8%  win 54.1%  worst drawdown-14.8%  <- momentum still there, most volatile
ZONES = [
    (0, 15, "Ice-cold", "Blood in the streets", "Historic-bottom territory. Panic is already priced in."),
    (15, 30, "Pessimistic", "Watch from the left side", "Cold but not yet extreme; can start scaling in."),
    (30, 60, "Neutral", "Not extreme", "Normal sentiment noise -- go back to fundamentals and single stocks."),
    (60, 80, "Optimistic", "No edge", "Hot, but historically this bucket has the worst forward return AND "
     "win rate of all five -- neither a clear add zone nor a clear sell zone; the single "
     "worst bucket to make a heavy bet in."),
    (80, 100, "Greed", "Fat-tail risk", "Peak sentiment; momentum usually keeps running within a 120-day "
     "window, but this bucket also has the deepest interim drawdown of the five -- high "
     "volatility, not a top signal."),
]

# ---- Module-level self-check: anchors must be strictly monotonic (review, "other minor issues" #1) ----
for _k, _i in INDICATORS.items():
    if _i["anchors"] is None:
        continue
    _xs = [x for x, _ in _i["anchors"]]
    assert _xs == sorted(_xs) and len(set(_xs)) == len(_xs), \
        "anchor raw values must be strictly increasing: %s" % _k
    assert all(0 <= y <= 100 for _, y in _i["anchors"]), "anchor scores must be in 0-100: %s" % _k
assert W_TOTAL == 100, "dimension weights must sum to 100, got %d" % W_TOTAL
for _d in DIMENSIONS:
    for _m in _d["members"]:
        assert _m in INDICATORS, "undefined indicator %s" % _m
