# -*- coding: utf-8 -*-
"""Unit tests for asi/guidance.py. Run: python tests/test_guidance.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asi"))
import guidance as G  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)


def make_series(n_days, reading_fn, close_fn, start="2014-01-01"):
    d0 = dt.date.fromisoformat(start)
    out, i, day = [], 0, d0
    while len(out) < n_days:
        if day.weekday() < 5:
            out.append((day.isoformat(), close_fn(i), reading_fn(i)))
            i += 1
        day += dt.timedelta(days=1)
    return out


print("Window")
s = make_series(3200, lambda i: 50.0, lambda i: 100.0)
w = G._window(s)
ok(w[0][0] > (dt.date.fromisoformat(s[-1][0]).replace(year=dt.date.fromisoformat(s[-1][0]).year - 10)).isoformat(),
   "only the last 10 years are used")

print("\nRarity")
# one day every 140 trading days (~6.4 months) dips to 20, everything else sits at 50
s = make_series(3200, lambda i: 20.0 if i % 140 == 0 else 50.0, lambda i: 100.0)
r = G.rarity(20.0, s, "low")
ok(r["label"] == "6m", "a dip reached once every ~6 months is labelled 6m (got %s, every %s)" % (r["label"], r["every_months"]))
r = G.rarity(10.0, s, "low")
ok(r["label"] == "beyond_10y" and r["hits"] == 0, "a reading below anything in the window is beyond_10y")
r = G.rarity(50.0, s, "low")
ok(r["label"] is None, "an everyday reading gets no rarity label")

print("\nOdds")
# price rises steadily: every low-side day is followed by a gain
s = make_series(1500, lambda i: 20.0 if i % 50 == 0 else 50.0, lambda i: 100.0 + i)
o = G.odds(20.0, s, "low")
ok(o["3m"]["win_rate"] == 100.0 and o["6m"]["win_rate"] == 100.0, "rising market -> 100% win rate after lows")
ok(o["episodes"] == o["days"], "dips 50 trading days apart are separate episodes")
o = G.odds(50.0, s, "high")
ok(o["3m"]["win_rate"] == 0.0, "rising market -> 0% chance of a decline after highs")
# execution lag: a one-off spike on the signal day must not count
closes = [100.0] * 400
closes[100] = 1000.0
s = [(d, closes[i], 20.0 if i == 100 else 50.0) for i, (d, _c, _r) in enumerate(make_series(400, lambda i: 0, lambda i: 0))]
o = G.odds(20.0, s, "low")
ok(o["3m"]["median_return"] == 0.0, "returns are measured from the execution day, not the signal day")

print("\nSleeve plan")
p = G.sleeve_plan(0.75, 1_000_000, 5, invested=500_000, triggered=True)
ok(p["action"] == "buy" and p["order_total"] == 250_000 and p["order_per_fund"] == 50_000,
   "trigger to 75% from 50% of a 1M sleeve -> buy 250k, 50k per fund")
p = G.sleeve_plan(0.20, 1_000_000, 5, invested=0, triggered=False)
ok(p["action"] == "hold" and p["order_total"] == 0 and p["gap_to_target"] == 200_000,
   "no order on a quiet day even when invested differs from the target")
p = G.sleeve_plan(0.20, 1_000_000, 5, invested=500_000, triggered=True)
ok(p["action"] == "sell" and p["order_total"] == -300_000 and p["target_cash"] == 800_000,
   "trigger to 20% from 50% -> sell 300k of the sleeve, never the core")

print("\n" + "=" * 46)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
