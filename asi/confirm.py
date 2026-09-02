# -*- coding: utf-8 -*-
"""
State machine for the asymmetric confirmation rule (code review section 4).

v1 only looked at a single prior-day value and required manually passing
--prev; this version reads asi_history.csv and advances automatically,
requiring the score to hold above the threshold for [need_days consecutive
trading days] before confirming, instead of firing on a single noisy crossing.

Warning -- backtest verdict (see report.py section 5): across 2005-2026 this
rule was a [net negative] -- it cost 5.4% of return on average, and actually
made the worst interim drawdown 1.4 percentage points deeper. The
implementation is kept for reproducibility; it is not recommended as a live
trading rule by default.
"""

PANIC, GREED = 15.0, 85.0


def state(series, need_days=3, thr=PANIC):
    """
    series: chronological list of ASI values (may contain None)
    Returns the latest state: {"state", "msg", "run"}
    """
    vals = [v for v in series if v is not None]
    if not vals:
        return {"state": "no_data", "msg": "No valid readings.", "run": 0}
    cur = vals[-1]

    # was the most recent crossing a "broke below, not yet confirmed" event?
    broke = False
    for v in reversed(vals):
        if v < thr:
            broke = True
            break
        # count back the number of consecutive days spent above the threshold
    run = 0
    for v in reversed(vals):
        if v >= thr:
            run += 1
        else:
            break

    if cur < thr:
        return {"state": "panic_unconfirmed", "run": 0,
                "msg": "Panic has fired but is [unconfirmed]. Historically, "
                       "after breaking below 15 the market often keeps "
                       "grinding lower for a while; waiting for %d "
                       "consecutive trading days back above %.0f before "
                       "confirming." % (need_days, thr)}
    if broke and run < need_days:
        return {"state": "confirming", "run": run,
                "msg": "Day %d/%d holding above %.0f, not yet confirmed."
                       % (run, need_days, thr)}
    if broke and run == need_days:
        return {"state": "confirmed", "run": run,
                "msg": "[CONFIRMED] %d consecutive days above %.0f, "
                       "selling pressure appears to be exhausting. Note: "
                       "backtest shows this rule costs 5.4% of return on "
                       "average with no drawdown improvement -- reference "
                       "only, not a trading instruction." % (need_days, thr)}
    if cur >= GREED:
        return {"state": "greed", "run": run,
                "msg": "Sentiment is at an extreme -- watch for fat-tail risk."}
    return {"state": "normal", "run": run,
            "msg": "No special signal, treat as a routine reading."}
