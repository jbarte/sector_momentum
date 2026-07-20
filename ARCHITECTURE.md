# Sector Momentum Scanner -- Architecture

> Last updated: 2026-07-11. This document describes the system as it is
> actually built, not the original v1 plan.

---

## 1. What it does

A daily automated pipeline that:

1. Fetches price data for 11 US SPDR sector ETFs and 11 STOXX Europe 600
   sector ETF proxies (some multi-component composites), plus benchmarks.
2. Computes a set of momentum/technical signals per sector.
3. Z-scores all signals cross-sectionally, then rolls them into a composite
   score (level + change sub-scores).
4. Compares against the prior scan to produce rank deltas, composite deltas,
   and an "emerging" flag.
5. Persists everything to Supabase (Postgres).
6. Builds a static HTML dashboard and publishes it to GitHub Pages.

A parallel **thematic ETF** track (`config/themes.yaml`) runs the same scoring
engine over a universe of genre/theme ETFs (AI, defence, clean energy, etc.)
with its own z-score cohort and leaderboard.

---

## 2. Universe

Defined in `config/universe.yaml`.

**US sectors** -- one SPDR Select Sector ETF per GICS sector (XLK, XLF, XLE,
XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC). Benchmark: **RSP** (equal-weight
S&P 500).

**EU sectors** -- iShares STOXX Europe 600 sector UCITS ETFs (`.DE` tickers on
XETRA; Real Estate via `IPRP.L`). Financials and Materials are **equal-weight
composites** of multiple sub-sector ETFs (Banks + Financial Services + Insurance;
Basic Resources + Chemicals). Benchmark: **EXSA.DE** (iShares STOXX Europe 600).

**Themes** -- defined in `config/themes.yaml`, one US-listed ETF per theme
(e.g. ITA, UFO, ICLN, ARKK), scored against ACWI (SPY fallback) as a single
global benchmark.

Both universes map to the 11 GICS sectors. EU and US sectors are scored within
their own region cohort; themes form a third independent cohort.

---

## 3. Data sources

| Need | Source | Notes |
|---|---|---|
| Daily price/volume | **stooq** (primary, no key), **yfinance** (fallback) | `src/data/prices.py`; cache-aggressive |
| US constituent breadth | Wikipedia S&P 500 list + price data | `src/data/constituents.py` + `src/signals/breadth.py`; % above 50-DMA per US sector; EU = N/A |
| News sentiment | **GDELT** headlines + **ProsusAI/finbert** | `src/data/news_sentiment.py`; per-GICS-sector signed polarity, z-scored across sectors; sub-sectors inherit their GICS parent's score |
| Macro context | **FRED** | `src/data/macro.py`; rates, yield curve (not currently wired into scoring) |

**Removed sources:** Reddit/PRAW, Finnhub, StockTwits, and Google Trends (all
removed; StockTwits blocked by Cloudflare, Reddit never shipped past stub,
Finnhub US-only free tier, Google Trends 429-blocked from CI and superseded by
FinBERT 2026-07-19).

---

## 4. Signal layer

All signals are computed in `src/pipeline.py` (`build_signals_rows` /
`build_theme_signals_rows`) using calculators in `src/signals/`.

### Per-sector signals (`SIGNAL_COLUMNS`)

| Signal | Module | Role |
|---|---|---|
| `rs_ratio` | `signals/relative_strength.py` | RRG RS-Ratio (level of relative strength vs benchmark) |
| `rs_momentum` | `signals/relative_strength.py` | RRG RS-Momentum (rate of change of RS; configurable fast period, default 5) |
| `return_1m`, `return_3m`, `return_6m` | `signals/momentum.py` | Multi-horizon absolute returns |
| `acceleration` | `signals/momentum.py` | 1M return minus 3M return (second derivative) |
| `above_50dma`, `above_200dma` | `signals/technical.py` | Price distance from 50/200-day moving averages |
| `ma50_slope` | `signals/technical.py` | Slope of the 50-DMA |
| `obv_slope` | `signals/technical.py` | Slope of on-balance volume |
| `breadth_above_50dma` | `signals/breadth.py` | True constituent breadth (US only; EU = NaN) |

EU STOXX sub-sectors (Banks, Financial Services, Insurance, Basic Resources,
Chemicals) are scored as standalone sectors; each maps to its GICS-11 parent
via `stoxx_to_gics` (`src/sector_map.py`) for consumers that key by GICS name
(FinBERT sentiment, Swedish-ticker matching).

### Sentiment signal (info-only)

