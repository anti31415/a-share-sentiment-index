# -*- coding: utf-8 -*-
"""
Data-fetching layer. All free public endpoints, all disk-cached:

  * Sina CN_MarketData.getKLineData -- daily bars, [unadjusted], back to 2001.
      Tencent kline and East Money push2his (fqt=0) serve the same unadjusted
      closes and are the failovers; the daily check rotates across all three.
      Unadjusted matters: only raw prices can be compared directly against
      annual-report book value per share to compute PB.
  * East Money datacenter-web RPT_F10_..  -- per-stock annual-report BPS.
  * Sina hs_a node / East Money push2 clist (failover) -- full A-share market listing +
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
TENCENT = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,%d"
TENCENT_MAX = 2000        # longer requests come back with an empty payload
EASTMONEY = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
             "&fields1=f1&fields2=f51,f53,f56&klt=101&fqt=0&lmt=%d&end=20500101")


class NoData(RuntimeError):
    """Every source came back empty or failed. Raised rather than returning []
    so cached_json() never persists an empty result as if it were real."""


def _sina_kline(sym, n=6000):
    s = fetch(SINA.replace("datalen=6000", "datalen=%d" % n) % sym, retries=2)
    rows = json.loads(s)
    if not isinstance(rows, list):
        return []
    return [[r["day"], float(r["close"]), float(r["volume"])] for r in rows
            if r.get("close") and float(r["close"]) > 0]


def _tencent_kline(sym, n=TENCENT_MAX):
    """Tencent unadjusted daily bars. Row = [date, open, close, high, low,
    volume-in-lots]; volume x100 -> shares, matching Sina's units."""
    s = fetch(TENCENT % (sym, min(n, TENCENT_MAX)), retries=2)
    d = (json.loads(s).get("data") or {})
    d = d.get(sym) if isinstance(d, dict) else None
    rows = (d or {}).get("day") or []
    return [[r[0], float(r[2]), float(r[5]) * 100.0] for r in rows
            if len(r) >= 6 and float(r[2]) > 0]


def _eastmoney_kline(sym, n=6000):
    """East Money unadjusted (fqt=0) daily bars: "date,close,volume-in-lots"."""
    secid = ("1." if sym[:2] == "sh" else "0.") + sym[2:]
    s = fetch(EASTMONEY % (secid, n), retries=2)
    rows = ((json.loads(s).get("data") or {}).get("klines")) or []
    out = []
    for line in rows:
        d, c, v = line.split(",")[:3]
        if float(c) > 0:
            out.append([d, float(c), float(v) * 100.0])
    return out


SOURCES = [_sina_kline, _eastmoney_kline, _tencent_kline]   # full-history sources first


def _kline(sym, n, rotate=False):
    """Daily bars with source failover (all three serve identical unadjusted
    closes). Every one of these free endpoints blocks a burst of concurrent
    requests from one IP -- Sina with HTTP 456, Tencent with 501 -- and a
    blocked source used to crash the daily check. With rotate=True the first
    source is picked per symbol, so a 900-stock burst is split ~300/300/300
    instead of landing on one host."""
    order = list(SOURCES)
    if rotate:
        k = sum(map(ord, sym)) % len(order)
        order = order[k:] + order[:k]
    errs = []
    for src in order:
        try:
            rows = src(sym, n)
        except Exception as e:                       # noqa: BLE001
            errs.append("%s: %r" % (src.__name__, e))
            continue
        if rows:
            return rows[-n:]
        errs.append("%s: empty" % src.__name__)
    raise NoData("no daily bars for %s (%s)" % (sym, "; ".join(errs)))


def index_daily(sym="sh000001"):
    """[[date, close, volume], ...] in chronological order."""
    return cached_json("idx_" + sym, lambda: _kline(sym, 6000))


def stock_daily(sym):
    """Per-stock [unadjusted] [[date, close, volume], ...]."""
    return cached_json("k_" + sym, lambda: _kline(sym, 6000))


def stock_recent(sym, n=400):
    """Last `n` unadjusted bars only -- enough for the daily check (252-day
    new-low window + a 90-day re-score window). The first source rotates, which spreads the
    ~900-request burst across all three sources (see _kline). Separate cache
    key so a short series never shadows stock_daily()."""
    return cached_json("kr%d_%s" % (n, sym), lambda: _kline(sym, n, rotate=True))


# ---------------------------------------------------------------- full market listing
def universe():
    """[[code, mkt, name, pb], ...] -- full A-share listing (Sina hs_a node).
    Both the numerator and denominator of the below-book-value rate are now
    [measured], replacing v1's hardcoded TOTAL_A_SHARES = 5400."""
    def sina():
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

    def eastmoney():
        # f12 code, f13 market (1=SH, 0=SZ), f14 name, f23 PB(MRQ). Same
        # Shanghai+Shenzhen A-share scope as Sina's hs_a node.
        base = ("https://%s.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=0&np=1"
                "&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                "&fields=f12,f13,f14,f23")
        hosts = ["82.push2", "push2", "push2delay"]  # the bare push2 host often 502s

        def page_of(pn):
            for h in list(hosts):
                try:
                    return json.loads(fetch(base % (h, pn), retries=2)).get("data") or {}
                except Exception:                    # noqa: BLE001
                    hosts.remove(h)
                    hosts.append(h)                  # demote a failing host
            raise NoData("East Money clist page %d failed on every host" % pn)

        rows, pn = [], 1
        while pn <= 80:
            data = page_of(pn)
            page = data.get("diff") or []
            if not page:
                break
            for x in page:
                pb = x.get("f23")
                rows.append([x["f12"], 1 if x["f13"] == 1 else 0, x["f14"],
                             float(pb) if isinstance(pb, (int, float)) and pb != 0 else None])
            if len(rows) >= data.get("total", 0):
                break
            pn += 1
            time.sleep(0.1)
        return rows

    def go():
        # A full listing is ~5,500 names; a short one means paging broke off
        # part-way (block / timeout) and must not become the denominator.
        for src in (sina, eastmoney):
            try:
                rows = src()
            except Exception:                        # noqa: BLE001
                continue
            if len(rows) >= 4000:
                return rows
        raise NoData("full A-share listing unavailable from Sina and East Money")
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
        res = json.loads(s).get("result")        # a failure raises, so it isn't cached as "no BPS"
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
        if label and done[0] % 50 == 0:
            print("      %s %d/%d" % (label, done[0], len(items)), flush=True)
        return r
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(run, items))
