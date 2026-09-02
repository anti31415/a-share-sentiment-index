# -*- coding: utf-8 -*-
"""
A real backtest (code review section 2) -- built on the full daily series in
data/asi_history.csv.

The fundamental difference from v1's backtest.py:
  v1 fed 11 known historical bottoms' parameters back into the anchors it
     had defined from those same 11 bottoms -- a 100% hit rate is guaranteed
     by construction. That's not a test, it's a definition.
  This does a [forward-return bucket test] instead: bucket every day by its
     ASI, then look at the actual 20/60/120-day forward return. Only if the
     low-score bucket's forward return is meaningfully better and the
     high-score bucket's is worse has the index actually been validated.
"""
import math
import random

# kept in sync with indicators.ZONES (boundaries recalibrated by data on 2026-09-02)
BUCKETS = [(0, 15, "0-15  Ice-cold"), (15, 30, "15-30 Pessimistic"),
           (30, 60, "30-60 Neutral"), (60, 80, "60-80 Optimistic"),
           (80, 101, "80-100 Greed")]
HORIZONS = (20, 60, 120)


# ------------------------------------------------------------------ utilities
def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def median(xs):
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def spearman(xs, ys):
    return pearson(_rank(xs), _rank(ys))


# ------------------------------------------------------------------ forward returns
def attach_forward(rows):
    c = [r["close"] for r in rows]
    n = len(rows)
    for i, r in enumerate(rows):
        for hz in HORIZONS:
            r["fwd%d" % hz] = (c[i + hz] / c[i] - 1) * 100 if i + hz < n else None
        j = min(n - 1, i + 120)
        r["mdd120"] = (min(c[i + 1:j + 1]) / c[i] - 1) * 100 if j > i else None
    return rows


def bucket_table(rows, key, title, lo_date=None, hi_date=None):
    sel = [r for r in rows if r.get(key) is not None
           and (lo_date is None or r["date"] >= lo_date)
           and (hi_date is None or r["date"] <= hi_date)]
    total = len(sel)
    print("")
    print("  %s   (sample %d trading days, %s -> %s)"
          % (title, total, sel[0]["date"] if sel else "-", sel[-1]["date"] if sel else "-"))
    print("  " + "-" * 94)
    print("  %-18s %6s %7s %10s %10s %10s %9s %13s"
          % ("ASI bucket", "days", "share", "fwd 20d", "fwd 60d", "fwd 120d",
             "120d win%", "worst DD"))
    print("  " + "-" * 94)
    out = {}
    for lo, hi, name in BUCKETS:
        b = [r for r in sel if lo <= r[key] < hi]
        if not b:
            print("  %-18s %6d %6.1f%%          -          -          -         -           -"
                  % (name, 0, 0.0))
            out[name] = None
            continue
        f = {hz: [r["fwd%d" % hz] for r in b if r["fwd%d" % hz] is not None] for hz in HORIZONS}
        wr = [1 if r["fwd120"] > 0 else 0 for r in b if r["fwd120"] is not None]
        dd = [r["mdd120"] for r in b if r["mdd120"] is not None]
        print("  %-18s %6d %6.1f%% %9.2f%% %9.2f%% %9.2f%% %8.1f%% %10.2f%%"
              % (name, len(b), len(b) / total * 100,
                 mean(f[20]), mean(f[60]), mean(f[120]),
                 mean(wr) * 100 if wr else float("nan"), mean(dd)))
        out[name] = {"n": len(b), "share": len(b) / total * 100,
                     "f20": mean(f[20]), "f60": mean(f[60]), "f120": mean(f[120]),
                     "win": mean(wr) * 100 if wr else float("nan"), "mdd": mean(dd)}
    print("  " + "-" * 94)
    xs = [r[key] for r in sel if r["fwd120"] is not None]
    ys = [r["fwd120"] for r in sel if r["fwd120"] is not None]
    rho = spearman(xs, ys) if xs else float("nan")
    lvl = pearson([r[key] for r in sel], [r["close"] for r in sel])
    print("  Spearman(ASI, fwd 120d return) = %+.3f   <- more negative is better (low score -> high return)" % rho)
    print("  Pearson (ASI, index level)     = %+.3f   <- design rule 1: should stay near 0" % lvl)
    out["_rho"] = rho
    out["_lvl"] = lvl
    out["_n"] = total
    return out


