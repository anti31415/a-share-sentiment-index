# -*- coding: utf-8 -*-
"""Unit tests for the overheating sell rule (asi/heat.py, actionable.StateMachine).
Run: python tests/test_sell_rule.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asi"))
import actionable as A  # noqa: E402
import heat  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)


def run(steps, start=A.NEUTRAL):
    m = A.StateMachine(start)
    return m, [m.step(*s) for s in steps]


print("Heat percentile")
p = heat.rolling_pct(list(range(300)) + [1000.0, -1.0])
ok(p[249] is None and p[250] == 1.0, "no reading before 250 prior values; a new high ranks 1.0")
ok(p[-1] == 0.0, "a new low ranks 0.0")
ok(p[-2] == 1.0, "today's value is ranked only against earlier days")

print("\nEntering trim")
m, out = run([(40.0, 0.95, 100.0)])
ok(out[-1][0] == A.TRIM and out[-1][2] == "heat", "heat >= 0.90 in the neutral band -> trim, reason heat")
m, out = run([(40.0, 0.85, 100.0)])
ok(out[-1][0] == A.NEUTRAL, "heat 0.85 alone does not trim")
m, out = run([(55.0, 0.2, 100.0)])
ok(out[-1][0] == A.TRIM and out[-1][2] == "asi", "smoothed ASI in 52-75 still trims, reason asi")
m, out = run([(20.0, 0.99, 100.0)])
ok(out[-1][0] == "Pessimistic (add)", "a buy-side reading wins over heat")
m, out = run([(40.0, None, 100.0)])
ok(out[-1][0] == A.NEUTRAL, "missing heat falls back to the ASI-only rule")

print("\nHolding a heat trim")
steps = [(40.0, 0.95, 100.0)] + [(40.0, 0.30, 100.0)] * 50
m, out = run(steps)
ok(out[-1][0] == A.TRIM, "stays trimmed while heat cools and the index is flat")
m, out = run([(40.0, 0.95, 100.0), (40.0, 0.3, 95.0), (40.0, 0.3, 90.0)])
ok(out[-1][0] == A.NEUTRAL, "back to neutral once the index is 10% below the signal-day close")
steps = [(40.0, 0.95, 100.0)] + [(40.0, 0.30, 100.0)] * A.HEAT_TRIM_MAX_DAYS
m, out = run(steps)
ok(out[-1][0] == A.NEUTRAL and out[-2][0] == A.TRIM, "back to neutral after HEAT_TRIM_MAX_DAYS")
steps = [(40.0, 0.95, 100.0)] + [(40.0, 0.95, 100.0)] * A.HEAT_TRIM_MAX_DAYS + [(40.0, 0.3, 100.0)]
m, out = run(steps)
ok(all(o[0] == A.TRIM for o in out), "still hot at expiry -> the hold renews instead of flipping out and back")
m, out = run([(40.0, 0.95, 100.0), (20.0, 0.5, 100.0)])
ok(out[-1][0] == "Pessimistic (add)", "a buy-side reading ends a heat trim")

print("\nASI trim keeps its old exit")
m, out = run([(55.0, 0.2, 100.0), (50.0, 0.2, 100.0), (45.0, 0.2, 100.0)])
ok(out[1][0] == A.TRIM and out[2][0] == A.NEUTRAL, "ASI-triggered trim exits below 46")
m, out = run([(55.0, 0.2, 100.0), (50.0, 0.2, 80.0)])
ok(out[-1][0] == A.TRIM, "ASI-triggered trim ignores the 10% price exit")

print("\nBacktest equivalence")
rows = [{"date": "d%04d" % i, "close": 100.0 + (i % 40), "s": 30 + (i * 7) % 50, "h": None} for i in range(400)]
nav_a, tr_a = A.simulate_hyst(rows, sig_key="s")
nav_b, tr_b = A.simulate_hyst(rows, sig_key="s", heat_key="h")
ok(abs(nav_a[-1] - nav_b[-1]) < 1e-12 and len(tr_a) == len(tr_b), "without heat the full rule replays the ASI-only rule exactly")

print("\n" + "=" * 46)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
