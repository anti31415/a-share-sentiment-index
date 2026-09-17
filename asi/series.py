# -*- coding: utf-8 -*-
"""
Assembles raw data into a [daily ASI input panel] -- the upstream source for
asi_history.csv (code review section 5).

Key point-in-time discipline:
  * Annual-report book value per share (BPS) is only usable [120 days after
    the report period] (annual reports are legally due by April 30) --
    otherwise it's lookahead bias.
  * The 60-day average volume excludes the current day and strictly uses 60
    bars (fixes v1's a[-61:-1], which was only 59 bars when len==60).
  * Drawdown / new-low windows count each security's own trading days.
"""
import bisect, os
from collections import deque

import fetch
from compute import vol_temp_score

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ index derivation
def rsi_wilder(closes, n=14):
    """Returns 50 with zero volatility (v1 returned 100, misreading a halt/
    flat stretch as extreme greed)."""
    if len(closes) < n + 1:
        return None
    g = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    l = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def index_panel(rows):
    """rows=[[date,close,vol],...] -> [{date, close, rsi14, bias60, drawdown, vol_ratio, ret5}]"""
    dates = [r[0] for r in rows]
    c = [r[1] for r in rows]
    v = [r[2] for r in rows]
    out = []
    # Incremental (Wilder) RSI, avoids recomputing O(n^2) every day
    ag = al = None
    for i in range(len(rows)):
        rec = {"date": dates[i], "close": c[i]}
        if i >= 14:
            g = [max(c[j] - c[j - 1], 0.0) for j in range(i - 13, i + 1)]
            ls = [max(c[j - 1] - c[j], 0.0) for j in range(i - 13, i + 1)]
            if i == 14:
                ag, al = sum(g) / 14.0, sum(ls) / 14.0
            else:
                ag = (ag * 13 + g[-1]) / 14.0
                al = (al * 13 + ls[-1]) / 14.0
            if al == 0:
                rec["rsi14"] = 100.0 if ag > 0 else 50.0
            else:
                rec["rsi14"] = 100.0 - 100.0 / (1.0 + ag / al)
        if i >= 59:
            ma60 = sum(c[i - 59:i + 1]) / 60.0
            rec["bias60"] = (c[i] - ma60) / ma60 * 100.0
        if i >= 60:                                   # needs 61 bars: the trailing 60 exclude today
            ma_amt = sum(v[i - 60:i]) / 60.0
            if ma_amt > 0:
                rec["vol_ratio"] = v[i] / ma_amt
        if i >= 5:
            rec["ret5"] = (c[i] / c[i - 5] - 1.0) * 100.0
        lo = max(0, i - 251)
        hi = max(c[lo:i + 1])
        if hi > 0:
            rec["drawdown"] = (c[i] / hi - 1.0) * 100.0
        rec["vol_temp"] = vol_temp_score(rec.get("vol_ratio"), rec.get("ret5"))
        out.append(rec)
    return out


# ------------------------------------------------------------------ cross-section
def _eff_bps_index(bps):
    """[[report_date, bps]] -> (list of available-from dates, list of bps
    values). Available date = report period + 120 days."""
    avail, vals = [], []
    for d, b in bps:
        y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
        # report period 12-31 -> usable from 4-30 next year; approximated as
        # +4 months (day-level precision isn't needed)
        m2, y2 = m + 4, y
        if m2 > 12:
            m2 -= 12
            y2 += 1
        avail.append("%04d-%02d-%02d" % (y2, m2, min(day, 28)))
        vals.append(b)
    return avail, vals


