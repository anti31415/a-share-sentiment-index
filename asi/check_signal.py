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

Every run also reports how rare today's smoothed reading is over the last ten
years and what followed similar readings (guidance.py). Given the size of the
flexible cash sleeve, it turns the target weight into money:

Usage:
    python asi/check_signal.py [--reserve AMOUNT --funds N [--invested AMOUNT]]

--reserve   the flexible cash sleeve the strategy manages (core holdings are
            never part of it and never sold)
--funds     how many funds the sleeve is spread over, evenly
--invested  how much of the sleeve is in the funds right now; defaults to the
            amount the rule's state entering today implies
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
import actionable as A          # noqa: E402
import fetch                    # noqa: E402
import guidance as G            # noqa: E402
import heat as heat_mod         # noqa: E402
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


def replay(smoothed, heats, closes):
    """Runs the full rule (actionable.StateMachine: ASI hysteresis plus the
    overheating trim) over the whole series, one day at a time.
    Returns (state entering today, state after today, target weight,
    trigger reason for today's move into trim or None)."""
    machine = A.StateMachine(INITIAL_STATE)
    for s, h, c in zip(smoothed[:-1], heats[:-1], closes[:-1]):
        machine.step(s, h, c)
    prior = machine.state
    new_state, weight, reason = machine.step(smoothed[-1], heats[-1], closes[-1])
    return prior, new_state, weight, reason


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Daily ASI signal check.")
    p.add_argument("--reserve", type=float)
    p.add_argument("--funds", type=int, default=1)
    p.add_argument("--invested", type=float)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # One cached request; sizes the re-score window to cover every day since
    # asi_history.csv was last committed.
    idx = fetch.index_daily()
    idx_dates = [r[0] for r in idx]
    today = idx_dates[-1]
    hist_rows = history.load()
    committed = [(r["date"], r["asi_v2"]) for r in hist_rows
                 if r["asi_v2"] is not None and r["date"] < today]
    through = committed[-1][0] if committed else ""
    missing = gap_dates(through, idx_dates[-MAX_WINDOW:], today)
    window = min(max(MIN_WINDOW, len(missing) + SMOOTH_N + 1), MAX_WINDOW)

    date, vals, ip, _bb, recent_panel = run_daily.today_values(recent_n=window)
    try:
        res = run_daily.compute(vals)
    except InsufficientData as e:
        print(json.dumps({"date": date, "ok": False,
                          "reason": "InsufficientData: %s" % e}, ensure_ascii=False))
        return

    seq = asi_series(date, res["score"], recent_panel, committed)
    smoothed = trailing_mean([s for _d, s in seq])
    close_by_date = {r["date"]: r["close"] for r in hist_rows}
    close_by_date.update({r["date"]: r["close"] for r in recent_panel})
    close_by_date[date] = ip["close"]
    heat_by_date = heat_mod.series_by_date(idx)
    prior_state, new_state, new_weight, reason = replay(
        smoothed, [heat_by_date.get(d) for d, _s in seq], [close_by_date.get(d) for d, _s in seq])
    triggered = new_state != prior_state
    heat_today = heat_by_date.get(date)

    out = {
        "date": date,
        "close": round(ip["close"], 2),
        "asi_today": res["score"],
        "asi_smoothed_5d": round(smoothed[-1], 2),
        "coverage": res["coverage"],
        "missing_inputs": [k for k in history.V2_KEYS if vals.get(k) is None],
        "prior_state": prior_state,
        "new_state": new_state,
        "satellite_target_weight": new_weight,
        "triggered": triggered,
        "trigger_reason": reason,
        "heat": round(heat_today, 3) if heat_today is not None else None,
        "heat_trim_threshold": A.HEAT_TRIM_ENTER,
        "history_through": through,
        "recomputed_days": len(seq) - len(committed) - 1,
    }

    g_series = [(d, close_by_date[d], sm) for (d, _s), sm in zip(seq, smoothed)
                if close_by_date.get(d) is not None]
    side = G.side_of(smoothed[-1], g_series)
    out["guidance"] = {"rarity": G.rarity(smoothed[-1], g_series, side),
                       "odds": G.odds(smoothed[-1], g_series, side)}
    if args.reserve:
        prior_weight = A.STATE_WEIGHT[prior_state]
        invested = args.invested if args.invested is not None else prior_weight * args.reserve
        out["sleeve_plan"] = G.sleeve_plan(new_weight, args.reserve, args.funds, invested, triggered)
        out["sleeve_plan"]["invested_assumed"] = args.invested is None

    if triggered:
        out["alert"] = (
            "ASI ACTION SIGNAL — %s\n"
            "5-day smoothed ASI = %.1f (raw today = %.1f, coverage %.0f%%)\n"
            "State change: %s -> %s%s\n"
            "Action: place an order today to move your tactical satellite "
            "sleeve to %.0f%% invested (the rest stays in cash/money-market).\n"
            "Remember this is a FIRST-CROSSING trigger — do not repeat the "
            "order if the signal stays in this zone tomorrow; wait for the "
            "next state change."
        ) % (date, smoothed[-1], res["score"], res["coverage"] * 100,
             prior_state, new_state,
             {"heat": " (trigger: short-term overheating, heat %.0f%%)" % ((heat_today or 0) * 100),
              "asi": " (trigger: smoothed ASI in the 52-75 band)"}.get(reason, ""),
             new_weight * 100)
        print(out["alert"], file=sys.stderr)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
