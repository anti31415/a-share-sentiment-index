# -*- coding: utf-8 -*-
"""
Turns ASI from "a daily reading" into "an executable signal for a fund holding."

Background: the target user holds index ETFs + quant-fund products, and
subscription/redemption has a 1-2 week (roughly 5-10 trading day) lag --
decide to add/trim today, and the money doesn't actually settle for one to
two weeks. That implies:
  1. Single-day noise is meaningless -- with 5-10 days between decision and
     execution, today's reading is stale by the time it matters, so
     decisions should use a [smoothed] reading.
  2. "Buy the exact bottom / sell the exact top" is a false goal -- execution
     lag alone kills that precision. The real question is "does a signal
     issued 5-10 days ago still hold up by the time it executes?"
  3. Checking every day invites acting every day -- subscription/redemption
     has cost and friction, so a [hysteresis] mechanism is needed: fire once
     on entering an extreme zone, only allow firing again after returning to
     neutral -- not daily churn.

Only runs the most recent ~10 years (from 2016-09); the indicators with a
shorter history (ERP / margin balance) are essentially complete within this window.
"""
import history
import backtest as bt


WINDOW_START = "2016-09-01"


def load_window():
    rows = [r for r in history.load() if r["date"] >= WINDOW_START]
    return bt.attach_forward(rows)


def smooth(rows, key, n=5):
    """n-day simple average -- a decision shouldn't be swung by single-day
    noise anyway, since execution takes 5-10 days regardless."""
    out = []
    buf = []
    for r in rows:
        v = r.get(key)
        if v is not None:
            buf.append(v)
            if len(buf) > n:
                buf.pop(0)
        out.append(sum(buf) / len(buf) if buf else None)
    return out


# ------------------------------------------------------------------ lag-decay check
def lag_decay(rows, key="asi_v2_sm5", lags=(0, 3, 5, 8, 10, 15, 20)):
    """
    A signal fires on day T, execution happens on day T+lag. Test: bucket by
    the day-T signal, then look at the return over the 120 days [after]
    T+lag -- how much of the "buy-the-dip bonus" survives as lag grows?
    """
    c = [r["close"] for r in rows]
    n = len(rows)
    out = {}
    for lag in lags:
        rows_lag = []
        for i in range(n):
            if i + lag >= n:
                continue
            sig = rows[i].get(key)
            if sig is None:
                continue
            entry = c[i + lag]
            j = min(n - 1, i + lag + 120)
            fwd120 = (c[j] / entry - 1) * 100 if j > i + lag else None
            rows_lag.append({"sig": sig, "fwd120": fwd120, "entry_date": rows[i + lag]["date"]})
        cold = [r for r in rows_lag if r["sig"] < 30 and r["fwd120"] is not None]
        hot = [r for r in rows_lag if r["sig"] > 70 and r["fwd120"] is not None]
        mid = [r for r in rows_lag if 30 <= r["sig"] <= 70 and r["fwd120"] is not None]
        out[lag] = {
            "cold_n": len(cold), "cold_fwd120": bt.mean([r["fwd120"] for r in cold]) if cold else None,
            "hot_n": len(hot), "hot_fwd120": bt.mean([r["fwd120"] for r in hot]) if hot else None,
            "mid_n": len(mid), "mid_fwd120": bt.mean([r["fwd120"] for r in mid]) if mid else None,
        }
    return out


# ------------------------------------------------------------------ tiered rebalancing strategy
# Design rationale:
#   * Fund subscription/redemption has a lag, so "decide and execute the
#     same day" is fantasy -- hence [a smoothed signal + hysteresis
#     triggering]: entering a zone for the [first time] fires one rebalance
#     order; staying in that zone for extra days doesn't fire it repeatedly
#     (in reality you wouldn't submit a new subscription every single day
#     sentiment stays ice-cold for 10 days straight).
#   * Only the [satellite sleeve] moves; the core position (the part you
#     already intended to hold long-term) never changes -- this is closer to
#     "enhancing an existing holding" than to timing the entire position in
#     and out.
#   * The execution price is the closing price on the LAG-th trading day
#     after the signal fires, simulating the subscription/redemption lag.
SATELLITE_BANDS = [
    (0, 15, 1.00),     # ice-cold: satellite sleeve fully invested
    (15, 30, 0.70),
    (30, 60, 0.50),    # neutral: half-invested by default (the baseline state)
    (60, 80, 0.20),
    (80, 101, 0.00),   # greed: satellite sleeve emptied out
]


def zone_target(score, bands=SATELLITE_BANDS):
    for lo, hi, w in bands:
        if lo <= score < hi:
            return w
    return 0.50


