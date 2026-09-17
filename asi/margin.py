# -*- coding: utf-8 -*-
"""
Margin (financing + securities-lending) balance -- nationwide total (SSE +
SZSE combined), via akshare `stock_margin_account_info` (East Money data
center; one call returns the entire history, from 2012-09-27).

Use: the 5-day change rate feeds the "leverage sentiment" dimension --
2015's crash-style forced-deleveraging stampede and the 2024-02 snowball/DMA
crisis both left an early footprint in how fast the margin balance collapsed.
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


# The committed CSV only advances when someone reruns fetch_and_save() and
# commits it. Days after its last row come straight from the same East Money
# report akshare wraps (RPTA_WEB_MARGIN_DAILYTRADE.FIN_BALANCE -- identical
# values, e.g. 2026-09-01 = 26369.05565241), no akshare needed.
# A balance older than this many calendar days is not forward-filled.
MAX_STALE_DAYS = 4


def _live_tail(since):
    try:
        rows = em_datacenter("RPTA_WEB_MARGIN_DAILYTRADE",
                             "STATISTICS_DATE,FIN_BALANCE", "STATISTICS_DATE", since)
    except Exception:                                # noqa: BLE001
        return {}
    return {d: float(r["FIN_BALANCE"]) for d, r in rows
            if r.get("FIN_BALANCE") is not None}


def load_balance(live=True):
    """{date: margin balance (100M CNY)}, in chronological order."""
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
                out[r["date"]] = float(r["margin_balance"])
            except (KeyError, ValueError):
                continue
    if live and out:
        out.update(_live_tail(max(out)))
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

    def ffill(d):
        # Never carry a balance more than MAX_STALE_DAYS forward: once the
        # data stops, a flat forward-fill reads as a genuine 0% change and
        # silently biases the leverage dimension. Missing is honest --
        # compute() renormalizes and coverage shows it.
        j = bisect.bisect_right(bkeys, d) - 1
        if j < 0 or days_between(bkeys[j], d) > MAX_STALE_DAYS:
            return None
        return bal[bkeys[j]]

    vals = [ffill(d) for d in dates]
    out = {}
    for i in range(5, len(dates)):
        v, v0 = vals[i], vals[i - 5]
        if v is not None and v0 is not None and v0 > 0:
            out[dates[i]] = round((v / v0 - 1) * 100.0, 3)
    return out
