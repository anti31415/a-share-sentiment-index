# -*- coding: utf-8 -*-
"""Unit tests (v1 had zero). Run: python tests/test_compute.py"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asi"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from compute import compute, interpolate, vol_temp_score, InsufficientData, zone_of  # noqa: E402
from indicators import INDICATORS, DIMENSIONS                                        # noqa: E402
from series import rsi_wilder, index_panel                                           # noqa: E402

FAIL = []
TOTAL = [0]


def ok(cond, msg):
    TOTAL[0] += 1
    print(("  [PASS] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAIL.append(msg)


def near(a, b, tol=1e-6):
    return abs(a - b) < tol


print("Anchors and config")
for k, i in INDICATORS.items():
    if i["anchors"]:
        xs = [x for x, _ in i["anchors"]]
        ok(xs == sorted(xs) and len(set(xs)) == len(xs), "%s anchors strictly increasing" % k)
ok(sum(d["weight"] for d in DIMENSIONS) == 100, "dimension weights sum to 100")

print("\nInterpolation")
a = [(0, 0), (10, 50), (20, 100)]
ok(near(interpolate(-5, a), 0), "clamps below left end")
ok(near(interpolate(25, a), 100), "clamps above right end")
ok(near(interpolate(5, a), 25), "linear within range")
ok(near(interpolate(10, a), 50), "hits an exact anchor")

print("\nRSI boundary (v1 bug 1.3)")
ok(near(rsi_wilder([100.0] * 60), 50.0), "zero volatility -> 50 (v1 returned 100)")
ok(near(rsi_wilder([100.0 + i for i in range(60)]), 100.0), "one-way rally -> 100")
ok(rsi_wilder([100.0] * 10) is None, "insufficient samples -> None (no longer fakes 50)")

print("\n60-day average-volume slice (v1 bug 1.2)")
rows = [["d%03d" % i, 100.0, 10.0] for i in range(61)]
rows[-1][2] = 20.0
p = index_panel(rows)
ok(p[-1].get("vol_ratio") is not None, "volume ratio available at exactly 61 bars")
ok(near(p[-1]["vol_ratio"], 2.0), "denominator strictly takes 60 bars (=10.0), ratio = 2.0")
ok(index_panel(rows[:60])[-1].get("vol_ratio") is None, "no volume ratio with only 60 bars")

print("\nMissing-value normalization (v1 bug 1.1)")
full = {"broken_net_rate": 8.0, "vol_temp": 50.0, "rsi14": 50.0,
        "bias60": 0.0, "drawdown": -20.0, "breadth": 50.0, "new_low": 1.5,
        "margin_chg5": 0.0, "limit_up_rate": 1.5, "limit_down_rate": 0.3,
        "max_consec_limit": 2}
r_full = compute(full)
ok(near(r_full["coverage"], 1.0), "full inputs -> coverage = 1.0")
part = dict(full)
for k in ("breadth", "new_low"):
    part.pop(k)
r_part = compute(part)
ok(near(r_part["coverage"], 0.90), "missing breadth dimension (weight 10%) -> coverage = 0.90")
ok(abs(r_part["score"] - r_full["score"]) < 6,
   "losing one dimension only shifts the score a little (%.1f -> %.1f), doesn't collapse to 0"
   % (r_full["score"], r_part["score"]))
only = {"broken_net_rate": 8.0}
r_only = compute(only)
ok(r_only["degraded"] is True, "degraded = True with only the valuation dimension left")
ok(r_only["score"] > 30, "a single surviving dimension is still not scored as ice-cold (v1 would give 11.2)")

print("\nA missing valuation dimension must refuse to score (review 1.1 follow-up)")
try:
    compute({"breadth": 50.0, "rsi14": 50.0})
    ok(False, "raises InsufficientData when below-book-value rate is missing")
except InsufficientData:
    ok(True, "raises InsufficientData when below-book-value rate is missing")
ok(compute({"breadth": 50.0, "rsi14": 50.0}, require_valuation=False)["score"] > 0,
   "explicit override still allows a degraded score (backtest use only)")

print("\nVolume-price pairing (review 3.2)")
panic = vol_temp_score(2.5, -5.0)
euphoria = vol_temp_score(2.5, 5.0)
exhaust = vol_temp_score(0.5, -5.0)
ok(panic < 15, "heavy volume + decline = panic, %.1f pts (v1 would give ~96, 'hot')" % panic)
ok(euphoria > 85, "heavy volume + advance = euphoria, %.1f pts" % euphoria)
ok(panic < exhaust < 50, "light-volume decline (%.1f) is colder than neutral, warmer than heavy-volume decline" % exhaust)

print("\nPrice momentum three-in-one (review 3.1)")
v = {"broken_net_rate": 8.0, "rsi14": 20.0, "bias60": -20.0, "drawdown": -60.0}
r = compute(v)
mom = [d for d in r["dims"] if d["key"] == "momentum"][0]
ok(mom["weight"] == 20, "price-momentum dimension weight is 20% (v1's three parts summed to 40%)")
ok(mom["score"] < 10, "dimension score is low when all three are extreme: %.1f" % mom["score"])

print("\nZones return an index, not a reverse lookup by Chinese label")
i, name, tag, _ = zone_of(7.0)
ok(i == 0 and "Ice-cold" in name, "score 7 -> ice-cold zone, index=0")
ok(zone_of(100.0)[0] == 4, "score 100 -> greed zone, index=4")

print("\n2026-09-02 recalibration: greed-zone boundary moved from 85 to 80")
ok(zone_of(79.9)[1] == zone_of(65.0)[1], "79.9 and 65 fall in the same 'optimistic' zone")
ok(zone_of(80.0)[1] != zone_of(79.9)[1], "80 is where the greed zone starts")
ok("No edge" in zone_of(70.0)[2], "the optimistic-zone tag flags it as the worst-performing bucket in backtest")

print("\nERP indicator (review 6.4)")
from indicators import INDICATORS as _IND
ok("erp" in _IND, "erp indicator is registered")
ok("erp" in [m for d in DIMENSIONS for m in d["members"] if d["key"] == "valuation"],
   "erp has been folded into the valuation-despair dimension")
v_bn_only = compute({"broken_net_rate": 8.0})
v_bn_erp = compute({"broken_net_rate": 8.0, "erp": 5.0})
val_bn = [d for d in v_bn_only["dims"] if d["key"] == "valuation"][0]["score"]
val_both = [d for d in v_bn_erp["dims"] if d["key"] == "valuation"][0]["score"]
ok(val_bn != val_both, "adding erp changes the valuation-dimension score (the two components aren't the same number)")

print("\nStructural Z-score (review 3.3, rolling_z has no lookahead)")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asi"))
from history import rolling_z                                                        # noqa: E402
hist = [10.0 + (i % 2) for i in range(260)]     # alternating 10/11 to create a nonzero std dev
z = rolling_z(hist + [20.0])
ok(z[-1] is not None and z[-1] > 0, "once enough samples exist, a reading above the historical mean -> positive Z")
ok(all(x is None for x in z[:249]), "no Z-score until a full year of samples exists")
z_flat = rolling_z([10.0] * 300)
ok(z_flat[-1] == 0.0, "zero historical variance -> Z degrades to 0.0 instead of a div-by-zero error")

print("\nLeverage sentiment / speculation extremity (added 2026-09-02 on user request)")
ok("leverage" in [d["key"] for d in DIMENSIONS], "leverage-sentiment dimension is registered")
ok("speculation" in [d["key"] for d in DIMENSIONS], "speculation-extremity dimension is registered")
lev = [d for d in DIMENSIONS if d["key"] == "leverage"][0]
spec = [d for d in DIMENSIONS if d["key"] == "speculation"][0]
ok(lev["weight"] == 10, "leverage-sentiment weight is 10%")
ok(spec["weight"] == 5, "speculation-extremity weight is 5% (deliberately low -- small, volatile sample)")
r_crash = compute({"broken_net_rate": 8.0, "margin_chg5": -29.0})
r_calm = compute({"broken_net_rate": 8.0, "margin_chg5": 0.0})
ok(r_crash["score"] < r_calm["score"],
   "a 29% margin-balance collapse in 5 days (2015-07-08's real value) reads colder than flat")
r_up = compute({"broken_net_rate": 8.0, "limit_up_rate": 8.9, "max_consec_limit": 20})
r_down = compute({"broken_net_rate": 8.0, "limit_down_rate": 95.4})
ok(r_up["score"] > r_down["score"],
   "a limit-up frenzy (2015-06-12's real value) reads hotter than a limit-down cascade (2015-08-24's real value)")

import limitboard                                                                    # noqa: E402
up_thr, down_thr = limitboard.limit_thresholds("300750", "CATL", "2026-01-01")
ok(near(up_thr, 0.198), "ChiNext limit-up threshold is 19.8% after 2020-08-24")
up_thr2, _ = limitboard.limit_thresholds("300750", "CATL", "2019-01-01")
ok(near(up_thr2, 0.098), "ChiNext limit-up threshold is 9.8% before 2020-08-24")
up_st, _ = limitboard.limit_thresholds("600000", "ST Example", "2026-01-01")
ok(near(up_st, 0.048), "ST-flagged stocks have a 4.8% limit-up threshold")

print("\nActionable hysteresis state machine (added 2026-09-02: a fund-holding-friendly executable signal)")
import actionable as ACT                                                             # noqa: E402
ns, w = ACT.hyst_zone_target(10.0, "Neutral (default)")
ok(ns == "Ice-cold (full)" and w == 1.00, "a score of 10 from neutral triggers ice-cold/full")
ns2, w2 = ACT.hyst_zone_target(20.0, ns)
ok(ns2 == "Ice-cold (full)" and w2 == 1.00, "20 hasn't reached the exit threshold (25) -- stays ice-cold/full")
ns3, w3 = ACT.hyst_zone_target(26.0, ns2)
ok(ns3 == "Neutral (default)" and w3 == 0.50, "26 triggers the exit, back to neutral")
ns4, w4 = ACT.hyst_zone_target(70.0, "Neutral (default)")
ok(ns4 == "Optimistic (trim)" and w4 == 0.20, "a score of 70 from neutral triggers optimistic/trim")
ns5, w5 = ACT.hyst_zone_target(60.0, ns4)
ok(ns5 == "Optimistic (trim)" and w5 == 0.20, "60 hasn't reached the exit threshold (<55) -- stays optimistic/trim")
ns6, w6 = ACT.hyst_zone_target(50.0, ns5)
ok(ns6 == "Neutral (default)" and w6 == 0.50, "50 triggers the exit, back to neutral")
ns7, w7 = ACT.hyst_zone_target(82.0, "Optimistic (trim)")
ok(ns7 == "Neutral (default)" and w7 == 0.50,
   "spiking to 82 also exits optimistic/trim (back to neutral, not further trimming)")

print("=" * 46)
print("%d passed, %d failed" % (TOTAL[0] - len(FAIL), len(FAIL)))
sys.exit(1 if FAIL else 0)
