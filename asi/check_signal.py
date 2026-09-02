# -*- coding: utf-8 -*-
"""
Daily live signal check for the ASI tactical satellite-sleeve strategy.

Run this once per trading-day morning (the automated task calls it at 9am on
weekdays). It:
  1. Pulls today's ASI reading (reuses run_daily.today_values()).
  2. Computes the 5-day-smoothed ASI (today + the last 4 rows already in
     data/asi_history.csv).
  3. Loads the persisted strategy state from data/tranche_state.json
     (created on first run; defaults to "Neutral").
  4. Applies the hysteresis state machine from actionable.hyst_zone_target().
  5. If the state changed today, that IS the actionable trigger: print (and
     return) an alert describing exactly what order to place. If not,
     prints a quiet "no action" line.
  6. Always persists the new state + today's smoothed value, and appends
     today's row to asi_history.csv the same way run_daily.py does, so the
     smoothing window keeps advancing day over day.

Exit code is 0 always (a "no signal" day is not an error); the caller
(the scheduled task / notification skill) inspects the printed JSON on the
last line to decide whether to page the user.

Usage:
    python asi/check_signal.py
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "data", "tranche_state.json")

sys.path.insert(0, HERE)
import actionable as A          # noqa: E402
import history                  # noqa: E402
import run_daily                # noqa: E402
from compute import InsufficientData  # noqa: E402


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"state": "Neutral (default)", "updated": None}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def smoothed_asi_today(today_score):
    """5-day mean of the last 4 rows in asi_history.csv plus today's fresh score."""
    rows = history.load()
    tail = [r["asi_v2"] for r in rows[-4:] if r["asi_v2"] is not None]
    window = tail + [today_score]
    return sum(window) / len(window)


def main():
    date, vals, ip, bn = run_daily.today_values()
    try:
        res = run_daily.compute(vals)
    except InsufficientData as e:
        out = {"date": date, "ok": False, "reason": "InsufficientData: %s" % e}
        print(json.dumps(out, ensure_ascii=False))
        return

    smoothed = smoothed_asi_today(res["score"])
    prior = load_state()
    new_state, new_weight = A.hyst_zone_target(smoothed, prior["state"])
    triggered = new_state != prior["state"]

    out = {
        "date": date,
        "close": round(ip["close"], 2),
        "asi_today": res["score"],
        "asi_smoothed_5d": round(smoothed, 2),
        "coverage": res["coverage"],
        "prior_state": prior["state"],
        "new_state": new_state,
        "satellite_target_weight": new_weight,
        "triggered": triggered,
    }

    if triggered:
        out["alert"] = (
            "ASI ACTION SIGNAL — %s\n"
            "5-day smoothed ASI = %.1f (raw today = %.1f, coverage %.0f%%)\n"
            "State change: %s -> %s\n"
            "Action: place an order today to move your tactical satellite "
            "sleeve to %.0f%% invested (the rest stays in cash/money-market).\n"
            "Remember this is a FIRST-CROSSING trigger — do not repeat the "
            "order if the signal stays in this zone tomorrow; wait for the "
            "next state change."
        ) % (date, smoothed, res["score"], res["coverage"] * 100,
             prior["state"], new_state, new_weight * 100)
        print(out["alert"], file=sys.stderr)

    save_state({"state": new_state, "updated": date,
                "asi_smoothed_5d": round(smoothed, 2)})

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
