# -*- coding: utf-8 -*-
"""Smoke tests for asi/indices.py helpers and asi/report_indices.py rendering.
Run: python tests/test_indices_report.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asi"))
import indices  # noqa: E402
import report_indices as R  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)


print("Input split")
ok(set(indices.INDEX_KEYS).isdisjoint(indices.MARKET_KEYS), "index-specific and market-wide inputs don't overlap")
ok(set(indices.INDEX_KEYS) | set(indices.MARKET_KEYS) == set(indices.history.V2_KEYS), "together they cover every scored input")
ok(indices.trailing_mean([1, 2, 3, None, 5], 2) == [1, 1.5, 2.5, None, 4.0], "smoothing skips missing days without resetting")

print("\nReport")


def fake(sym, primary, state, prior, reason=None):
    ytd = [{"date": "2026-%02d-%02d" % (m, d), "close": 3000 + m * 10 + d, "asi_sm": 40 + m + d / 10,
            "heat": 0.95 if (m, d) == (3, 10) else 0.4, "state": state} for m in range(1, 10) for d in (5, 10, 20)]
    return {"symbol": sym, "name": sym, "primary": primary, "date": "2026-09-20", "close": 3100.5, "change_pct": -1.2,
            "asi_today": 49.0, "asi_smoothed_5d": 48.7, "coverage": 1.0, "missing_inputs": ["iv_temp"] if primary else [],
            "heat": 0.31, "prior_state": prior, "state": state, "satellite_target_weight": 0.2,
            "triggered": state != prior, "trigger_reason": reason, "trim_by": "asi" if state == "Optimistic (trim)" else None,
            "history_start": "2020-01-02" if not primary else "2005-01-04",
            "rarity": {"side": "high", "months": 121, "hits": 60, "every_months": 2.0, "label": None},
            "odds": {"side": "high", "days": 800, "episodes": 9, "3m": {"win_rate": 60.8, "median_return": -1.5, "n": 700},
                     "6m": {"win_rate": 63.6, "median_return": -4.0, "n": 650}},
            "ytd": ytd, "transitions_ytd": [{"date": "2026-08-10", "from": "Neutral (default)", "to": "Optimistic (trim)", "reason": "asi"}]}


data = {"as_of": "2026-09-20", "indices": [fake("sh000001", True, "Optimistic (trim)", "Optimistic (trim)"),
                                           fake("sh000680", False, "Optimistic (trim)", "Neutral (default)", "heat")]}
page = R.render(data, R.merged_labels(None))
ok(page.count("<svg") == 2, "one chart per index")
ok("State changes today" in page and "short-term overheating" in page, "a state change and its reason are in the headline")
ok("Inputs unavailable today: iv_temp" in page, "missing inputs are flagged")
ok("History for this index starts 2020-01-02" in page, "a short-history index is flagged")
ok('class="hot"' in page, "overheating days are marked on the chart")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"title": "T2", "states": {"Optimistic (trim)": "TRIM!"}, "index_names": {"sh000001": "Main"}}, f)
L = R.merged_labels(f.name)
os.unlink(f.name)
page = R.render(data, L)
ok("<title>T2</title>" in page and "TRIM!" in page, "a labels file overrides text, nested dicts merged")
ok(L["states"]["Neutral (default)"] == "Neutral (default)", "keys the labels file doesn't set keep their defaults")

print("\n" + "=" * 46)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
