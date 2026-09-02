---
name: asi-signal-check
description: Check the A-Share Sentiment Index (ASI) tactical satellite-sleeve trading signal and report whether today's reading crosses an actionable threshold. Use this when the user asks to check their sentiment index, run the daily ASI check, or wants to know if it's time to add to or trim their tactical reserve for A-share ETF / quant-fund holdings.
---

# ASI Signal Check

A-share sentiment index that turns a market-sentiment score into a concrete,
lag-aware trading signal for a *tactical satellite sleeve* sitting alongside a
core ETF / quant-fund holding — not a system for timing the core holding
itself (fund subscription/redemption lag of 1-2 weeks makes that impractical).

## What this skill does

1. Runs `asi/check_signal.py`, which:
   - pulls today's market data and computes the six-dimension ASI score
     (valuation despair, cooling, momentum, breadth, leverage sentiment,
     speculation extremity — see `asi/indicators.py` for the full model);
   - 5-day-smooths it against the last 4 rows of `asi/data/asi_history.csv`
     (decisions execute with lag, so single-day noise is discarded);
   - runs it through a hysteresis state machine (`asi/actionable.py:
     hyst_zone_target`) that only acts at the two ends genuinely validated
     by backtest — it does **not** trade the "no edge" 30-65 middle band;
   - compares the new state against the persisted state in
     `asi/data/tranche_state.json`.
2. If the state **changed** today (a first-crossing event), that is the
   actionable trigger. Report it clearly and tell the user the target
   satellite-sleeve weight to move to. If the state did **not** change,
   report a quiet "no action needed today" — most days are like this by
   design (the strategy intentionally avoids churn).

## How to run it

```bash
cd asi
python check_signal.py
```

It prints a human-readable alert to stderr **only when triggered**, and
always prints one JSON line to stdout, e.g.:

```json
{"date":"2026-09-02","close":3979.89,"asi_today":48.6,"asi_smoothed_5d":51.9,
 "coverage":0.85,"prior_state":"Neutral (default)",
 "new_state":"Pessimistic (add)","satellite_target_weight":0.75,
 "triggered":true,"alert":"..."}
```

## Interpreting the result

| `new_state` | Satellite weight | Meaning |
|---|---|---|
| Ice-cold (full) | 100% | ASI 5-day-smoothed < 15 — rare, deepest panic tier |
| Pessimistic (add) | 75% | ASI 5-day-smoothed < 25 — add to the reserve |
| Neutral (default) | 50% | No signal — baseline allocation, do nothing |
| Optimistic (trim) | 20% | ASI 5-day-smoothed in [65, 80) — trim the reserve |

- **`triggered: true`** → tell the user explicitly: "Place an order today
  to move the tactical sleeve to `satellite_target_weight * 100`% invested."
  Emphasize this is a first-crossing trigger — remind them not to repeat
  the order every day the reading stays in the same zone; the next order
  only comes on the *next* state change.
- **`triggered: false`** → nothing to report by default. Only mention it if
  the user explicitly asked for a status check (then say "no signal today,
  currently in `new_state`").
- **`coverage < 1.0`** → some data sources (ERP, margin balance, limit-up
  reconstruction) came back partial today; the score is still valid
  (dimensions renormalize), but flag it as a lower-confidence reading if
  coverage drops noticeably below ~0.85.

## Delivering the alert

When this check is invoked by a scheduled/automated run (see
`docs/AUTOMATION.md` in the project root) and `triggered` is `true`, push
a notification to the user immediately — this is exactly the "something
happened while they were away that they'd want to know now" case. Keep the
notification to one line: state change + target weight, e.g.
`"ASI: state → Pessimistic (add). Move satellite sleeve to 75%."`
When invoked interactively by the user asking "check my sentiment index,"
report the full JSON contents in readable form regardless of `triggered`.

## Background / methodology

The full model, its six dimensions, and every validation round (including
what did **not** work — the confirm-then-buy rule, linear position mapping,
and the speculation dimension all showed negative or negligible value in
backtest and are flagged as such in `asi/indicators.py` and
`README.md`) are documented in the project root `README.md`. Read it before
making any changes to the anchors, weights, or hysteresis thresholds — they
are calibrated against specific historical events (2015 leverage bull/bust,
2018 trade war, 2020 covid crash, 2024-02 snowball/DMA crisis) and are not
arbitrary.

## Caveats to always surface if asked

- Backtest window is 2016-09 to present (~10 years); the smoothed ASI never
  reached the two most extreme states (Ice-cold / more-extreme-than-Optimistic)
  in that window — they exist as a safety net for conditions this decade
  didn't produce, not as proven-in-backtest triggers.
- This is not investment advice; it is a mechanical, backtested rule the
  user asked to have monitored. Never claim certainty about future returns.