def cross_section(sample, index_dates, with_bps=True, kline_by_code=None):
    """
    Computes three cross-sectional readings per day across the sampled stocks.
    Returns {date: {"breadth":%, "new_low":%, "broken_net_rate":%, "n": stocks trading in the sample}}

    with_bps=False skips the per-stock annual-report BPS fetch (one request per
    sampled stock, i.e. roughly half of a cold run's total network work) and
    returns broken_net_rate=None for every date. Callers that read the
    below-book-value rate from the full-market listing instead of from the
    sample -- run_daily.today_values() does -- never look at that key, so
    fetching it is pure cost for them. build_panel(), which writes the
    historical below-book-value column into asi_history.csv, still needs it.

    kline_by_code={code: bars} reads bars already fetched by the caller instead
    of fetching them here; a code missing from it is skipped, not re-fetched.
    Either way a stock whose bars or BPS can't be fetched is dropped from the
    cross-section (the >=50-stock thresholds below still apply) rather than
    aborting the whole run.
    """
    idx_set = set(index_dates)
    up = {d: 0 for d in index_dates}
    tot = {d: 0 for d in index_dates}
    nlow = {d: 0 for d in index_dates}
    nlow_tot = {d: 0 for d in index_dates}
    below = {d: 0 for d in index_dates}
    below_tot = {d: 0 for d in index_dates}

    for code, mkt, name, _pb in sample:
        if kline_by_code is not None:
            k = kline_by_code.get(code)
        else:
            try:
                k = fetch.stock_daily(fetch.sina_symbol(code, mkt))
            except Exception:                        # noqa: BLE001
                k = None
        if not k or len(k) < 30:
            continue
        if with_bps:
            try:
                bps = fetch.stock_bps(code, mkt)
            except Exception:                        # noqa: BLE001
                bps = None
            avail, bvals = _eff_bps_index(bps) if bps else ([], [])
        else:
            avail, bvals = [], []

        win = deque()                                  # rolling 252-day low
        prev = None
        for d, close, _v in k:
            # rolling window (simplified monotonic-queue: fixed-length deque + min)
            win.append(close)
            if len(win) > 252:
                win.popleft()
            if d in idx_set:
                if prev is not None:
                    tot[d] += 1
                    if close > prev:
                        up[d] += 1
                if len(win) >= 60:
                    nlow_tot[d] += 1
                    if close <= min(win) + 1e-9:
                        nlow[d] += 1
                if avail:
                    j = bisect.bisect_right(avail, d) - 1
                    if j >= 0 and bvals[j] > 0:
                        below_tot[d] += 1
                        if close < bvals[j]:
                            below[d] += 1
            prev = close

    out = {}
    for d in index_dates:
        out[d] = {
            "breadth_raw": (up[d] / tot[d] * 100.0) if tot[d] >= 50 else None,
            "new_low": (nlow[d] / nlow_tot[d] * 100.0) if nlow_tot[d] >= 50 else None,
            "broken_net_rate": (below[d] / below_tot[d] * 100.0) if below_tot[d] >= 50 else None,
            "n": tot[d],
        }
    return out


def smooth(seq, k=5):
    out, buf = [], deque()
    for x in seq:
        if x is None:
            out.append(None)
            continue
        buf.append(x)
        if len(buf) > k:
            buf.popleft()
        out.append(sum(buf) / len(buf))
    return out


def build_panel(n_sample=900, start="2005-01-01", index_sym="sh000001",
                 sample=None, min_cross_start="2004-01-01"):
    """
    Returns [{date, close, ...raw indicator values}] -- the full daily ASI input.
      index_sym : Sina symbol; swap in a sector index to reuse the whole
                  pipeline for a sector line (code review 6.3).
      sample    : explicit sampling pool (e.g. filtered by sector code prefix
                  or with ST names excluded); if omitted, defaults to a
                  900-stock random market-wide sample.
    """
    idx = [r for r in fetch.index_daily(index_sym) if r[0] >= min_cross_start]
    ip = index_panel(idx)
    dates = [r["date"] for r in ip]
    sample = sample if sample is not None else fetch.sample_universe(n_sample)
    cs = cross_section(sample, dates)
    br = smooth([cs[d]["breadth_raw"] for d in dates], 5)
    for i, rec in enumerate(ip):
        d = rec["date"]
        rec["breadth"] = br[i]
        rec["new_low"] = cs[d]["new_low"]
        rec["broken_net_rate"] = cs[d]["broken_net_rate"]
        rec["n_sample"] = cs[d]["n"]
    return [r for r in ip if r["date"] >= start]
