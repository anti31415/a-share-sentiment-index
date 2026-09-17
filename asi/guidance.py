# -*- coding: utf-8 -*-
"""
Plain-language context for a reading of the 5-day-smoothed ASI, from the last
ten years of its own history:

  rarity(reading)     -- how rare the reading is, as a return period: "a low
                         this deep shows up about once every N months". Counted
                         on calendar months: a month "reaches" a low when its
                         lowest daily reading is at or below it (a monthly
                         check would otherwise miss mid-month extremes).
  odds(reading)       -- what happened after past readings at least this
                         extreme: share of days on which the Shanghai
                         Composite was higher (low side) or lower (high side)
                         3 and 6 months later, measured from the day an order
                         placed on the signal would actually execute (EXEC_LAG
                         trading days later -- fund subscription/redemption).
  reference_table()   -- the reading thresholds for the standard return
                         periods (3m ... 10y) with the odds for each.
  sleeve_plan(weight) -- a target satellite weight turned into money, for a
                         flexible cash reserve spread evenly over the funds on
                         top of core holdings that are never sold.

Only the last YEARS years are used: the market's structure has changed
quickly, and the user asked for recent history to carry the decision.
Samples are overlapping daily windows, so `episodes` (separate dips at least
a month apart) is the honest count of independent observations.
"""
import datetime as _dt

YEARS = 10
EXEC_LAG = 8                      # trading days from signal to execution
HORIZONS = {"3m": 60, "6m": 120}  # trading days
PERIODS = [(3, "3m"), (6, "6m"), (12, "1y"), (24, "2y"), (60, "5y"), (120, "10y")]
EPISODE_GAP = 21


def _window(series):
    """series: [(date, close, smoothed_asi)] oldest first -> the last YEARS years."""
    if not series:
        return []
    last = _dt.date.fromisoformat(series[-1][0])
    try:
        start = last.replace(year=last.year - YEARS)
    except ValueError:                               # Feb 29
        start = last.replace(year=last.year - YEARS, day=28)
    start = start.isoformat()
    return [x for x in series if x[0] > start and x[2] is not None]


def _months(win):
    out = {}
    for d, _c, s in win:
        lo, hi = out.get(d[:7], (s, s))
        out[d[:7]] = (min(lo, s), max(hi, s))
    return out


def side_of(reading, series):
    vals = sorted(s for _d, _c, s in _window(series))
    return "low" if reading <= vals[len(vals) // 2] else "high"


def rarity(reading, series, side=None):
    """{"side", "months", "hits", "every_months", "label"}. label is the
    rarest standard period the reading qualifies for ("3m" ... "10y"),
    "beyond_10y" when no month in the window went this far, or None when the
    reading is more common than once every 3 months."""
    win = _window(series)
    side = side or side_of(reading, series)
    months = _months(win)
    if side == "low":
        hits = sum(1 for lo, _hi in months.values() if lo <= reading)
    else:
        hits = sum(1 for _lo, hi in months.values() if hi >= reading)
    n = len(months)
    if hits == 0:
        return {"side": side, "months": n, "hits": 0, "every_months": None, "label": "beyond_10y"}
    every = n / hits
    label = None
    for p, name in PERIODS:
        if every >= p:
            label = name
    return {"side": side, "months": n, "hits": hits, "every_months": round(every, 1), "label": label}


def _episodes(indices):
    count, last = 0, None
    for i in indices:
        if last is None or i - last > EPISODE_GAP:
            count += 1
        last = i
    return count


def odds(reading, series, side=None):
    """{"side", "days", "episodes", "3m": {...}, "6m": {...}} where each horizon
    has "win_rate" (share of outcomes in the side's favour: up after a low,
    down after a high), "median_return" (%), "n"."""
    win = _window(series)
    side = side or side_of(reading, series)
    closes = [c for _d, c, _s in win]
    hit = [i for i, (_d, _c, s) in enumerate(win)
           if (s <= reading if side == "low" else s >= reading)]
    out = {"side": side, "days": len(hit), "episodes": _episodes(hit)}
    for name, h in HORIZONS.items():
        rets = []
        for i in hit:
            a, b = i + EXEC_LAG, i + EXEC_LAG + h
            if b < len(closes):
                rets.append((closes[b] / closes[a] - 1) * 100.0)
        if not rets:
            out[name] = {"win_rate": None, "median_return": None, "n": 0}
            continue
        rets.sort()
        good = sum(1 for r in rets if (r > 0 if side == "low" else r < 0))
        out[name] = {"win_rate": round(good / len(rets) * 100.0, 1),
                     "median_return": round(rets[len(rets) // 2], 2),
                     "n": len(rets)}
    return out


def _quantile(vals, p):
    vals = sorted(vals)
    x = p * (len(vals) - 1)
    lo = int(x)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (x - lo)


def reference_table(series):
    """[{"side", "period", "threshold", **odds}] -- for each standard return
    period, the reading a month has to reach to be that rare, and the odds
    after readings at least that extreme."""
    months = _months(_window(series))
    lows = [lo for lo, _hi in months.values()]
    highs = [hi for _lo, hi in months.values()]
    rows = []
    for side, vals in (("low", lows), ("high", highs)):
        for p, name in PERIODS:
            share = min(1.0, 1.0 / p) if p <= len(vals) else 1.0 / len(vals)
            thr = _quantile(vals, share if side == "low" else 1 - share)
            o = odds(thr, series, side)
            rows.append({"side": side, "period": name, "threshold": round(thr, 1), **o})
    return rows


def sleeve_plan(target_weight, reserve, n_funds, invested=0.0, triggered=False):
    """Money view of a target satellite weight. `reserve` is the flexible
    cash sleeve, `invested` how much of it is currently in the funds (above
    the core holdings, which this never touches). Orders are only proposed on
    a trigger day: the rule acts on first crossings, so a gap between the
    target and what is actually invested on a quiet day is shown, not traded
    -- e.g. no topping up the sleeve while the state is "Optimistic (trim)"."""
    target = round(target_weight * reserve, 2)
    gap = round(target - invested, 2)
    action = "hold"
    if triggered and abs(gap) >= 0.005 * reserve:
        action = "buy" if gap > 0 else "sell"
    order = gap if action != "hold" else 0.0
    return {"reserve": reserve, "target_invested": target, "target_cash": round(reserve - target, 2),
            "currently_invested": invested, "gap_to_target": gap, "action": action,
            "order_total": order, "order_per_fund": round(order / n_funds, 2),
            "target_per_fund": round(target / n_funds, 2)}


def series_from_history(rows, smooth_n=5):
    """[(date, close, 5-day-smoothed ASI)] from history.load()-style rows."""
    out, buf = [], []
    for r in rows:
        if r.get("asi_v2") is not None:
            buf.append(r["asi_v2"])
            if len(buf) > smooth_n:
                buf.pop(0)
        out.append((r["date"], r["close"], sum(buf) / len(buf) if buf else None))
    return out
