# -*- coding: utf-8 -*-
"""
One-off fetch for the two source files erp.py reads: data/cn10y_5y.csv (the
10-year government bond yield) and data/hs300_pe_5y.csv (CSI300's weighted
TTM PE). Both go through akshare, so `pip install akshare py_mini_racer` is
required to run this (see requirements.txt) -- nothing else in the project
needs those two packages.

    python asi/fetch_erp_data.py

Safe to re-run; it overwrites both CSVs with a fresh pull from 2021-09-01 to today.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def _patch_requests_for_akshare():
    """akshare's own TLS verification trips on some corporate/regional
    networks; this disables verification for akshare's calls only, the same
    way margin.py does it."""
    import urllib3
    urllib3.disable_warnings()
    import requests
    old_request = requests.Session.request

    def patched(self, *a, **kw):
        kw.setdefault("timeout", 25)
        kw["verify"] = False
        return old_request(self, *a, **kw)
    requests.Session.request = patched


def fetch_10y_yield(start="20210901"):
    """China 10-year government bond yield curve, daily. The upstream API
    limits how wide a single date range can be, so this pulls one year at a
    time and concatenates."""
    _patch_requests_for_akshare()
    import akshare as ak
    import pandas as pd
    import time

    from datetime import date
    end_year = date.today().year
    start_year = int(start[:4])
    chunks = []
    for y in range(start_year, end_year + 1):
        s = start if y == start_year else "%d0101" % y
        e = "%d1231" % y if y < end_year else date.today().strftime("%Y%m%d")
        df = ak.bond_china_yield(start_date=s, end_date=e)
        sub = df[df["曲线名称"] == "中债国债收益率曲线"][["日期", "10年"]].copy()
        chunks.append(sub)
        time.sleep(0.3)
    all_df = pd.concat(chunks).drop_duplicates("日期").sort_values("日期")
    all_df.columns = ["date", "y10"]
    out = os.path.join(DATA, "cn10y_5y.csv")
    all_df.to_csv(out, index=False, encoding="utf-8-sig")
    print("Wrote %s: %d rows, %s -> %s" % (out, len(all_df), all_df["date"].min(), all_df["date"].max()))


def fetch_hs300_pe(start="2021-08-01"):
    """CSI300's cap-weighted TTM PE, monthly, from legulegu's index-basic-pe
    endpoint. Uses the [addTtmPe] column, NOT the plain arithmetic-mean
    column -- see the note in erp.py for why."""
    _patch_requests_for_akshare()
    import requests
    import py_mini_racer
    import pandas as pd
    from datetime import datetime
    from akshare.stock_feature.stock_a_pe_and_pb import hash_code
    from akshare.stock_feature.stock_a_indicator import get_cookie_csrf

    js = py_mini_racer.MiniRacer()
    js.eval(hash_code)
    token = js.call("hex", datetime.now().date().isoformat()).lower()
    url = "https://legulegu.com/api/stockdata/index-basic-pe"
    r = requests.get(url, params={"token": token, "indexCode": "000300.SH"},
                      **get_cookie_csrf(url="https://legulegu.com/stockdata/sz50-ttm-lyr"))
    df = pd.DataFrame(r.json()["data"])
    df = df[df["date"] >= start][["date", "addTtmPe", "addTtmPeQuantile"]]
    df.columns = ["date", "pe_ttm", "pe_q"]
    out = os.path.join(DATA, "hs300_pe_5y.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("Wrote %s: %d rows, %s -> %s" % (out, len(df), df["date"].min(), df["date"].max()))


if __name__ == "__main__":
    fetch_10y_yield()
    fetch_hs300_pe()
