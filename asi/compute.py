# -*- coding: utf-8 -*-
"""
ASI scoring and composition. Fixes three bugs from v1 plus one structural change.

  1.1 [Severe] A missing indicator was scored as 0 -> now normalized by
      effective weight, with a reported coverage; a missing valuation
      dimension (below-book-value rate) now REJECTS scoring instead of
      scoring degraded.
  1.2 The 60-day average volume slice was one day short -> see vol_ratio in series.py.
  1.3 RSI with zero volatility returned 100 -> now returns 50.
  3.2 Monotonic volume mapping -> volume-price pairing (vol_temp_score).
"""
from indicators import DIMENSIONS, INDICATORS, ZONES, W_TOTAL     # noqa: E402


class InsufficientData(Exception):
    """Not enough valid indicators to produce a score."""


def interpolate(value, anchors):
    """Piecewise-linear interpolation, clamped at both ends. Anchor
    monotonicity is already asserted at module level in indicators.py."""
    if value <= anchors[0][0]:
        return float(anchors[0][1])
    if value >= anchors[-1][0]:
        return float(anchors[-1][1])
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return 50.0


# ------------------------------------------------------------------ volume-price pairing
_VT_CORNERS = {          # (light/heavy volume, down/up) -> sentiment score
    (-1, -1): 30.0,      # light volume + decline: selling pressure exhausted, cold but not panicked
    (+1, -1): 8.0,       # heavy volume + decline: panic selling  <- v1 would score this "hot"
    (-1, +1): 45.0,      # light volume + advance: low-volume bounce, neutral-cold
    (+1, +1): 92.0,      # heavy volume + advance: euphoria
}


def vol_temp_score(volume_ratio, ret5):
    """
    Volume must be paired with price direction (code review 3.2).
      volume_ratio : today's turnover / trailing-60-day average turnover
      ret5          : trailing-5-day return (%)
    Bilinear interpolation across the four corners of the (volume, price) plane.
    """
    if volume_ratio is None or ret5 is None:
        return None
    v = max(-1.0, min(1.0, (volume_ratio - 1.0) / 0.8))   # 0.2x -> -1, 1.8x -> +1
    r = max(-1.0, min(1.0, ret5 / 5.0))                   # saturates at +-5%
    wv, wr = (v + 1) / 2.0, (r + 1) / 2.0
    return (_VT_CORNERS[(-1, -1)] * (1 - wv) * (1 - wr)
            + _VT_CORNERS[(+1, -1)] * wv * (1 - wr)
            + _VT_CORNERS[(-1, +1)] * (1 - wv) * wr
            + _VT_CORNERS[(+1, +1)] * wv * wr)


def score_indicator(key, raw):
    ind = INDICATORS[key]
    if ind["anchors"] is None:                 # vol_temp is already a score
        return float(raw)
    return interpolate(float(raw), ind["anchors"])


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def zone_of(score):
    """Returns (index, name, tag, desc) -- no longer reverse-looked-up by
    Chinese label text (code review, "other minor issues")."""
    for i, (lo, hi, name, tag, desc) in enumerate(ZONES):
        if lo <= score < hi:
            return i, name, tag, desc
    return len(ZONES) - 1, ZONES[-1][2], ZONES[-1][3], ZONES[-1][4]


def compute(values, min_coverage=0.70, require_valuation=True):
    """
    values: {indicator_key: raw_value}
    Returns a dict: score / coverage / degraded / zone / dims / rows
    require_valuation=False is only for historical backtests over periods
    with a KNOWN, SYSTEMATIC gap in the valuation dimension; daily live runs
    must keep this True.
    """
    dims, total, eff_w = [], 0.0, 0
    for d in DIMENSIONS:
        members = []
        for k in d["members"]:
            raw = values.get(k)
            if raw is None:
                members.append({"key": k, "name": INDICATORS[k]["name"], "missing": True})
                continue
            s = score_indicator(k, raw)
            members.append({"key": k, "name": INDICATORS[k]["name"],
                            "unit": INDICATORS[k]["unit"], "raw": round(float(raw), 2),
                            "score": round(s, 1), "missing": False})
        got = [m["score"] for m in members if not m["missing"]]
        if not got:
            if d["required"] and require_valuation:
                raise InsufficientData(
                    "The [%s] dimension has no valid data. It is the only "
                    "valuation dimension -- without it the ASI is a different "
                    "instrument (everything left is a transform of price/"
                    "volume), so scoring is refused." % d["name"])
            dims.append({"key": d["key"], "name": d["name"], "weight": d["weight"],
                         "score": None, "missing": True, "members": members})
            continue
        ds = _median(got) if d["agg"] == "median" else sum(got) / len(got)
        total += ds * d["weight"]
        eff_w += d["weight"]
        dims.append({"key": d["key"], "name": d["name"], "weight": d["weight"],
                     "score": round(ds, 1), "missing": False, "members": members,
                     "contrib": round(ds * d["weight"] / W_TOTAL, 1)})

    if eff_w == 0:
        raise InsufficientData("No valid dimensions at all.")

    score = round(total / eff_w, 1)                # normalize: denominator shrinks with what's missing
    coverage = eff_w / float(W_TOTAL)
    zi, zname, ztag, zdesc = zone_of(score)
    return {"score": score, "coverage": round(coverage, 3),
            "degraded": coverage < min_coverage,
            "zone_index": zi, "zone": zname, "zone_tag": ztag, "zone_desc": zdesc,
            "dims": dims,
            "rows": [m for d in dims for m in d["members"]]}
