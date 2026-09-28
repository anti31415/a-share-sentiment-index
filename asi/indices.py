# -*- coding: utf-8 -*-
"""
The ASI read through seven major indices.

Each index gets its own daily ASI, 5-day smoothing, overheating score, signal
state, and ten-year rarity / odds -- but only part of the model can actually
be index-specific with free data:

  per index    vol_temp, rsi14, bias60, drawdown (price and volume of the
               index itself) and the whole of heat.py (RSI, BIAS60, index
               volume; the financing-buy input is market-wide)
  market-wide  below-book rate, ERP, QVIX, margin balance / buy ratio, limit
               pools -- there is no free per-index source for these, so every
               index shares the same valuation, leverage and speculation
               backdrop (~83% of the score's weight)

So the seven ASI lines move together; what separates them is the
index-specific price action, the overheating trim, and above all the rarity
and odds, which are measured on each index's own history and forward
returns. The satellite-sleeve signal the strategy was backtested on is the
Shanghai Composite line (sh000001), identical to check_signal.py.

    python asi/indices.py [--reserve AMOUNT --funds N [--invested AMOUNT]] > out.json

With --reserve, the primary index's entry also carries `sleeve_plan`, the
money view of its target weight (see guidance.sleeve_plan and
check_signal.py, whose flags these mirror).
"""
import argparse
import json
import sys

import actionable as A
import fetch
import guidance as G
import heat as heat_mod
import history
import run_daily
import series
from compute import compute, InsufficientData

INDICES = [
    ("sh000001", "SSE Composite"),
    ("sz399001", "SZSE Component"),
    ("sz399006", "ChiNext"),
    ("sh000300", "CSI 300"),
    ("sh000680", "STAR Composite"),
    ("sh000016", "SSE 50"),
    ("sz399005", "SME 100"),
]
PRIMARY = "sh000001"
INDEX_KEYS = ("vol_temp", "rsi14", "bias60", "drawdown")
MARKET_KEYS = tuple(k for k in history.V2_KEYS if k not in INDEX_KEYS)
SMOOTH_N = 5
MAX_WINDOW = 90


def market_inputs():
    """{date: {market-wide input: value}} -- the committed history, with every
    day since its last row re-fetched live (as check_signal.py does)."""
    rows = history.load()
    out = {r["date"]: {k: r.get(k) for k in MARKET_KEYS} for r in rows}
    idx_dates = [r[0] for r in fetch.index_daily()]
    through = rows[-1]["date"] if rows else ""
    gap = sum(1 for d in idx_dates[-MAX_WINDOW:] if d > through)
    window = min(max(15, gap + SMOOTH_N + 1), MAX_WINDOW)
    _d, _vals, _ip, _bb, recent = run_daily.today_values(recent_n=window)
    for r in recent:
        if r["date"] > through:
            out[r["date"]] = {k: r.get(k) for k in MARKET_KEYS}
    return out


def trailing_mean(vals, n=SMOOTH_N):
    out, buf = [], []
    for v in vals:
        if v is not None:
            buf.append(v)
            if len(buf) > n:
                buf.pop(0)
        out.append(sum(buf) / len(buf) if buf and v is not None else None)
    return out


def build_index(sym, market):
    idx = fetch.index_daily(sym)
    panel = series.index_panel(idx)
    heat = heat_mod.series_by_date(idx)
    days = []
    for rec in panel:
        m = market.get(rec["date"])
        if m is None:
            continue
        vals = dict(m)
        vals.update({k: rec.get(k) for k in INDEX_KEYS})
        try:
            res = compute(vals, require_valuation=False)
            score, cov = res["score"], res["coverage"]
        except InsufficientData:
            score, cov = None, None
        days.append({"date": rec["date"], "close": rec["close"], "asi": score, "coverage": cov,
                     "heat": heat.get(rec["date"]),
                     "missing": [k for k in history.V2_KEYS if vals.get(k) is None]})
    sm = trailing_mean([d["asi"] for d in days])
    machine = A.StateMachine()
    for d, s in zip(days, sm):
        d["sm"] = s
        prior = machine.state
        d["state"], d["weight"], d["reason"] = machine.step(s, d["heat"], d["close"])
        d["prior_state"] = prior
        d["trim_by"] = machine.trim_by if d["state"] == A.TRIM else None
    return days


def summarize(sym, name, days, ytd_from):
    last = days[-1]
    prev = days[-2] if len(days) > 1 else last
    g_series = [(d["date"], d["close"], d["sm"]) for d in days]
    side = G.side_of(last["sm"], g_series)
    return {
        "symbol": sym, "name": name, "primary": sym == PRIMARY,
        "date": last["date"], "close": round(last["close"], 2),
        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2),
        "asi_today": last["asi"], "asi_smoothed_5d": round(last["sm"], 2),
        "coverage": last["coverage"], "missing_inputs": last["missing"],
        "heat": round(last["heat"], 3) if last["heat"] is not None else None,
        "prior_state": last["prior_state"], "state": last["state"],
        "satellite_target_weight": last["weight"],
        "triggered": last["state"] != last["prior_state"], "trigger_reason": last["reason"],
        "trim_by": last["trim_by"],
        "history_start": days[0]["date"],
        "rarity": G.rarity(last["sm"], g_series, side),
        "odds": G.odds(last["sm"], g_series, side),
        "ytd": [{"date": d["date"], "close": round(d["close"], 2), "asi_sm": round(d["sm"], 2),
                 "heat": round(d["heat"], 3) if d["heat"] is not None else None, "state": d["state"]}
                for d in days if d["date"] >= ytd_from and d["sm"] is not None],
        "transitions_ytd": [{"date": d["date"], "from": d["prior_state"], "to": d["state"], "reason": d["reason"]}
                            for d in days if d["date"] >= ytd_from and d["state"] != d["prior_state"]],
    }


def run(reserve=None, funds=1, invested=None):
    market = market_inputs()
    out = []
    for sym, name in INDICES:
        days = build_index(sym, market)
        if not days:
            continue
        year = days[-1]["date"][:4]
        out.append(summarize(sym, name, days, year + "-01-01"))
    if reserve:
        for s in out:
            if s["primary"]:
                inv = invested if invested is not None else A.STATE_WEIGHT[s["prior_state"]] * reserve
                s["sleeve_plan"] = G.sleeve_plan(s["satellite_target_weight"], reserve, funds, inv, s["triggered"])
                s["sleeve_plan"]["invested_assumed"] = invested is None
    return {"as_of": max(s["date"] for s in out), "indices": out}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Seven-index ASI.")
    p.add_argument("--reserve", type=float)
    p.add_argument("--funds", type=int, default=1)
    p.add_argument("--invested", type=float)
    a = p.parse_args()
    json.dump(run(a.reserve, a.funds, a.invested), sys.stdout, ensure_ascii=False)
