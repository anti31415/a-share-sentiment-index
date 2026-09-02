# A-Share Sentiment Index (ASI)

A market-sentiment index for China's A-share market, rebuilt from a
first-generation prototype through a code review, three rounds of
data-driven extension, and a final reframing around a specific, realistic
use case: **an executable signal for someone who already holds an index ETF
and/or a quant-fund product**, with a 1-2 week subscription/redemption lag
baked into the design and the backtest.

Everything here is derived from **real, point-in-time daily market data** --
2005-01-04 through the present for the core model (5,262+ trading days), and
2012-09 / 2016-09 / 2021-09 onward for the three indicators whose public
data sources don't go back further. No indicator's historical values were
hand-typed or reverse-engineered from "what a bottom should look like."

## Quick start

```bash
pip install -r requirements.txt      # only needed for margin.py / fetch_erp_data.py
cd asi
python download.py 900               # pulls & caches raw data (first run only, ~5-10 min)
python history.py 900                # builds data/asi_history.csv (~5,262 rows)
python run_daily.py                  # today's reading, human-readable
python check_signal.py               # today's reading, machine-readable + trade trigger
python report.py                     # the full v1-vs-v2 validation report
python report_actionable.py          # the tactical-satellite-sleeve backtest report
python ../tests/test_compute.py      # 61 unit tests
```

Nothing here needs an API key. `asi/data/cache/` holds a gzip cache of every
HTTP response so re-running any script is fast and doesn't re-hit the
network; delete it to force a refetch.

## What it actually is

A 0-100 score built from **six weighted dimensions**, each in turn built
from one or more raw indicators:

| Dimension | Weight | Members | What it measures |
|---|---|---|---|
| Valuation despair | 35% | below-book-value rate, equity risk premium (ERP) | how cheap the market is |
| Trading cooldown | 20% | volume-price temperature | is anyone still trading, and which way |
| Price momentum | 20% | RSI(14), BIAS(60), drawdown depth (median of the three) | how far price has fallen from normal |
| Market breadth | 10% | advancers share, new-52-week-low share | broad-based selling vs. structural |
| Leverage sentiment | 10% | margin (financing) balance 5-day change | is leveraged money adding or being forcibly liquidated |
| Speculation extremity | 5% | limit-up share, limit-down share, longest limit-up streak | how frothy or panicked is the tape |

0 = extreme panic / blood in the streets. 100 = extreme greed / the fat tail
of a rally. Full anchor tables and the reasoning behind every weight are in
`asi/indicators.py`.

### Zone boundaries (recalibrated by data, not intuition)

| Score | Zone | What the backtest actually shows there |
|---|---|---|
| 0-15 | Ice-cold | fwd 120d **+22.9%**, win rate 90.3% |
| 15-30 | Pessimistic | fwd 120d **+14.1%**, win rate 84.2% |
| 30-60 | Neutral | fwd 120d +5.4%, win rate 58.9% |
| 60-80 | **Optimistic ("no edge")** | fwd 120d **+1.1%**, win rate **44.4%** -- the worst bucket of all five |
| 80-100 | Greed | fwd 120d +12.0%, win rate 53.5% -- momentum still runs, but the deepest interim drawdown of the five |

The original boundaries were (0, 15, 30, 70, 85, 100), set by intuition. A
rolling 150-day scan of forward-120-day returns across the full sample found
that the genuinely "no edge, forward returns often negative" zone is 60-80
-- the old scheme split that across the bottom of "optimistic 70-85" and the
top of "neutral" -- while the old "greed zone 85-100" actually has *positive*
forward returns (no top forms within a 120-day window) and is simply the
most volatile zone. The boundary moved to 60/80 to match what the data shows.

## What's provably true, and what isn't

The project's whole point is to not overclaim. Here's the honest ledger:

**Holds up under a real, non-self-referential backtest:**
- Full-sample Spearman correlation between ASI and the forward-120-day
  return: **-0.263** (v2) vs. **-0.130** (v1, the original flat six-indicator
  design) -- a meaningfully cleaner ranking signal.
