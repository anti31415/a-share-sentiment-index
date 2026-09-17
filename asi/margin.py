# -*- coding: utf-8 -*-
"""
Margin (financing + securities-lending) balance -- nationwide total (SSE +
SZSE combined), via akshare `stock_margin_account_info` (East Money data
center; one call returns the entire history, from 2012-09-27).

Three derived series feed the "leverage sentiment" dimension:
  1. margin_chg5      -- 5-day change of the financing-only balance (the
                          original member; 2015's crash-style forced
                          deleveraging and the 2024-02 snowball/DMA crisis
                          both left an early footprint here).
  2. two_margin_chg5   -- same, but on financing + securities-lending
                          combined ("两融余额"), a slightly broader read on
                          total leveraged exposure.
  3. margin_buy_ratio  -- today's financing purchase amount as a % of the
                          prior day's financing balance ("融资买入占比"):
                          how aggressively new leveraged money is buying
                          relative to the money already outstanding, a more
                          activity-sensitive (less inertial) signal than the
                          balance level itself. Deliberately defined against
                          the financing balance rather than whole-market
                          turnover -- akshare/Sina's index klines only carry
                          share volume, not turnover value in yuan, so a
                          true buy-amount/market-turnover ratio isn't
                          reconstructable from what this project already
                          fetches.
"""
import bisect
import csv
import os

from net import days_between, em_datacenter

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "data", "margin_account_info.csv")


def fetch_and_save():
    import warnings
    import urllib3
    urllib3.disable_warnings()
    import requests
    old_request = requests.Session.request

    def patched(self, *a, **kw):
        kw.setdefault("timeout", 25)
        kw["verify"] = False
        return old_request(self, *a, **kw)
    requests.Session.request = patched
    import akshare as ak

    df = ak.stock_margin_account_info()
    # akshare returns Chinese column names; keep only the four this project
    # actually uses and rename them to English right away so nothing
    # downstream has to know about the source library's native names.
    df = df.rename(columns={
        "日期": "date",
        "融资余额": "margin_balance",
        "融券余额": "sec_lending_balance",
        "融资买入额": "financing_buy_amount",
    })
    df = df[["date", "margin_balance", "sec_lending_balance", "financing_buy_amount"]]
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date")
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return len(df)


# The committed CSV only advances when someone reruns fetch_and_save() and
# commits it. Days after its last row come straight from the same East Money
# report akshare wraps (RPTA_WEB_MARGIN_DAILYTRADE -- identical values, e.g.
# 2026-09-01 FIN_BALANCE = 26369.05565241), no akshare needed.
# A value older than this many calendar days is not forward-filled.
MAX_STALE_DAYS = 4


def _live_tail(since):
    try:
        rows = em_datacenter("RPTA_WEB_MARGIN_DAILYTRADE",
                             "STATISTICS_DATE,FIN_BALANCE,LOAN_BALANCE,FIN_BUY_AMT",
                             "STATISTICS_DATE", since)
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for d, r in rows:
        if None in (r.get("FIN_BALANCE"), r.get("LOAN_BALANCE"), r.get("FIN_BUY_AMT")):
            continue
        out[d] = {"margin_balance": float(r["FIN_BALANCE"]),
                  "sec_lending_balance": float(r["LOAN_BALANCE"]),
                  "financing_buy_amount": float(r["FIN_BUY_AMT"])}
    return out


def load_all(live=True):
    """{date: {margin_balance, sec_lending_balance, financing_buy_amount}}
    (all in 100M CNY), chronological."""
    if not os.path.exists(CSV_PATH):
        try:
            fetch_and_save()
        except Exception:                            # noqa: BLE001  (akshare absent)
            pass
    out = {}
    if not os.path.exists(CSV_PATH):
        return _live_tail("2012-01-01") if live else out
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out[r["date"]] = {
                    "margin_balance": float(r["margin_balance"]),
                    "sec_lending_balance": float(r["sec_lending_balance"]),
                    "financing_buy_amount": float(r["financing_buy_amount"]),
                }
            except (KeyError, ValueError):
                continue
    if live and out:
        out.update(_live_tail(max(out)))
    return out


def load_balance(live=True):
    """{date: financing-only balance (100M CNY)}."""
    return {d: v["margin_balance"] for d, v in load_all(live).items()}


def _ffill_series(series_by_date, dates):
    keys = sorted(series_by_date)
    if not keys:
        return [None] * len(dates)

    def ffill(d):
        # Never carry a value more than MAX_STALE_DAYS forward: once the data
        # stops, a flat forward-fill reads as a genuine 0% change and silently
        # biases the leverage dimension. Missing is honest -- compute()
        # renormalizes and coverage shows it.
        j = bisect.bisect_right(keys, d) - 1
        if j < 0 or days_between(keys[j], d) > MAX_STALE_DAYS:
            return None
        return series_by_date[keys[j]]
    return [ffill(d) for d in dates]


def chg5_series(dates):
    """{date: 5-day change % of the financing-only balance}. No lookahead --
    day i uses balance[i] / balance[i-5] - 1 on the forward-filled series."""
    bal = load_balance()
    vals = _ffill_series(bal, dates)
    out = {}
    for i in range(5, len(dates)):
        v, v0 = vals[i], vals[i - 5]
        if v is not None and v0 is not None and v0 > 0:
            out[dates[i]] = round((v / v0 - 1) * 100.0, 3)
    return out


def two_margin_chg5_series(dates):
    """{date: 5-day change % of financing + securities-lending balance combined}."""
    m = load_all()
    combined = {d: v["margin_balance"] + v["sec_lending_balance"] for d, v in m.items()}
    vals = _ffill_series(combined, dates)
    out = {}
    for i in range(5, len(dates)):
        v, v0 = vals[i], vals[i - 5]
        if v is not None and v0 is not None and v0 > 0:
            out[dates[i]] = round((v / v0 - 1) * 100.0, 3)
    return out


def buy_ratio_series(dates):
    """{date: today's financing buy amount / prior trading day's financing
    balance, as a %}."""
    m = load_all()
    keys = sorted(m)
    bal_vals = _ffill_series({d: v["margin_balance"] for d, v in m.items()}, dates)
    buy_vals = _ffill_series({d: v["financing_buy_amount"] for d, v in m.items()}, dates)
    out = {}
    for i in range(1, len(dates)):
        buy, prior_bal = buy_vals[i], bal_vals[i - 1]
        if buy is not None and prior_bal is not None and prior_bal > 0:
            out[dates[i]] = round(buy / prior_bal * 100.0, 3)
    return out