`src/data/news_sentiment.py` fetches the last 24h of English headlines per
GICS-11 sector from GDELT, scores each with ProsusAI/finbert, and reduces to a
per-sector mean signed polarity z-scored across sectors. The z-score is stored
as `sentiment_score`; the per-sector `news_polarity`/`news_count`/
`news_positive_pct`/`news_negative_pct` rows go to `sentiment_signals` and are
shown on `docs/sentiment.html`. Sentiment does **not** affect the canonical
composite score (the dashboard offers a client-side toggle to blend it in). If
GDELT or FinBERT is unavailable, `sentiment_score` is left NULL for that scan.

---

## 5. Scoring (`src/scoring.py`)

1. **Cross-sectional z-score** each signal across all sectors in the cohort
   (NaN-safe: stats computed on non-NaN values, missing z-scores filled with 0.0).
2. **Level score** = mean z of `rs_ratio`, `return_3m`, `return_6m`,
   `above_50dma`, `above_200dma`.
3. **Change score** = mean z of `rs_momentum`, `acceleration`, `ma50_slope`,
   `obv_slope`.
4. **Data score** = 0.50 * level + 0.50 * change (configurable in
   `config/weights.yaml`).
5. **Composite** = data score (sentiment blending is off by default;
   `blend_sentiment=False`).
6. **Rank** 1..N by composite (1 = best).

Weights live in `config/weights.yaml`. Signal parameters (e.g.
`rs_momentum_fast`) are also configured there.

---

## 6. State & persistence (`src/state.py`)

**Storage: Supabase (Postgres)** via `psycopg2`. Connection string from
`DATABASE_URL` env var.

### Tables

| Table | Content |
|---|---|
| `scans` | One row per scan run (`scan_id`, `run_at`, `config_hash`) |
| `signals` | Long-format: one row per (scan, region, sector, signal_name) with `raw_value` and `z_value` |
| `scores` | One row per (scan, region, sector) with `level_score`, `change_score`, `data_score`, `sentiment_score`, `composite`, `rank`, deltas, `emerging_flag` |
| `sentiment_signals` | FinBERT news signals per (scan, region, sector, signal_name); historical Google Trends rows retained but no longer written |
| `theme_scores` | Same shape as `scores` but for thematic ETFs |
| `theme_signals` | Same shape as `signals` but for thematic ETFs |

**Idempotency:** same-UTC-day scans are replaced (not duplicated).

**Deltas:** each scan computes `delta_composite`, `delta_rank`, and
`emerging_flag` (requires >= 2 consecutive improving scans) by joining against
the most recent prior scan.

---

## 7. Backups (`src/backup.py`, `src/storage_backup.py`)

Before each scan, a full zip of all tables is uploaded to a private Supabase
Storage bucket (`db-backups`). Requires `SUPABASE_SERVICE_KEY`. Restore via
`python restore.py` (latest) / `--list` / `--local <dir>`.

---

## 8. Backtest (`src/backtest/`)

A monthly top-N rotation backtest (`engine.py`, `strategy.py`, `metrics.py`)
evaluates whether the scoring model would have caught past rotations early.
Supports `--cost-bps` for transaction costs, stale-price guards, and curated
rotation event-studies (`config/rotations.yaml` -> `rotations.py`). Results
are persisted to `backtests/` and rendered in the dashboard's Backtest tab.

---

## 9. Dashboard (`dashboard/`)

`dashboard/build.py` reads the DB and renders a static site into `docs/` using
**Jinja2** templates and embedded **Plotly** figures (JSON + plotly-basic
bundle). The site is self-contained and offline-capable.

### Pages

- **Sectors** (`docs/index.html`) -- Leaderboard, RRG rotation plot, Drill-down,
  Movers, History, Backtest, and Guide tabs. EN/SV language toggle.
- **Themes** (`docs/themes.html`) -- same tab structure for thematic ETFs.
- **Sentiment** (`docs/sentiment.html`) -- FinBERT news-sentiment dashboard
  (info-only, separate from sector scoring).
- **Per-scan reports** (`docs/reports/report_<scan_id>.md`) -- Markdown
  snapshots (incrementally generated; existing reports are not regenerated).

### Key build steps

1. Load scan history and latest scores from DB.
2. Build Plotly figures (RRG scatter with tails, movers bar chart, history
   lines, backtest equity curves).
3. Compute rank trajectories and deltas.
4. Render Jinja2 templates with embedded figure JSON and score data.
5. Copy Plotly JS bundle and CSS assets.
6. Write `docs/.nojekyll` so GitHub Pages serves the static output as-is.

---

## 10. CI/CD (`.github/workflows/`)