- Signal-driven satellite-sleeve purchases (see below) averaged a purchase
  price **13.0% below** the plain period-average price.
- A moving-block bootstrap (2,000 iterations, block=120 days, to avoid the
  inflated significance overlapping windows create) puts the ice-cold
  bucket's excess forward return at **p ≈ 0.09** -- suggestive, not proof;
  21 years only produced 31 ice-cold days total.

**Does not hold up, and is flagged as such in the code:**
- The asymmetric "confirm before buying" rule (wait for 3 consecutive days
  back above the panic threshold): **net negative** -- costs ~5.4% of
  return on average and makes the worst drawdown *worse*, not better. Kept
  in `asi/confirm.py` for reproducibility, not recommended for live use.
- A continuous linear position-sizing curve (`position = clip(100-ASI, 20,
  90)%`): underperforms a constant 50% position on every metric. ASI has
  no edge across ~85% of trading days (the 30-80 range), so a formula that
  trades every day mostly trades noise.
- Speculation-extremity, added in the third round, moves ranking power and
  price-decoupling by essentially nothing on its own -- consistent with
  its deliberately tiny 5% weight, and flagged in `indicators.py` as
  "supporting signal, not backtest-proven."

## The actionable design (`asi/actionable.py`, `asi/check_signal.py`)

The insight that reframed the whole project: **a 1-2 week fund
subscription/redemption lag makes "time the core position" impossible**, so
timing the core holding isn't the right goal. Instead:

- The **core holding never moves** and isn't part of this strategy at all.
- A separate **tactical reserve** (e.g. 20-30% of total investable assets,
  held in cash/money-market funds) swaps between cash yield and index
  exposure, driven by a **5-day-smoothed** ASI (single-day noise is
  irrelevant when execution takes 5-10 days anyway).
- A **hysteresis state machine** only acts at the two zones actually
  validated by backtest -- entering "Pessimistic (add)" below 25, entering
  "Optimistic (trim)" in 65-80 -- and only on the *first* crossing of a
  zone, not every day the reading stays there. The 30-65 "no edge" middle
  is left alone entirely.

Backtested over the last 10 years (2016-09 onward) with an 8-day execution
lag, the signal-driven tactical reserve beats a same-shaped always-cash,
always-invested, and 24-month-DCA benchmark on every metric -- CAGR,
max drawdown, and Calmar ratio. Full numbers, the exact state thresholds,
and an operating playbook are in `asi/report_actionable.py`'s output and
`.claude/skills/asi-signal-check/SKILL.md`.

**Caveat stated plainly:** over this particular 10-year window the smoothed
ASI never actually reached the two most extreme states (below 15, or above
80) -- it ranged 17.3 to 72.1. Those extreme tiers exist as a safety net for
market conditions more severe than anything 2016-2026 produced, not as
something this specific backtest window proves works.

## Data sources (all free, no API key)

| Source | What it provides | History |
|---|---|---|
| Sina `CN_MarketData.getKLineData` | Unadjusted daily bars, index & stock | back to 2001 |
| Sina `hs_a` node | Full A-share listing + live PB | current snapshot |
| East Money `datacenter-web` (`RPT_F10_FINANCE_MAINFINADATA`) | Per-stock annual-report book value per share | as reported |
| akshare `stock_margin_account_info` (wraps East Money) | Nationwide margin (financing) balance | 2012-09-27+ |
| China Central Depository & Clearing 10-year yield (via akshare `bond_china_yield`) | 10-year government bond yield | 2021-09+ (see note below) |
| legulegu `index-basic-pe` | CSI300 cap-weighted TTM PE | monthly, 2005-04+ |

**Known gap:** the 10-year bond yield's own history-query endpoint was tried
repeatedly and never returned reliably further back than 2021-09 (it seems
to expect an interactive browser session). This caps the ERP indicator to a
5-year window with percentile-based (not absolute) anchors -- documented in
`asi/erp.py`.