# State-machine hysteresis band -- only fires at the two ends genuinely
# validated by backtest; the 60-80 "no edge" zone is deliberately not
# actively traded (both entering and exiting it get repeatedly whipsawed by
# the 30-60/60-80 boundary), so 30-100 is left alone entirely. The ENTER
# threshold is more extreme than the EXIT threshold, creating a buffer that
# cuts down on back-and-forth churn.
HYST_STATES = [
    # state name, satellite weight, entry condition (reading), exit condition (reading returns into this range to exit)
    {"name": "Ice-cold (full)", "weight": 1.00, "enter": lambda s: s < 15, "exit": lambda s: s >= 25},
    {"name": "Pessimistic (add)", "weight": 0.75, "enter": lambda s: s < 25, "exit": lambda s: s >= 40},
    {"name": "Neutral (default)", "weight": 0.50, "enter": None, "exit": None},
    {"name": "Optimistic (trim)", "weight": 0.20, "enter": lambda s: 65 <= s < 80, "exit": lambda s: s < 55 or s >= 80},
]


STATE_WEIGHT = {"Ice-cold (full)": 1.00, "Pessimistic (add)": 0.75, "Neutral (default)": 0.50, "Optimistic (trim)": 0.20}


def hyst_zone_target(score, state):
    """
    Hysteresis state machine: `state` is the currently active state name.
    Only an `enter` condition moves into a state; only an `exit` condition
    returns it to neutral.
    Returns (new state name, the satellite weight for that new state) --
    the weight is always determined by the NEW state, never the old one.
    """
    if state == "Ice-cold (full)":
        new_state = "Neutral (default)" if score >= 25 else state
    elif state == "Pessimistic (add)":
        new_state = "Neutral (default)" if score >= 40 else state
    elif state == "Optimistic (trim)":
        new_state = "Neutral (default)" if (score < 55 or score >= 80) else state
    else:
        # currently neutral -- check whether an extreme state should fire
        # (priority: ice-cold > pessimistic > optimistic)
        if score < 15:
            new_state = "Ice-cold (full)"
        elif score < 25:
            new_state = "Pessimistic (add)"
        elif 65 <= score < 80:
            new_state = "Optimistic (trim)"
        else:
            new_state = "Neutral (default)"
    return new_state, STATE_WEIGHT[new_state]


def simulate_hyst(rows, sig_key="asi_v2_sm5", lag=8, cost_bp=5, cash_annual=0.02):
    """
    State-machine hysteresis version -- [the satellite sleeve is accounted
    for independently], not mixed in with the core holding. This is the
    framework that actually matches the target user's situation: the core
    holding (ETF/quant fund) doesn't move and isn't part of this strategy;
    what this strategy manages is a separate "tactical reserve" (e.g. 20-30%
    of total investable assets held in cash/money-market funds) that swaps
    between cash yield and index exposure following the signal. The
    uninvested portion earns interest at cash_annual (default 2%, an
    approximation of money-market/short-bond yields).
    """
    n = len(rows)
    c = [r["close"] for r in rows]
    cash_daily = (1 + cash_annual) ** (1 / 243) - 1
    state = "Neutral (default)"
    w = 0.50
    pending = []
    nav = [1.0]
    trades = []
    for i in range(1, n):
        sig = rows[i - 1].get(sig_key)
        if sig is not None and not pending:
            new_state, new_w = hyst_zone_target(sig, state)
            if new_state != state:
                pending.append((i - 1 + lag, new_w))
                state = new_state
        cur_ret = c[i] / c[i - 1] - 1
        executed_today = False
        for exec_i, tgt in list(pending):
            if exec_i == i:
                cost = abs(tgt - w) * (cost_bp / 10000.0)
                w = tgt
                executed_today = True
                trades.append({"date": rows[i]["date"], "price": c[i], "target": tgt, "state": state})
                pending.remove((exec_i, tgt))
        step = w * cur_ret + (1 - w) * cash_daily
        if executed_today:
            step -= cost
        nav.append(nav[-1] * (1 + step))
    return nav, trades


def bench_fixed(rows, w, cash_annual=0.02):
    """Fixed-weight benchmark (same independent-satellite-sleeve accounting; the uninvested part earns cash yield)."""
    n = len(rows)
    c = [r["close"] for r in rows]
    cash_daily = (1 + cash_annual) ** (1 / 243) - 1
    nav = [1.0]
    for i in range(1, n):
        cur_ret = c[i] / c[i - 1] - 1
        nav.append(nav[-1] * (1 + w * cur_ret + (1 - w) * cash_daily))
    return nav


