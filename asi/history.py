# -*- coding: utf-8 -*-
"""
Builds data/asi_history.csv -- the append-only historical series called for
in code review section 5. Also computes both v1 (the old model, bugs and
all) and v2 (the new model) ASI values side by side for comparison.
"""
import csv, os, sys

import erp as erp_mod
import fetch
import limitboard
import margin
import series
from compute import compute, InsufficientData

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(HERE))
import sentiment_index_v1 as v1                                # noqa: E402

OUT = os.path.join(HERE, "data", "asi_history.csv")
COLS = ["date", "close", "broken_net_rate", "bn_z10y", "erp", "breadth", "new_low",
        "vol_ratio", "ret5", "vol_temp", "rsi14", "bias60", "drawdown",
        "margin_chg5", "limit_up_rate", "limit_down_rate", "max_consec_limit",
        "n_sample", "asi_v2", "coverage", "zone_v2", "asi_v2_noerp",
        "asi_v2_nolvspec", "asi_v1"]

Z_WINDOW = 2520      # ~10 trading years
Z_MIN = 250          # need at least a year of data before giving a Z-score, avoids a noisy small-sample reading


def rolling_z(vals):
    """
    Code review 3.3's structural-neutralization suggestion: the below-book-
    value rate's absolute anchors drift with market structure (listed-company
    count, sector composition) -- 16% isn't the same thing in 2005 as in
    2026. This provides an auxiliary reading: the below-book-value rate's
    Z-score against its own [up to 10 years of past, historical-data-only]
    window -- display only, does not feed into the score. Only uses the
    window up to and including the current day, to avoid lookahead bias.
    """
    out, buf = [], []
    for x in vals:
        if x is None:
            out.append(None)
        elif len(buf) < Z_MIN:
            out.append(None)
        else:
            m = sum(buf) / len(buf)
            var = sum((b - m) ** 2 for b in buf) / len(buf)
            sd = var ** 0.5
            out.append(round((x - m) / sd, 2) if sd > 1e-9 else 0.0)
        if x is not None:
            buf.append(x)
            if len(buf) > Z_WINDOW:
                buf.pop(0)
    return out


V2_KEYS = ("broken_net_rate", "erp", "breadth", "new_low", "vol_temp",
           "rsi14", "bias60", "drawdown",
           "margin_chg5", "limit_up_rate", "limit_down_rate", "max_consec_limit")


def v2_row(rec):
    vals = {k: rec.get(k) for k in V2_KEYS}
    try:
        # The valuation dimension can be missing in parts of the historical
        # series due to sample gaps; degraded scoring is allowed here but
        # the coverage figure records it.
        return compute(vals, require_valuation=False)
    except InsufficientData:
        return None


def v2_row_noerp(rec):
    """Comparison score with ERP removed -- measures how much ERP added
    (only meaningful for the recent ~5 years)."""
    vals = {k: rec.get(k) for k in V2_KEYS if k != "erp"}
    try:
        return compute(vals, require_valuation=False)
    except InsufficientData:
        return None


NOLVSPEC_KEYS = ("broken_net_rate", "erp", "breadth", "new_low", "vol_temp",
                 "rsi14", "bias60", "drawdown")


def v2_row_nolvspec(rec):
    """Comparison score with the leverage-sentiment and speculation-extremity
    dimensions removed (added 2026-09 per a user follow-up request)."""
    vals = {k: rec.get(k) for k in NOLVSPEC_KEYS}
    try:
        return compute(vals, require_valuation=False)
    except InsufficientData:
        return None


def v1_row(rec):
    """Feeds the exact same inputs into the old model (bugs included), for a fair comparison."""
    vals = {"broken_net_rate": rec.get("broken_net_rate"),
            "breadth": rec.get("breadth"),
            "volume_ratio": rec.get("vol_ratio"),
            "rsi14": rec.get("rsi14"),
            "bias60": rec.get("bias60"),
            "drawdown": rec.get("drawdown")}
    vals = {k: x for k, x in vals.items() if x is not None}
    if not vals:
        return None
    return v1.compute(vals)


