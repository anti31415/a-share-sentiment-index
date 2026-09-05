# -*- coding: utf-8 -*-
"""
Daily live signal check for the ASI tactical satellite-sleeve strategy.

Run this once per trading-day morning (the automated task calls it at 9am on
weekdays). It:
  1. Pulls today's ASI reading (reuses run_daily.today_values()).
  2. Rebuilds the recent ASI series and 5-day-smooths it.
  3. Replays the hysteresis state machine from actionable.hyst_zone_target()
     over that series to derive the state the strategy is currently in.
  4. If today's step changes the state, that IS the actionable trigger: print
     (and return) an alert describing exactly what order to place. If not,
     prints a quiet "no action" line.

[This check holds no state between runs.] The state is a pure function of the
ASI series, replayed from "Neutral (default)" exactly the way
actionable.simulate_hyst() replays it in the backtest, so two runs on the same
data always agree and nothing has to survive between them. That matters
because the scheduled task runs in a fresh container against a fresh clone: an
earlier version persisted the state in data/tranche_state.json, which is
gitignored, so every scheduled run silently started from "Neutral (default)"
again -- re-firing the same "first crossing" alert every day for as long as
the reading stayed in an extreme zone.

For the same reason the series is not read from data/asi_history.csv alone.
That file only advances when someone commits it; days between its last row and
today are re-scored here from freshly fetched data (see run_daily's
recent_panel), so the 5-day window keeps moving whether or not the CSV is
up to date.

Exit code is 0 always (a "no signal" day is not an error); the caller
(the scheduled task / notification skill) inspects the printed JSON on the
last line to decide whether to page the user.

Usage:
    python asi/check_signal.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
import actionable as A          # noqa: E402
import fetch                    # noqa: E402
import history                  # noqa: E402
import run_daily                # noqa: E402
from compute import compute, InsufficientData  # noqa: E402

SMOOTH_N = 5          # 5-day smoothing, matching the backtest's asi_v2_sm5
MIN_WINDOW = 15       # recent days always re-scored, enough for a 5-day mean
MAX_WINDOW = 90       # cap on how far back a stale history.csv makes us go
INITIAL_STATE = "Neutral (default)"


def score_row(rec):
    """ASI for one past day, scored the way history.build() scores it."""
    vals = {k: rec.get(k) for k in history.V2_KEYS}
    try:
        return compute(vals, require_valuation=False)["score"]
    except InsufficientData:
        return None


def gap_dates(committed_through, recent_dates, today):
    """Trading days that the committed history is missing, today excluded."""
    return [d for d in recent_dates if committed_through < d < today]


def asi_series(today, today_score, recent_panel, committed):
    """[(date, asi), ...] oldest first, ending with today's fresh score."""
    through = committed[-1][0] if committed else ""
    live = [(r["date"], score_row(r)) for r in recent_panel
            if through < r["date"] < today]
    return committed + [(d, s) for d, s in live if s is not None] + [(today, today_score)]


def trailing_mean(vals, n=SMOOTH_N):
    out, buf = [], []
    for v in vals:
        buf.append(v)
        if len(buf) > n:
            buf.pop(0)
        out.append(sum(buf) / len(buf))
    return out


def replay(smoothed):
    """Runs the state machine over the whole smoothed series.
    Returns (state entering today, state after today, target weight)."""
    state = INITIAL_STATE
    for s in smoothed[:-1]:
        state, _ = A.hyst_zone_target(s, state)
    prior = state
    new_state, weight = A.hyst_zone_target(smoothed[-1], state)
    return prior, new_state, weight


def main():
    # One cached request; lets us size the window and decide whether the
    # per-stock BPS fetch (about half of a cold run's network work) is needed
    # at all -- it only is when a past day has to be re-scored.
    idx_dates = [r[0] for r in fetch.index_daily()]
    today = idx_dates[-1]
    committed = [(r["date"], r["asi_v2"]) for r in history.load()
                 if r["asi_v2"] is not None and r["date"] < today]
    through = committed[-1][0] if committed else ""
    missing = gap_dates(through, idx_dates[-MAX_WINDOW:], today)
    window = min(max(MIN_WINDOW, len(missing) + SMOOTH_N + 1), MAX_WINDOW)

    date, vals, ip, bn, recent_panel = run_daily.today_values(
        recent_n=window, with_bps=bool(missing))
    try:
        res = run_daily.compute(vals)
    except InsufficientData as e:
        print(json.dumps({"date": date, "ok": False,
                          "reason": "InsufficientData: %s" % e}, ensure_ascii=False))
        return

    seq = asi_series(date, res["score"], recent_panel, committed)
    smoothed = trailing_mean([s for _d, s in seq])
    prior_state, new_state, new_weight = replay(smoothed)
    triggered = new_state != prior_state

    out = {
        "date": date,
        "close": round(ip["close"], 2),
        "asi_today": res["score"],
        "asi_smoothed_5d": round(smoothed[-1], 2),
        "coverage": res["coverage"],
        "prior_state": prior_state,
        "new_state": new_state,
        "satellite_target_weight": new_weight,
        "triggered": triggered,
        "history_through": through,
        "recomputed_days": len(seq) - len(committed) - 1,
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
        ) % (date, smoothed[-1], res["score"], res["coverage"] * 100,
             prior_state, new_state, new_weight * 100)
        print(out["alert"], file=sys.stderr)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
