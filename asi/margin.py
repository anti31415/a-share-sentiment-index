# -*- coding: utf-8 -*-
"""
Margin (financing + securities-lending) balance -- nationwide total (SSE +
SZSE combined), via akshare `stock_margin_account_info` (East Money data
center; one call returns the entire history, from 2012-09-27).

Use: the 5-day change rate feeds the "leverage sentiment" dimension --
2015's crash-style forced-deleveraging stampede and the 2024-02 snowball/DMA
crisis both left an early footprint in how fast the margin balance collapsed.
"""
import csv
import os

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
    # akshare returns Chinese column names ("日期" = date, "融资余额" = margin
    # balance, plus a dozen other columns this project doesn't use); keep
    # only the two we need and rename them to English right away so nothing
    # downstream has to know about the source library's native column names.
    df = df.rename(columns={"日期": "date", "融资余额": "margin_balance"})
    df = df[["date", "margin_balance"]]
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date")
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return len(df)


def load_balance():
    """{date: margin balance (100M CNY)}, in chronological order."""
    if not os.path.exists(CSV_PATH):
        fetch_and_save()
    out = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out[r["date"]] = float(r["margin_balance"])
            except (KeyError, ValueError):
                continue
    return out


def chg5_series(dates):
    """
    Given a chronological list of trading days, returns {date: 5-day change %}.
    Uses only [past] data, no lookahead -- day i uses balance[i] / balance[i-5] - 1,
    where `balance` is the actual date-sorted series from load_balance(),
    not aligned to a trading calendar; the occasional holiday / data gap is
    forward-filled with the most recent available value.
    """
    bal = load_balance()
    bkeys = sorted(bal)
    if not bkeys:
        return {}
    import bisect

    def ffill(d):
        j = bisect.bisect_right(bkeys, d) - 1
        return bal[bkeys[j]] if j >= 0 else None

    vals = [ffill(d) for d in dates]
    out = {}
    for i in range(5, len(dates)):
        v, v0 = vals[i], vals[i - 5]
        if v is not None and v0 is not None and v0 > 0:
            out[dates[i]] = round((v / v0 - 1) * 100.0, 3)
    return out