| Workflow | Trigger | What it does |
|---|---|---|
| `scan.yml` | Daily cron (`0 6 * * *` UTC) + manual | Runs `pytest`, then `scan.py --no-dashboard`, then `dashboard/build.py`, deploys `docs/` as a Pages artifact |
| `build-docs.yml` | Push to `main` when `dashboard/`, `src/`, `config/`, `backtests/`, or `requirements.lock` change + manual | Rebuilds `docs/` and redeploys the Pages artifact |
| `test.yml` | Push to `main`/`feature/**`/`fix/**`/`chore/**` + PRs to `main` | Runs `pytest` |
| `code-review.yml` | PRs | Automated code review via Claude |

`scan.yml` and `build-docs.yml` share a `pages-deploy` concurrency group so
their Pages deployments don't race.

**Generated artifact policy:** `docs/` is gitignored, not committed. Each
workflow rebuilds it from the database on every run and deploys it directly
via `actions/upload-pages-artifact` + `actions/deploy-pages`. See
`design/specs/2026-07-20-pages-artifact-deploy-design.md`.

---

## 11. Data flow

```
config/universe.yaml ──┐
config/themes.yaml ────┤
config/weights.yaml ───┤
                       ▼
                   scan.py
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    stooq/yfinance  GDELT+FinBERT  S&P 500
     (prices)      (sentiment)    (breadth)
         │             │             │
         ▼             ▼             ▼
     src/signals/   news_        breadth.py
     momentum.py    sentiment.py
     relative_strength.py
     technical.py
         │             │             │
         └──────┬──────┘─────────────┘
                ▼
          src/pipeline.py  (build_signals_rows)
                │
                ▼
          src/scoring.py   (z-score -> level/change -> composite -> rank)
                │
                ▼
          src/state.py     (save to Supabase/Postgres)
                │
                ▼
       dashboard/build.py  (Jinja2 + Plotly -> docs/)
                │
                ▼
         GitHub Pages      (https://jbarte.github.io/sector_momentum/)
```

---

## 12. Module index

| Path | Purpose |
|---|---|
| `scan.py` | Pipeline entrypoint: config -> prices -> signals -> scoring -> DB -> report -> dashboard |
| `src/pipeline.py` | Signal-row builders (pure functions over price dicts) |
| `src/scoring.py` | Cross-sectional z-scoring, level/change/composite/rank |
| `src/state.py` | Supabase/Postgres DDL, read/write, delta computation |
| `src/report.py` | Markdown report generation (ranked table, movers, Swedish overlay) |
| `src/backup.py` | CSV-dump backup helpers |
| `src/storage_backup.py` | Supabase Storage upload/download for DB backups |
| `src/data/prices.py` | stooq + yfinance price fetcher with caching and fallback |
| `src/data/news_sentiment.py` | GDELT headlines + ProsusAI/finbert signed polarity, z-scored per GICS sector |
| `src/sector_map.py` | STOXX sub-sector -> GICS-11 parent map (identity fallback) |
| `src/data/constituents.py` | S&P 500 constituent list (Wikipedia scrape) |
| `src/data/macro.py` | FRED macro data loader |
| `src/signals/momentum.py` | Returns and acceleration |
| `src/signals/relative_strength.py` | RRG RS-Ratio and RS-Momentum |
| `src/signals/technical.py` | MA distances, MA slope, OBV slope |
| `src/signals/breadth.py` | Constituent % above 50-DMA |
| `src/backtest/engine.py` | Monthly rotation backtest engine |
| `src/backtest/strategy.py` | Top-N strategy with transaction costs |
| `src/backtest/metrics.py` | Backtest performance metrics |
| `src/backtest/rotations.py` | Curated rotation event-study replay |
| `src/backtest/results.py` | Backtest result persistence |
| `src/backtest/replay.py` | Point-in-time score replay |
| `dashboard/build.py` | Static dashboard builder (Jinja2 + Plotly -> docs/) |
| `dashboard/templates/` | HTML/JS Jinja2 templates |
| `config/universe.yaml` | Sector ETF tickers and benchmarks |
| `config/weights.yaml` | Pillar weights, signal params |
| `config/themes.yaml` | Thematic ETF universe |
| `config/sector_map.yaml` | STOXX -> GICS mapping |
| `config/sector_etfs.yaml` | Reference UCITS ETFs for the instruments panel |
| `config/rotations.yaml` | Curated historical rotation events for backtesting |

---

## 13. Tech stack

Python 3.11+, `pandas`, `numpy`, `scipy`, `psycopg2` (Postgres), `transformers`
+ `torch` (FinBERT news sentiment), `plotly` + `jinja2` (dashboard), `pyyaml`,
`requests`, `python-dotenv`. See `requirements.txt` for runtime deps and
`requirements-dev.txt` for test deps; exact pins in `.lock` files.

Hosting: **Supabase** (Postgres + Storage), **GitHub Actions** (CI/CD),
**GitHub Pages** (dashboard). All free tier.
