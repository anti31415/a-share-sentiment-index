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
python history.py 900                # rebuilds data/asi_history.csv from scratch (~5,262 rows)
python history.py --rescore          # re-scores the committed history after a model change (seconds)
python run_daily.py                  # today's reading, human-readable
python check_signal.py               # today's reading, machine-readable + trade trigger
python report.py                     # the full v1-vs-v2 validation report
python report_actionable.py          # the tactical-satellite-sleeve backtest report
python ../tests/test_compute.py      # 68 unit tests
```

Nothing here needs an API key. `asi/data/cache/` holds a gzip cache of every
HTTP response so re-running any script is fast and doesn't re-hit the
network; delete it to force a refetch.

## What it actually is

A 0-100 score built from **five weighted dimensions**, each in turn built
from one or more raw indicators:

| Dimension | Weight | Members | What it measures |
|---|---|---|---|
| Valuation despair | 55% | below-book-value rate, equity risk premium (ERP) | how cheap the market is |
| Trading cooldown | 22% | volume-price temperature, 50ETF implied volatility (QVIX) | is anyone still trading, and how much fear is priced into options |
| Price momentum | 6% (inverted) | RSI(14), BIAS(60), drawdown depth (median of the three) | trend continuation, counted against the reversion signal |
| Leverage sentiment | 11% | margin (financing) balance 5-day change, financing buy ratio | is leveraged money adding or being forcibly liquidated, and how actively |
| Speculation extremity | 6% | limit-up share, limit-down share, longest limit-up streak | how frothy or panicked is the tape |

A sixth dimension, market breadth (advancers / new-low / new-high share),
was removed on 2026-09-17 -- see round 5 below.

**2026-09 round 3:** added `iv_temp` (50ETF QVIX), `new_high_rate`, and
`margin_buy_ratio` per a user follow-up request (dimension weights
unchanged, each slots into an existing dimension as an additional member).
Two other candidates from the same request were tested and *not* added: a
combined financing+securities-lending balance correlates 0.998 with the
existing financing-only balance and added nothing; a true financing-buy /
whole-market-turnover ratio isn't reconstructable from data this project
already fetches (Sina's index klines carry share volume, not turnover
value), so `margin_buy_ratio` is defined against the financing balance
instead. Effect, measured against the same forward-return backtest: the
raw rank correlation with the forward-120-day return got very slightly
*weaker* (Spearman -0.263 -> -0.256, a ~3% relative loss) -- these three
indicators add a bit of broad-sample noise. But the metric that actually
matters for this project -- the tactical satellite-sleeve backtest below --
improved: CAGR 2.99% -> 3.28%, Calmar 0.234 -> 0.253, **and trade count
dropped from 32 to 18** (fewer false hysteresis triggers), because iv_temp
in particular smooths out some single-day whipsaw in the 5-day-smoothed
signal. Kept for that reason, with the trade-off stated plainly rather
than cherry-picking whichever number looks better.

0 = extreme panic / blood in the streets. 100 = extreme greed / the fat tail
of a rally. Full anchor tables and the reasoning behind every weight are in
`asi/indicators.py`.

### Zone boundaries (recalibrated by data, not intuition)

| Score | Zone | What the backtest actually shows there |
|---|---|---|
| 0-15 | Ice-cold | fwd 120d **+23.2%**, win rate 94.1% |
| 15-30 | Pessimistic | fwd 120d **+21.7%**, win rate 90.4% |
| 30-60 | Neutral | fwd 120d +7.1%, win rate 59.0% |
| 60-80 | **Optimistic ("no edge")** | fwd 120d **-2.4%**, win rate **42.4%** -- the only bucket with a genuinely negative average forward return |
| 80-100 | Greed | fwd 120d +9.1%, win rate 59.0% -- momentum still runs, but historically the most volatile zone |

The original boundaries were (0, 15, 30, 70, 85, 100), set by intuition. A
rolling 150-day scan of forward-120-day returns across the full sample found
that the genuinely "no edge, forward returns often negative" zone is 60-80
-- the old scheme split that across the bottom of "optimistic 70-85" and the
top of "neutral" -- while the old "greed zone 85-100" actually has *positive*
forward returns (no top forms within a 120-day window) and is simply the
most volatile zone. The boundary moved to 60/80 to match what the data shows.
(The bucket numbers above reflect the 2026-09 momentum-dimension
reweighting described further down -- the 60/80 boundaries themselves held
up under a fresh population-percentile check against the new score's
distribution, so they weren't moved again; only the numbers inside each
bucket changed, and the "no edge" bucket got noticeably cleaner -- now
genuinely negative rather than barely positive.)

## What's provably true, and what isn't

The project's whole point is to not overclaim. Here's the honest ledger:

**Holds up under a real, non-self-referential backtest:**
- Full-sample Spearman correlation between ASI and the forward-120-day
  return: **-0.381** (v2, after the 2026-09 dimension-reweighting round) vs.
  **-0.130** (v1, the original flat six-indicator design) -- a meaningfully
  cleaner ranking signal.
- Signal-driven satellite-sleeve purchases (see below) averaged a purchase
  price **11.6% below** the plain period-average price.
- A moving-block bootstrap (2,000 iterations, block=120 days, to avoid the
  inflated significance overlapping windows create) puts the ice-cold
  bucket's excess forward return at **p ≈ 0.06** -- suggestive, not proof;
  21 years only produced 34 ice-cold days total.

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
  validated by backtest -- entering "Pessimistic (add)" below 28, entering
  "Optimistic (trim)" in 52-75 -- and only on the *first* crossing of a
  zone, not every day the reading stays there. The middle "no edge" band
  is left alone entirely.

Backtested over the last 10 years (2016-09 onward) with an 8-day execution
lag, the signal-driven tactical reserve beats a same-shaped always-cash,
always-invested, and 24-month-DCA benchmark on every metric -- CAGR,
max drawdown, and Calmar ratio. Full numbers, the exact state thresholds,
and an operating playbook are in `asi/report_actionable.py`'s output and
`.claude/skills/asi-signal-check/SKILL.md`.

**2026-09 round 4 (a user follow-up on weighting methodology):** the user
asked what statistical method should set factor weights -- logistic
regression, XGBoost, IC-weighting? Two real experiments came out of it,
both documented in `asi/indicators.py` and `asi/actionable.py`:

1. *Full IC/IR-weighted composite, tested properly out-of-sample* (weights
   fit on 2005-2020 only, evaluated purely on 2021-2026): looked spectacular
   in-sample (fwd120 Spearman -0.256 -> -0.470) and then **lost to the
   hand-set weights out-of-sample** (-0.363 vs -0.280). Not adopted --
   direct empirical evidence for why tree/regression-based factor weighting
   is risky here: ~21 years of daily data sounds like a lot, but the number
   of genuinely *independent* extreme-regime events (2015 leverage bull/bust,
   2018, 2020, 2024-02) is closer to a dozen, and a high-freedom model fits
   the specifics of those dozen rather than anything general.
2. *The price-momentum dimension's direction, re-examined the same way*:
   RSI(14)/BIAS(60)/drawdown were originally treated as contrarian
   (stretched -> expect reversion), but measured IC -- **including when fit
   on 2005-2020 only and checked purely out-of-sample on 2021-2026** -- says
   they're mild 120-day trend-*continuation* signals instead. This one
   generalized, so it shipped: the dimension's own reading is unchanged
   (still "high = market feels strong"), but its contribution to the
   composite is now inverted, its weight cut 20%->5%, and the 15 points
   freed went to the valuation dimension (35%->50%, by far the strongest
   and most robust factor by IC). Full-sample Spearman improved -0.256 ->
   -0.381, and the "no edge" bucket's forward return flipped from +0.9% to
   a genuinely negative -2.4%.

That weight change alone *broke* the hysteresis strategy above (its fixed
score thresholds were calibrated to the old score scale; trade count
collapsed from 18 to 8 and CAGR/Calmar both dropped), so the thresholds
were re-derived from a local return-curve scan on the strategy's own
2016-2026 operating window -- CAGR 3.03% -> **5.25%**, Calmar 0.191 ->
**0.498**, max drawdown -15.8% -> **-10.5%**. Stated plainly: **this
threshold recalibration is NOT out-of-sample validated** the way the
weight change above was -- a single ~10-year window with a couple dozen
trades doesn't leave enough data to hold out a test period and still have
anything to evaluate, so treat these specific numbers (28/40/52/75/46/76)
as a reasonable, data-informed estimate carrying real overfitting risk, not
a proven result, and revisit once more years of genuinely new data exist.

**2026-09-17 round 5 (direct whole-market data, no more stock sampling):**
the daily check used to scrape ~900 individual stocks every morning to
reconstruct the below-book-value rate, breadth, new lows/highs and
limit-up statistics -- the step behind every rate-limit crash of the cloud
routine, and ~6.5 minutes of its runtime. Three changes:

1. *Below-book-value rate* now comes straight from legulegu's whole-market
   series (2005-01 onward, one request) for **both history and the live
   reading**. The old sample reconstruction turned out to be
   survivorship-biased before ~2014 (the sample is drawn from today's
   listing): 2008-11 bottom 21.6% sampled vs 13.3% actual, 2012-12 11.8%
   vs 5.1%; from 2019 on the two agree within ~1pt. Anchors unchanged (they
   sit on the post-2019 regime; a percentile re-mapping tested no better).
2. *Market breadth dimension removed.* None of its members has a free
   whole-market daily source with history (new lows need a 252-day window;
   free endpoints stop at 120 days), and with the direct below-book series
   it wasn't earning its weight: removing it improved fwd60 and fwd120
   Spearman IC in **each** of 2005-10, 2011-15, 2016-20 and 2021-26
   separately. Its 10% was spread proportionally (55/22/6/11/6).
3. *Limit-up / limit-down / streak (live)* come from East Money's
   whole-market limit pools (~20 trading days served; history stays on the
   sample reconstruction, which matches closely: 2026-09-01 limit-up share
   1.59% direct vs 1.57% reconstructed, longest streak 7 vs 6). QVIX got a
   live tail and a staleness guard, like the margin data.

Measured on the same forward-return backtest (the "before" column is the
same model code scored on the old sample inputs):

| | before | after |
|---|---|---|
| fwd120 Spearman, 2016-09+ | -0.468 | **-0.519** |
| fwd120 Spearman, 2021+ | -0.502 | **-0.541** |
| fwd120 Spearman, full 2005+ | -0.381 | -0.337 |
| Satellite sleeve, 8-day lag (2016-09+): CAGR / max DD / Calmar | 4.86% / -10.54% / 0.46 | 4.85% / **-9.43%** / **0.51** |
| Daily check runtime | ~6.5 min (cloud) | ~40 s (local) |

The full-sample IC is lower only because the old pre-2014 below-book
readings were inflated in a way that happened to flag the 2005/2008/2012
bottoms more loudly -- the direct series is the true figure. The sleeve's
return is unchanged within noise (~22-29 trades over ten years); the gain is
in drawdown and in ranking power over the period the strategy actually
operates in. One side effect: the recalibrated scale almost never reaches
the ice-cold zone any more (6 days in 21 years, none since 2016), so the
zone boundaries are the obvious next thing to revisit -- not done here,
because re-deriving them on the same window is exactly the overfitting risk
flagged above.

**Caveat stated plainly:** over this particular 10-year window the smoothed
ASI never actually reached the two most extreme states (below 15, or above
~78) -- the score's own scale changed with the 2026-09 reweighting, so
exact historical range figures are due for a refresh; the qualitative point
stands: those extreme tiers exist as a safety net for market conditions
more severe than anything 2016-2026 produced, not as something this
specific backtest window proves works.

## Data sources (all free, no API key)

| Source | What it provides | History |
|---|---|---|
| Sina / Tencent / East Money daily klines | Unadjusted daily bars, index (daily check) & stock (history rebuild and sector lines only) | back to 2001 |
| legulegu `below-net-asset-statistics-data` | Whole-market count of A-shares below book value, and listed-company count | daily, 2005-01-05+ |
| East Money `push2ex` limit-up / limit-down pools | Whole-market limit-up and limit-down names, streak length | last ~20 trading days |
| Sina `hs_a` node | Full A-share listing + live PB (sampling for history rebuilds / sectors) | current snapshot |
| East Money `datacenter-web` (`RPT_F10_FINANCE_MAINFINADATA`) | Per-stock annual-report book value per share (sector lines only) | as reported |
| akshare `stock_margin_account_info` (wraps East Money) | Nationwide margin (financing) balance, securities-lending balance, financing buy amount | 2012-09-27+ |
| akshare `index_option_50etf_qvix` | SSE 50ETF options QVIX (China's rough VIX analogue) | 2015-02-09+ |
| China Central Depository & Clearing 10-year yield (via akshare `bond_china_yield`) | 10-year government bond yield | 2021-09+ (see note below) |
| legulegu `index-basic-pe` | CSI300 cap-weighted TTM PE | monthly, 2005-04+ |

**Known gap:** the 10-year bond yield's own history-query endpoint was tried
repeatedly and never returned reliably further back than 2021-09 (it seems
to expect an interactive browser session). This caps the ERP indicator to a
5-year window with percentile-based (not absolute) anchors -- documented in
`asi/erp.py`.

**Also known:** East Money's limit-up/limit-down pools only serve the last
~20 trading days, so they feed the live reading only; for the historical
backtest `asi/limitboard.py` reconstructs the same statistics from the same unadjusted daily bars used
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
│   ├── market_stats.py      Whole-market below-book rate and limit pools, read directly
│   ├── iv.py                50ETF QVIX implied volatility
│   ├── limitboard.py        Limit-up/down + streak reconstruction (history only)
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
│       ├── cn10y_5y.csv, hs300_pe_5y.csv, margin_account_info.csv,
│       │   qvix_50etf.csv, below_book_rate.csv                          raw source data
│       └── cache/                   gzip HTTP cache (gitignored)
├── tests/test_compute.py    68 unit tests
├── sentiment_index_v1.py    the original prototype, kept ONLY so v2 can be
│                             scored against it on identical inputs for comparison
├── .claude/skills/asi-signal-check/SKILL.md   packages check_signal.py as a Claude Code skill
└── requirements.txt
```

## What's still open

- ERP only has ~5 years of history with percentile (not absolute) anchors --
  a longer, reliable bond-yield source would let it graduate to the same
  kind of absolute anchor the below-book-value rate has.
- The per-sector lines (`sectors.py`) still score from code-prefix stock
  samples, including a sample-based below-book rate (the direct series only
  exists for the whole market and a few large indices), and their CSVs
  predate round 5's weights -- they need a rebuild before being compared
  with the composite.
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