def build(n_sample=900, seed=20260902):
    sample = fetch.sample_universe(n_sample, seed=seed)
    panel = series.build_panel(n_sample, sample=sample)
    dates = [r["date"] for r in panel]

    erp_by_date = erp_mod.daily_series(dates)
    margin_by_date = margin.chg5_series(dates)

    print("      Reconstructing limit-up/streaks: loading cached daily bars for %d sampled stocks..."
          % len(sample), flush=True)
    kline_by_code = {}
    for code, mkt, name, _pb in sample:
        k = fetch.stock_daily(fetch.sina_symbol(code, mkt))
        if k:
            kline_by_code[code] = k
    lb = limitboard.cross_section(sample, dates, kline_by_code)

    for rec in panel:
        d = rec["date"]
        rec["erp"] = erp_by_date.get(d)
        rec["margin_chg5"] = margin_by_date.get(d)
        rec["limit_up_rate"] = lb[d]["up_rate"]
        rec["limit_down_rate"] = lb[d]["down_rate"]
        rec["max_consec_limit"] = lb[d]["max_consec"] if lb[d]["up_rate"] is not None else None

    rows = []
    for rec in panel:
        r2 = v2_row(rec)
        r2n = v2_row_noerp(rec)
        r2ls = v2_row_nolvspec(rec)
        r1 = v1_row(rec)
        rows.append({
            "date": rec["date"], "close": round(rec["close"], 2),
            "broken_net_rate": _r(rec.get("broken_net_rate")),
            "erp": _r(rec.get("erp"), 3),
            "breadth": _r(rec.get("breadth")), "new_low": _r(rec.get("new_low")),
            "vol_ratio": _r(rec.get("vol_ratio"), 3), "ret5": _r(rec.get("ret5")),
            "vol_temp": _r(rec.get("vol_temp")), "rsi14": _r(rec.get("rsi14")),
            "bias60": _r(rec.get("bias60")), "drawdown": _r(rec.get("drawdown")),
            "margin_chg5": _r(rec.get("margin_chg5"), 3),
            "limit_up_rate": _r(rec.get("limit_up_rate"), 3),
            "limit_down_rate": _r(rec.get("limit_down_rate"), 3),
            "max_consec_limit": rec.get("max_consec_limit") if rec.get("max_consec_limit") is not None else "",
            "n_sample": rec.get("n_sample"),
            "asi_v2": r2["score"] if r2 else "", "coverage": r2["coverage"] if r2 else "",
            "zone_v2": r2["zone"] if r2 else "",
            "asi_v2_noerp": r2n["score"] if r2n else "",
            "asi_v2_nolvspec": r2ls["score"] if r2ls else "",
            "asi_v1": r1["score"] if r1 else "",
        })
    bn_z = rolling_z([rec.get("broken_net_rate") for rec in panel])
    for row, z in zip(rows, bn_z):
        row["bn_z10y"] = "" if z is None else z
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return rows


def _r(x, n=2):
    return "" if x is None else round(x, n)


def load():
    with open(OUT, encoding="utf-8-sig") as f:
        out = []
        for r in csv.DictReader(f):
            d = {"date": r["date"], "zone_v2": r["zone_v2"]}
            for k in COLS:
                if k in ("date", "zone_v2"):
                    continue
                d[k] = float(r[k]) if r[k] not in ("", None) else None
            out.append(d)
        return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    rs = build(n)
    print("Wrote %s -- %d rows, %s -> %s" % (OUT, len(rs), rs[0]["date"], rs[-1]["date"]))
    ok = [r for r in rs if r["asi_v2"] != ""]
    print("%d rows with a valid ASI; earliest %s" % (len(ok), ok[0]["date"] if ok else "-"))
