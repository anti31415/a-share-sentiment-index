# -*- coding: utf-8 -*-
"""
Data-fetching layer. Three sources, all free public endpoints, all disk-cached:

  * Sina CN_MarketData.getKLineData -- daily bars, [unadjusted], back to 2001.
      Unadjusted matters: only raw prices can be compared directly against
      annual-report book value per share to compute PB.
  * East Money datacenter-web RPT_F10_..  -- per-stock annual-report BPS.
  * East Money push2 clist                -- full A-share market listing +
      current PB, giving a [measured] below-book-value denominator that
      replaces v1's hardcoded TOTAL_A_SHARES = 5400.
"""
import json, os, random, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

from net import fetch, cached_json                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

SINA = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=6000")


# ---------------------------------------------------------------- index / stock daily bars
def _sina_kline(sym):
    s = fetch(SINA % sym)
    try:
        rows = json.loads(s)
    except Exception:                                # noqa: BLE001
        return []
    if not isinstance(rows, list):
        return []
    return [[r["day"], float(r["close"]), float(r["volume"])] for r in rows
            if r.get("close") and float(r["close"]) > 0]


def index_daily(sym="sh000001"):
    """[[date, close, volume], ...] in chronological order."""
    return cached_json("idx_" + sym, lambda: _sina_kline(sym))


def stock_daily(sym):
    """Per-stock [unadjusted] [[date, close, volume], ...]."""
    return cached_json("k_" + sym, lambda: _sina_kline(sym))


# ---------------------------------------------------------------- full market listing
def universe():
    """[[code, mkt, name, pb], ...] -- full A-share listing (Sina hs_a node).
    Both the numerator and denominator of the below-book-value rate are now
    [measured], replacing v1's hardcoded TOTAL_A_SHARES = 5400."""
    def go():
        base = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                "Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=hs_a")
        rows, pn = [], 1
        while True:
            txt = fetch(base % pn, retries=8,
                        headers={"Referer": "https://vip.stock.finance.sina.com.cn/"})
            try:
                page = json.loads(txt)
            except Exception:                        # noqa: BLE001
                break
            if not page:
                break
            for x in page:
                mkt = 1 if x["symbol"].startswith("sh") else 0
                pb = x.get("pb")
                rows.append([x["code"], mkt, x["name"],
                             float(pb) if pb not in (None, "", 0) else None])
            pn += 1
            time.sleep(0.15)
            if pn > 80:
                break
        return rows
    return cached_json("universe", go)


def sina_symbol(code, mkt):
    if code[:2] in ("92", "43", "83", "87", "88"):
        return "bj" + code
    return ("sh" if mkt == 1 else "sz") + code


# ---------------------------------------------------------------- annual-report BPS
def stock_bps(code, mkt):
    """[[report_date, bps], ...] in chronological order (annual reports only).
    NOTE: the API `filter` string below sends the literal Chinese value
    "年报" (meaning "annual report") -- this is a required request parameter
    for the East Money API, not narrative text, and must not be translated."""
    if mkt == 1:
        sfx = "SH"
    elif code[:1] in ("4", "8") or code[:2] == "92":
        sfx = "BJ"
    else:
        sfx = "SZ"

    def go():
        q = {"reportName": "RPT_F10_FINANCE_MAINFINADATA",
             "columns": "REPORT_DATE,BPS",
             "filter": '(SECUCODE="%s.%s")(REPORT_TYPE="年报")' % (code, sfx),
             "pageNumber": "1", "pageSize": "60",
             "sortColumns": "REPORT_DATE", "sortTypes": "1",
             "source": "HSF10", "client": "PC"}
        s = fetch("https://datacenter-web.eastmoney.com/api/data/v1/get?"
                  + urllib.parse.urlencode(q), retries=4)
        res = json.loads(s).get("result")
        if not res or not res.get("data"):
            return []
        return [[r["REPORT_DATE"][:10], float(r["BPS"])]
                for r in res["data"] if r.get("BPS") is not None]
    return cached_json("bps_" + code, go)


# ---------------------------------------------------------------- sampling and concurrency
def sample_universe(n, seed=20260902, exclude_st=False, code_prefix=None):
    """
    Fixed-seed random sample -- fully reproducible. Excludes the Beijing
    Stock Exchange (too short a history).
      exclude_st  : drop stocks whose current name contains ST/*ST (a partial
                    fix related to the survivorship-bias point in the code
                    review -- this can only filter by [current] ST status,
                    not year-by-year historical ST status, so it should only
                    be used for a recent ~5-year analysis window, not applied
                    to the full 2005-2026 sample -- otherwise stocks that were
                    genuinely ST'd or delisted years ago get misjudged as
                    "never ST'd" based on today's name.
      code_prefix : keep only stocks whose code starts with one of these
                    prefixes, for sector sampling (e.g. "300","301" = ChiNext).
    """
    u = [r for r in universe()
         if r[0][:1] in "036" and not (r[0][:2] == "92")]
    if exclude_st:
        u = [r for r in u if "ST" not in r[2].upper()]
    if code_prefix:
        u = [r for r in u if any(r[0].startswith(p) for p in code_prefix)]
    u.sort()
    random.Random(seed).shuffle(u)
    return u[:n]


def bulk(fn, items, workers=8, label=""):
    done = [0]

    def run(it):
        try:
            r = fn(it)
        except Exception:                            # noqa: BLE001
            r = None
        done[0] += 1
        if done[0] % 50 == 0:
            print("      %s %d/%d" % (label, done[0], len(items)), flush=True)
        return r
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(run, items))