def simulate_tranche(rows, sig_key="asi_v2_sm5", lag=8, core_weight=0.70,
                      bands=SATELLITE_BANDS, cost_bp=5):
    """
    core_weight   : the core position (the always-fully-invested share); satellite sleeve = 1 - core_weight
    lag           : trading days between a signal firing and it actually settling
    cost_bp       : two-sided impact cost per rebalance (basis points), simulating fees/slippage
    Returns the day-by-day NAV series, plus the execution price of every
    satellite-sleeve rebalance (used to compute the cost-basis comparison).
    """
    n = len(rows)
    c = [r["close"] for r in rows]
    satellite_target_now = zone_target(rows[0].get(sig_key) or 50.0, bands)
    pending = []          # [(execution-day index, new target weight)]
    satellite_w = satellite_target_now     # actual current satellite weight (moves toward the target incrementally)
    last_triggered_zone = None
    nav = [1.0]
    trades = []            # records every actual fill (for the cost-basis comparison)

    for i in range(1, n):
        # trigger check: only fire an order when the signal enters a NEW zone
        # relative to the last-triggered zone (hysteresis)
        sig = rows[i - 1].get(sig_key)          # decide from the prior day's already-settled smoothed signal, no lookahead
        if sig is not None and not pending:      # don't stack a new order while one is already in flight (you can't cancel-and-resubmit a real subscription either)
            zone = zone_target(sig, bands)
            if zone != last_triggered_zone:
                pending.append((i - 1 + lag, zone))
                last_triggered_zone = zone

        # settle any order that's due today
        cur_ret = c[i] / c[i - 1] - 1
        executed_today = False
        for exec_i, tgt in list(pending):
            if exec_i == i:
                cost = abs(tgt - satellite_w) * (cost_bp / 10000.0)
                satellite_w = tgt
                executed_today = True
                trades.append({"date": rows[i]["date"], "price": c[i], "target": tgt})
                pending.remove((exec_i, tgt))
        w = core_weight + satellite_w * (1 - core_weight)
        step = w * cur_ret
        if executed_today:
            step -= cost
        nav.append(nav[-1] * (1 + step))
    return nav, trades


def cost_basis(trades, rows_close_by_date, capital_per_trade=1.0):
    """
    A simplified cost-basis comparison: the execution price of every
    satellite-weight [increase], compared against the average cost of a
    plain [equal-weighted dollar-cost-average] (fixed monthly purchases)
    over the same backtest window.
    """
    buys = [t for i, t in enumerate(trades) if i == 0 or t["target"] > trades[i - 1]["target"]]
    if not buys:
        return None
    avg_signal_price = sum(t["price"] for t in buys) / len(buys)
    return avg_signal_price, len(buys)


# ------------------------------------------------------------------ benchmark strategies
def bench_buyhold(rows):
    c = [r["close"] for r in rows]
    nav = [1.0]
    for i in range(1, len(rows)):
        nav.append(nav[-1] * (c[i] / c[i - 1]))
    return nav


def bench_dca_monthly(rows, core_weight=0.70):
    """
    Dollar-cost-averaging benchmark: the satellite sleeve instead buys an
    equal number of shares on a [fixed monthly date], ignoring the signal
    entirely -- this is the closest match to what the user would do without
    a sentiment index, just "averaging down" the conventional way.
    Simplified as: the satellite weight rises linearly from 0 to 1 on the
    first trading day of each month (fully built up after N months, then
    held); here a 24-month build-out period stands in for "averaging in over time."
    """
    n = len(rows)
    c = [r["close"] for r in rows]
    build_months = 24
    month_marks = []
    last_month = None
    for i, r in enumerate(rows):
        m = r["date"][:7]
        if m != last_month:
            month_marks.append(i)
            last_month = m
    satellite_w = 0.0
    step = 1.0 / build_months
    nav = [1.0]
    marks_used = 0
    for i in range(1, n):
        cur_ret = c[i] / c[i - 1] - 1
        if i in month_marks and marks_used < build_months:
            satellite_w = min(1.0, satellite_w + step)
            marks_used += 1
        w = core_weight + satellite_w * (1 - core_weight)
        nav.append(nav[-1] * (1 + w * cur_ret))
    return nav


def nav_stats(nav, trading_days_per_year=243):
    yrs = len(nav) / trading_days_per_year
    cagr = (nav[-1] ** (1 / yrs) - 1) * 100
    peak, mdd = nav[0], 0.0
    for x in nav:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    m = bt.mean(rets)
    vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * (trading_days_per_year ** 0.5) * 100
    return {"final": nav[-1], "cagr": cagr, "mdd": mdd * 100, "vol": vol,
            "calmar": cagr / abs(mdd * 100) if mdd else float("nan")}