**Also known:** limit-up/limit-down/streak data isn't queried from East
Money's own "limit board" endpoint, because that endpoint only serves the
most recent ~10 trading days and can't feed a historical backtest. Instead
`asi/limitboard.py` reconstructs it from the same unadjusted daily bars used
everywhere else, using board-specific thresholds (10%/20%/5% for ST names,
with the 2020-08-24 ChiNext/STAR registration-reform cutover). Checked
against East Money's own measured count on 2026-08-28: 87 (reconstructed)
vs. 82 (measured), about a 6% relative error -- accurate enough for a
share-of-market metric, not for anything requiring per-stock precision.

## Project layout

```
sentiment-index/
├── asi/
│   ├── indicators.py        Indicator + dimension config (weights, anchors, zone boundaries)
│   ├── compute.py           Scoring engine (effective-weight normalization, refuses to score
│   │                        when the valuation dimension is entirely missing)
│   ├── net.py, fetch.py     HTTP layer + cached data fetchers (Sina, East Money)
│   ├── series.py            Index-derived indicators (RSI, BIAS, drawdown, volume ratio) +
│   │                        cross-sectional reconstruction (breadth, below-book-value rate)
│   ├── erp.py, fetch_erp_data.py   Equity risk premium
│   ├── margin.py            Margin-balance leverage indicator
│   ├── limitboard.py        Limit-up/down + streak reconstruction
│   ├── sectors.py           Per-sector ASI (5 lines: Shanghai Composite / Shenzhen Component /
│   │                        ChiNext / STAR 50 / CSI 2000)
│   ├── robustness_st.py     Survivorship-bias / ST-exclusion robustness check
│   ├── confirm.py           The (backtested-negative) asymmetric confirmation rule
│   ├── history.py           Builds/loads data/asi_history.csv, the append-only daily series
│   ├── backtest.py          Bucket tests, block-bootstrap significance, confirmation-rule and
│   │                        position-sizing backtests
│   ├── report.py            The full v1-vs-v2 validation report (python asi/report.py)
│   ├── actionable.py        The lag-aware, hysteresis-based tactical-sleeve strategy
│   ├── report_actionable.py Its backtest report (python asi/report_actionable.py)
│   ├── run_daily.py         One command: fetch -> score -> append to history -> print
│   ├── check_signal.py      Machine-readable daily signal check (for the scheduled task)
│   ├── download.py          One-off bulk data pull into the disk cache
│   └── data/
│       ├── asi_history.csv          the full daily series (v1 and v2 scored side by side)
│       ├── sector_*.csv             per-sector ASI series
│       ├── cn10y_5y.csv, hs300_pe_5y.csv, margin_account_info.csv   raw source data
│       └── cache/                   gzip HTTP cache (gitignored)
├── tests/test_compute.py    61 unit tests
├── sentiment_index_v1.py    the original prototype, kept ONLY so v2 can be
│                             scored against it on identical inputs for comparison
├── .claude/skills/asi-signal-check/SKILL.md   packages check_signal.py as a Claude Code skill
└── requirements.txt
```

## What's still open

- ERP only has ~5 years of history with percentile (not absolute) anchors --
  a longer, reliable bond-yield source would let it graduate to the same
  kind of absolute anchor the below-book-value rate has.
- Sector breadth/valuation use code-prefix approximations for sector
  membership, not an official constituent list -- CSI 2000 in particular has
  no constituent-based dimensions at all (coverage reads honestly low there
  rather than faking data).
- Survivorship-bias correction only compares an ST-included vs. ST-excluded
  sample over the last 5 years (by current name, since there's no
  year-by-year historical ST list) -- the pre-2021 series is unadjusted.
- The 60/80 zone recalibration and the tactical-sleeve strategy are both
  validated on the same historical window they were designed against; an
  out-of-sample period further in the future would be the real test.