# ------------------------------------------------------------------ significance
def block_bootstrap_diff(rows, key, lo, hi, iters=2000, block=120, seed=7):
    """
    Low-bucket forward-120-day return vs. the full-sample mean, with a
    p-value from a moving-block bootstrap. Overlapping windows badly inflate
    an ordinary t-statistic, so the return series is block-shuffled to
    preserve its autocorrelation structure before checking whether the
    "low-bucket excess return" still holds up.
    """
    sel = [r for r in rows if r.get(key) is not None and r["fwd120"] is not None]
    n = len(sel)
    if n < block * 3:
        return None
    flags = [1 if lo <= r[key] < hi else 0 for r in sel]
    vals = [r["fwd120"] for r in sel]
    if sum(flags) < 20:
        return None
    obs = mean([vals[i] for i in range(n) if flags[i]]) - mean(vals)
    rnd = random.Random(seed)
    cnt = 0
    for _ in range(iters):
        sh = []
        while len(sh) < n:
            s = rnd.randrange(n)
            sh.extend(vals[s:s + block] if s + block <= n
                      else vals[s:] + vals[:block - (n - s)])
        sh = sh[:n]
        bs = [sh[i] for i in range(n) if flags[i]]
        if bs and (mean(bs) - mean(sh)) >= obs:
            cnt += 1
    return obs, (cnt + 1) / float(iters + 1)


# ------------------------------------------------------------------ confirmation-rule backtest
def confirm_backtest(rows, key, need_days, thr=15.0, horizon=250):
    """
    After each time ASI breaks below thr:
      * requires need_days consecutive trading days back above thr before
        counting as [confirmed]
      * compares "buy at the break" against "buy at confirmation" on worst
        drawdown / one-year-later return
    """
    c = [r["close"] for r in rows]
    vals = [r.get(key) for r in rows]
    n = len(rows)
    events, i = [], 1
    while i < n:
        if vals[i] is None or vals[i - 1] is None:
            i += 1
            continue
        if vals[i] < thr <= vals[i - 1]:
            brk = i
            run, j, conf = 0, i, None
            while j < n:
                if vals[j] is not None and vals[j] >= thr:
                    run += 1
                    if run >= need_days:
                        conf = j
                        break
                else:
                    run = 0
                j += 1
            if conf is None:
                i += 1
                continue
            seg = c[brk:conf + 1]
            bot = brk + seg.index(min(seg))
            end = min(n - 1, conf + horizon)
            endb = min(n - 1, brk + horizon)
            events.append({
                "break": rows[brk]["date"], "bottom": rows[bot]["date"],
                "confirm": rows[conf]["date"], "lag": conf - bot,
                "give_up": (c[conf] / c[bot] - 1) * 100,
                "dd_naive": (min(c[brk:endb + 1]) / c[brk] - 1) * 100,
                "dd_conf": (min(c[conf:end + 1]) / c[conf] - 1) * 100,
                "ret_naive": (c[endb] / c[brk] - 1) * 100,
                "ret_conf": (c[end] / c[conf] - 1) * 100,
            })
            i = conf + 1
        else:
            i += 1
    return events


# ------------------------------------------------------------------ position-sizing backtest
def position_backtest(rows, key, lo=20, hi=90):
    """position = clip(100 - ASI, lo, hi) / 100, decided from [the prior day's] ASI, rebalanced daily."""
    sel = [r for r in rows if r.get(key) is not None]
    nav, cur = [1.0], 1.0
    for i in range(1, len(sel)):
        w = max(lo, min(hi, 100 - sel[i - 1][key])) / 100.0
        cur *= (1 + w * (sel[i]["close"] / sel[i - 1]["close"] - 1))
        nav.append(cur)
    return sel, nav


def bucket_position_backtest(rows, key, table):
    """table = [(lo, hi, position weight)]; also decided from the prior day's ASI only."""
    sel = [r for r in rows if r.get(key) is not None]
    nav, cur = [1.0], 1.0
    for i in range(1, len(sel)):
        a = sel[i - 1][key]
        w = 0.5
        for lo, hi, ww in table:
            if lo <= a < hi:
                w = ww
                break
        cur *= (1 + w * (sel[i]["close"] / sel[i - 1]["close"] - 1))
        nav.append(cur)
    return sel, nav


def bench_nav(sel, w):
    nav, cur = [1.0], 1.0
    for i in range(1, len(sel)):
        cur *= (1 + w * (sel[i]["close"] / sel[i - 1]["close"] - 1))
        nav.append(cur)
    return nav


def nav_stats(nav):
    yrs = len(nav) / 243.0
    cagr = (nav[-1] ** (1 / yrs) - 1) * 100
    peak, mdd = nav[0], 0.0
    for x in nav:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    m = mean(rets)
    vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * math.sqrt(243) * 100
    return {"final": nav[-1], "cagr": cagr, "mdd": mdd * 100, "vol": vol,
            "calmar": cagr / abs(mdd * 100) if mdd else float("nan")}
