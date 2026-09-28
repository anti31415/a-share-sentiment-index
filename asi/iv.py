# -*- coding: utf-8 -*-
"""
Implied volatility ("fear gauge") for the "trading cooldown" dimension --
SSE 50ETF options QVIX (China's rough analogue of the CBOE VIX), via akshare
`index_option_50etf_qvix`. One call returns the full history: SSE 50ETF
options launched 2015-02-09, so this covers 2015-02 onward -- essentially
the project's entire usable window, no 5-year cutoff needed unlike ERP/margin.

High IV = the options market is pricing in large future moves, which in
practice on the A-share market means fear/panic, not euphoria (2015-08-26's
Black Monday aftermath is the all-time QVIX high at 63.79; the calmest,
most complacent read was 8.31 on 2017-05-11, deep in a slow grinding bull).
So direction is "neg" like drawdown/broken_net_rate: rising IV -> falling
sentiment score.
"""
import bisect
import csv
import os

from net import days_between, fetch

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "data", "qvix_50etf.csv")


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

    df = ak.index_option_50etf_qvix()
    df["date"] = df["date"].astype(str)
    df = df[["date", "close"]].rename(columns={"close": "qvix"})
    df = df.dropna().sort_values("date")
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return len(df)


# The committed CSV only advances when fetch_and_save() is rerun and committed.
# Newer days come from the same optbbs CSV akshare reads (columns 0-4 are the
# 50ETF QVIX date/open/high/low/close), stdlib only. A value older than this
# many calendar days is not forward-filled -- a stale IV would otherwise read
# as a perfectly calm, unchanging market.
LIVE_URL = "http://1.optbbs.com/d/csv/d/k.csv"
MAX_STALE_DAYS = 4


def _live_rows():
    try:
        raw = fetch(LIVE_URL, retries=4)
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for line in raw.splitlines()[1:]:
        cells = line.split(",")
        if len(cells) < 5:
            continue
        try:
            y, m, d = (int(x) for x in cells[0].split("/"))
            out["%04d-%02d-%02d" % (y, m, d)] = float(cells[4])
        except ValueError:
            continue
    return out


def load(live=True):
    """{date: QVIX close}, chronological."""
    out = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    out[r["date"]] = float(r["qvix"])
                except (KeyError, ValueError):
                    continue
    if live:
        last = max(out) if out else ""
        out.update({d: v for d, v in _live_rows().items() if d > last})
    return out


def daily_series(dates, live=True):
    """{date: QVIX}, forward-filled onto the given trading-day list (no lookahead --
    only ever carries the most recent [past] value forward, and never more
    than MAX_STALE_DAYS)."""
    qvix = load(live)
    keys = sorted(qvix)
    if not keys:
        return {}
    out = {}
    for d in dates:
        j = bisect.bisect_right(keys, d) - 1
        if j >= 0 and days_between(keys[j], d) <= MAX_STALE_DAYS:
            out[d] = qvix[keys[j]]
    return out
