# -*- coding: utf-8 -*-
"""
Full-market aggregate statistics read directly from public endpoints, so the
daily check no longer has to reconstruct them from a ~900-stock sample.

  below_book_series()  -- share of all A-shares trading below book value,
                          legulegu, one call returns 2005-01 onward. Replaces
                          both the live full-listing count and the historical
                          sample/BPS reconstruction, so history and the live
                          reading share one definition.
  limit_pools(dates)   -- East Money's limit-up and limit-down pools: counts
                          plus the longest active limit-up streak. Only the
                          last ~20 trading days are served.

Stdlib only (the cloud routine has no akshare); the akshare functions of the
same names wrap these exact URLs.
"""
import csv
import json
import os
import urllib.parse

from net import fetch

HERE = os.path.dirname(os.path.abspath(__file__))
BELOW_BOOK_CSV = os.path.join(HERE, "data", "below_book_rate.csv")

_LEGU_TOKEN = "325843825a2745a2a8f9b9e3355cb864"      # static, public (same as akshare)
_EM_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _get(url, params, retries=4):
    return fetch(url + "?" + urllib.parse.urlencode(params), retries=retries)


# ------------------------------------------------------------------ below-book rate
def _fetch_below_book():
    s = _get("https://legulegu.com/stockdata/below-net-asset-statistics-data",
             {"marketId": "1", "token": _LEGU_TOKEN})
    out = {}
    for r in json.loads(s):
        n, tot = r.get("belowNetAsset"), r.get("totalCompany")
        if r.get("date") and n is not None and tot:
            out[r["date"][:10]] = (int(n), int(tot))
    return out


def refresh_below_book_csv():
    rows = _fetch_below_book()
    with open(BELOW_BOOK_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "below_book", "total", "below_book_rate"])
        for d in sorted(rows):
            n, tot = rows[d]
            w.writerow([d, n, tot, round(n / tot * 100.0, 3)])
    return len(rows)


def below_book_counts(live=True):
    """{date: (n_below_book, n_listed)}. Tries the live endpoint (full history
    in one response) and falls back to the committed CSV."""
    if live:
        try:
            rows = _fetch_below_book()
            if rows:
                return rows
        except Exception:                            # noqa: BLE001
            pass
    out = {}
    if os.path.exists(BELOW_BOOK_CSV):
        with open(BELOW_BOOK_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[r["date"]] = (int(r["below_book"]), int(r["total"]))
    return out


def below_book_series(live=True, counts=None):
    """{date: % of listed A-shares below book value}."""
    counts = below_book_counts(live) if counts is None else counts
    return {d: round(n / t * 100.0, 3) for d, (n, t) in counts.items() if t}


# ------------------------------------------------------------------ limit pools
def _pool(kind, date, sort):
    s = _get("https://push2ex.eastmoney.com/getTopic%sPool" % kind,
             {"ut": _EM_UT, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "10000",
              "sort": sort, "date": date.replace("-", "")})
    data = json.loads(s).get("data")
    return None if data is None else (data.get("pool") or [])


def limit_pools(dates, listed_by_date, workers=6):
    """{date: {"limit_up_rate", "limit_down_rate", "max_consec_limit"}} for the
    dates East Money still serves; rates are % of `listed_by_date[date]`.
    A date the endpoint returns nothing for (too old, or a failed request)
    is left out rather than read as zero."""
    from concurrent.futures import ThreadPoolExecutor

    def one(d):
        total = listed_by_date.get(d)
        if not total:
            return d, None
        try:
            up = _pool("ZT", d, "fbt:asc")
            down = _pool("DT", d, "fund:asc")
        except Exception:                            # noqa: BLE001
            return d, None
        if not up:          # an empty limit-up pool means "not served", not "none hit"
            return d, None
        return d, {
            "limit_up_rate": len(up) / total * 100.0,
            "limit_down_rate": (len(down) / total * 100.0) if down is not None else None,
            "max_consec_limit": max(p.get("lbc") or 1 for p in up),
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {d: v for d, v in ex.map(one, dates) if v is not None}
