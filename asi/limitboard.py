# -*- coding: utf-8 -*-
"""
Limit-up share / consecutive-limit-up streaks -- [reconstructed] from
unadjusted closing prices already on hand, rather than relying on East
Money's "limit board" endpoint (push2ex.eastmoney.com/getTopicZTPool, which
in practice only serves the most recent ~10 trading days and can't be used
for a historical backtest).

Reconstruction method: judge whether a day's return hits the board-specific
limit threshold.
  * Main board / SME board (000/001/002/003/600/601/603/605): +-10% (+-5% for ST names)
  * ChiNext 300/301, STAR 688: +-10% before the 2020-08-24 registration-system
    reform, +-20% after
Thresholds are set slightly inside the theoretical limit (e.g. 9.8% / 19.8%)
to absorb rounding of the closing price to the nearest cent.

Accuracy check (2026-08-28, 900-stock sample reconstruction vs. East Money's
measured figure): the sample reconstruction, scaled up to the full market,
gives 87 stocks; East Money's own measured figure is 82 -- about a 6%
relative error. That's an acceptable amount of sampling noise for a
proportion-style metric like "limit-up share."
"""
CHINEXT_STAR_CUTOVER = "2020-08-24"


def board_of(code):
    if code[:3] in ("300", "301", "688"):
        return "20pct"
    return "10pct"


def limit_thresholds(code, name, date):
    """Returns (up_thr, down_thr) -- the daily-return threshold (as a
    fraction) used to flag a limit-up / limit-down."""
    is_st = "ST" in name.upper()
    board = board_of(code)
    if board == "20pct" and date >= CHINEXT_STAR_CUTOVER:
        return 0.198, -0.198
    if is_st:
        return 0.048, -0.048
    return 0.098, -0.098


def daily_flags(code, name, kline):
    """
    kline: [[date, close, vol], ...] in chronological order.
    Returns {date: 'up' | 'down' | None}
    """
    out = {}
    for i in range(1, len(kline)):
        d, c, _ = kline[i]
        pc = kline[i - 1][1]
        if pc <= 0:
            continue
        pct = c / pc - 1
        up_thr, down_thr = limit_thresholds(code, name, d)
        if pct >= up_thr:
            out[d] = "up"
        elif pct <= down_thr:
            out[d] = "down"
        else:
            out[d] = None
    return out


def cross_section(sample, index_dates, kline_by_code):
    """
    sample: [[code, mkt, name, pb], ...]
    kline_by_code: {code: kline} -- daily bars already fetched, avoids re-requesting
    Returns {date: {"up_rate":%, "down_rate":%, "max_consec":int, "n": stocks trading in the sample}}
    """
    idx_set = set(index_dates)
    up_cnt = {d: 0 for d in index_dates}
    down_cnt = {d: 0 for d in index_dates}
    tot = {d: 0 for d in index_dates}
    consec_run = {d: [] for d in index_dates}       # streak length of stocks still mid-streak that day

    for code, mkt, name, _pb in sample:
        k = kline_by_code.get(code)
        if not k or len(k) < 30:
            continue
        flags = daily_flags(code, name, k)
        run = 0
        for d, _c, _v in k:
            f = flags.get(d)
            if d in idx_set:
                tot[d] += 1
                if f == "up":
                    up_cnt[d] += 1
                elif f == "down":
                    down_cnt[d] += 1
            if f == "up":
                run += 1
            else:
                run = 0
            if d in idx_set and f == "up" and run >= 2:
                consec_run[d].append(run)

    out = {}
    for d in index_dates:
        out[d] = {
            "up_rate": (up_cnt[d] / tot[d] * 100.0) if tot[d] >= 50 else None,
            "down_rate": (down_cnt[d] / tot[d] * 100.0) if tot[d] >= 50 else None,
            "max_consec": max(consec_run[d]) if consec_run[d] else 1,
            "n": tot[d],
        }
    return out
