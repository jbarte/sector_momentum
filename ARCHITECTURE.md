# ETF Momentum -- Architecture

> Last updated: 2026-08-09. Describes the system as it is actually built.
>
> The US SPDR / STOXX Europe 600 **sector** cohorts this project started as were
> retired on 2026-08-05. Themes are now the only scoring cohort. If you are
> reading a document that mentions GICS sectors, breadth, or a "parallel
> thematic track", it predates that change.

---

## 1. What it does

A daily automated pipeline that:

1. Fetches daily price data for every theme ETF plus the benchmark.
2. Pins every ticker to one shared **as-of date** before scoring.
3. Computes eight price-based signals per theme.
4. Z-scores all signals **cross-sectionally** (across themes, same day), then
   rolls them into Level / Change sub-scores and a composite.
5. Ranks 1..N, and diffs against the prior scan for rank/composite deltas.
6. Persists everything to Supabase (Postgres).
7. Builds a static dashboard and publishes it to GitHub Pages.
8. Emits band-crossing alerts.

---

## 2. Universe

Defined in `config/universe.yaml` (scan-wide settings only) and
**`config/themes.yaml`** (the universe itself).

One US-listed ETF per theme, scored against **ACWI** as a single global
benchmark (falls back to SPY if ACWI is missing from the price dict). 18 themes
as of 2026-08-09.

`themes.yaml` also records, per theme, the closest **UCITS** equivalent —
ticker, ISIN, TER, issuer, and a `match` quality of `exact` / `close` /
`partial`. These are reference only and never scored: the pipeline uses the US
listing for history and liquidity, while a European investor actually buys the
UCITS one. The `match` field exists because that gap is real — several themes
have only a `partial` equivalent, and one (Shipping) has none.

There is exactly **one cohort**, `THEME` (`src/cohorts.py`). This matters more
than it looks: composites are z-scored *within* a cohort and are meaningless
across cohorts, so anything that pools two cohorts into one ranking is a bug.

---

## 3. Data sources

| Need | Source | Notes |
|---|---|---|
| Daily price/volume | **yfinance** | `src/data/prices.py`; parquet cache per ticker, aggressive freshness rules |
| News sentiment | **GDELT** headlines + **ProsusAI/finbert** | `src/data/news_sentiment.py`; per-theme keyword queries, signed polarity z-scored across themes. **Info-only** |
| Macro context | **FRED** | `src/data/macro.py`; header chips only, affects no score |

**Single source of price truth.** stooq was the second leg of a two-source
fallback until 2026-08-09, when it was retired: its CSV endpoint now requires
solving a JavaScript proof-of-work challenge that no HTTP client can pass. That
makes yfinance a single point of failure — see BACKLOG.

**Removed sources:** Reddit/PRAW, Finnhub, StockTwits, Google Trends,
S&P 500 constituent breadth (all removed; the breadth signal has no constituent
list for thematic ETFs and is now always NaN).

### `end` is EXCLUSIVE

`fetch_prices(start, end)` returns bars strictly *before* `end`. Callers pass
`end=today`, which is what keeps an in-progress session out of the data —
Yahoo returns a partial candle during market hours, and the cache freshness
check only looks at the *date*, so a half-formed close would be cached and
never refetched.

### Cohort as-of alignment

Cache freshness is decided **per ticker**, so a dict returned by `fetch_prices`
can legitimately mix as-of dates — one refetched ticker beside 19 cache hits is
enough. Since the composite z-scores *across* the cohort, that would rank one
theme's Tuesday reading against another's Wednesday.

`align_cohort_asof()` pins every ticker to one date: the newest date every kept
ticker has a bar for. A ticker lagging the cohort's modal date by more than
`MAX_ASOF_LAG_DAYS` (4) is **dropped** rather than dragging the whole cohort
back to its date; the coverage guard in `scan.py` catches it if that spreads.

---

## 4. Signal layer

Computed in `src/pipeline.py` (`build_theme_signals_rows`) using calculators in
`src/signals/`. All are pure functions over a `{ticker -> OHLCV DataFrame}`
dict with no notion of "now", so the backtest drives them as-of any historical
date by truncating prices.

### Scored signals

| Signal | Module | Pillar |
|---|---|---|
| `rs_ratio` | `relative_strength.py` | Level |
| `return_3m`, `return_6m` | `momentum.py` | Level |
| `above_50dma` | `technical.py` | Level |
| `rs_momentum` | `relative_strength.py` | Change |
| `return_1m` | `momentum.py` | Change |
| `ma50_slope` | `technical.py` | Change |
| `obv_slope` | `technical.py` | Change |

The two pillar lists are **hardcoded** in `src/scoring.py`
(`_LEVEL_SIGNALS` / `_CHANGE_SIGNALS`), equal-weighted within each pillar. The
`level_signals:` / `change_signals:` keys in `weights.yaml` control *column
order in the dashboard only*; their values are ignored.

### Computed but not scored

`acceleration`, `above_200dma`, `max_dd_1y`, `rar_3m`, `rar_6m`, `calmar_6m`
are computed, stored and surfaced in the drill-down as context.
`acceleration` (= `return_1m - return_3m`) was a *scored* Change signal until
2026-08-09; because `return_3m` is simultaneously a Level input, it carried
that return with opposite signs in the two pillars and correlated **-0.31 with
the composite it belonged to**. `return_1m` replaced it — which removes only
the `- return_3m` term, since `return_1m` already entered positively inside
`acceleration`.
`breadth_above_50dma` is always NaN (no constituent list for a thematic ETF)
and is a leftover column.

### Sentiment (info-only)

GDELT headlines for each theme's own keyword set (up to 250, last 24h), scored
by FinBERT, reduced to a per-theme mean signed polarity, z-scored across themes.
Stored as `sentiment_score`, rendered on `docs/sentiment.html`, and **excluded
from the canonical composite** (`blend_sentiment=False`) — the dashboard offers
a client-side toggle to blend it at a chosen weight. Unavailable source leaves
it NULL for that scan.

---

## 5. Scoring (`src/scoring.py`)

1. **Cross-sectional z-score** each signal across the cohort. NaN-safe: stats
   are computed on non-NaN values, and the resulting z for a NaN input is 0.0
   (neutral). Filling raw NaN with 0.0 *before* standardising would turn a
   signal centred far from zero, like `rs_ratio` ~100, into a fake outlier.
2. **Level** = mean z of the four Level signals.
3. **Change** = mean z of the four Change signals.
4. **Data score** = `0.50 * level + 0.50 * change` (`data_pillar` in weights).
5. **Composite** = data score (sentiment weight 0 by default).
6. **Rank** 1..N, ties by average rank.

---

## 6. Horizons and trading cost (`src/horizons.py`)

A horizon preset is a `(rebalance cadence, top_n, buffer)` triple. `buffer` is
a hysteresis band **in ranks**: a holding is kept while `rank <= top_n + buffer`.

**Two presets ship, both on a monthly cadence**, differing only in band width:
`medium` = M/4/5 (band 50% of the universe) and `long` = M/5/8 (72%). A third,
weekly preset was removed 2026-08-14: swept on two independent windows, every
weekly cell was dominated, and the five best cells overall were monthly or
bi-weekly with a 50-67% band. Cadence contributes little; band width does the
work. `tests/test_horizons.py` asserts the presets share one cadence, so
re-introducing that dimension fails a test rather than going unnoticed.

Note `long` holds *more* names than `medium` (5 vs 4), which is deliberate:
concentration is a risk choice and holding period is a horizon choice, and the
old three-preset lineup conflated them.

This module is the single source read by three places that must agree, or the
dashboard would describe a strategy the backtest never ran:

- `backtest.py` — replays every preset into `backtests/`
- `dashboard/rows.py` — derives the server-rendered band state
- `dashboard/assets/rescore.js` — re-derives it client-side on switch

Client-side, `applyHorizonBadges()` in `index.html.j2` is the **single writer**
of every leaderboard badge; `Rescore.setupForRank` / `badgeForRank` /
`inBuyBand` are the shared rules it and every rebuild path call. Four separate
copies of `rank <= 3` for the highlighted rank badge had already drifted out of
step with `top_n` before they were collapsed into `inBuyBand`.

`round_trip_bps()` reads `costs.round_trip_bps` from the same file. `backtest.py`
and `scripts/horizon_sweep.py` both default from it, so the figures shown beside
a preset and the sweep that *chooses* presets share one assumption. It defaults
non-zero deliberately: sweeping at 0 bps systematically favours whichever
cadence trades most, which is how the pre-2026-08-09 presets were selected.

**The band does not scale itself.** It is stored in absolute ranks, so changing
the universe size silently changes the band *fraction*
`(top_n + buffer) / n_themes` — growing 13 → 20 themes once tightened it from
62% to 40% and tripled churn. Revisit the presets whenever the universe changes.

---

## 7. State & persistence (`src/state.py`)

**Supabase (Postgres)** via `psycopg2`, connection string from `DATABASE_URL`.

| Table | Content |
|---|---|
| `scans` | one row per run (`scan_id`, `run_at`, `config_hash`) |
| `signals` | long format: one row per (scan, region, theme, signal) with `raw_value` and `z_value` |
| `scores` | one row per (scan, region, theme): level / change / data / sentiment / composite / rank |
| `sentiment_signals` | FinBERT signals per (scan, region, theme, signal) |

`region` is the cohort discriminator and currently always `THEME`. It reads as
a legacy name, but it is **load-bearing**: retired US/EU sector rows are still
in these tables, and `region` is the filter that keeps them out of every read.

Two further tables — `positions` and `alert_prefs` — plus the `v_recent_scores`
view are **managed Supabase-side**, not by this repo's DDL. They back the
signed-in features (starred holdings, per-user alert preferences) and are read
directly from the browser under RLS.

**Idempotency:** a same-UTC-day scan replaces the previous one rather than
duplicating it.

**Deltas** (`delta_composite`, `delta_rank`, `emerging_flag`) are computed
against the prior scan. Note the dashboard recomputes its own rank delta at
build time from the last two scans in the rendered window — see BACKLOG for why
that is wrong on weekends.

---

## 8. Content gating

`dashboard/gating.py`. Guests see the newest scan at least `LAG_DAYS` (7) old,
plus a banner saying so. Authenticated users are upgraded to the live scan
**client-side** (`dashboard/assets/auth.js`), which re-queries `v_recent_scores`
and rebuilds the leaderboard rows. Sign-in is invite-only Supabase magic link;
signing out reloads the baked page, which *is* the gated state.

---

## 9. Backtest (`src/backtest/`)

Replays the scoring pipeline as-of each rebalance date, selects top-N under the
hysteresis rule, and compares to the benchmark.

- `replay.py` — rebalance calendars (`W`/`2W`/`M`/`2M`/`Q`), as-of scoring, and
  the fetch-versus-evaluation window split described below
- `strategy.py` — selection with hysteresis, turnover, transaction cost, churn stats
- `metrics.py` — CAGR, Sharpe, drawdown, hit rate. `periods_per_year()` is
  derived from the actual date spacing; leaving it at the monthly default would
  treble a quarterly track's CAGR
- `engine.py` — per-track orchestration
- `results.py` — persistence to `backtests/`

### Fetch window vs evaluation window

**Price history is always fetched from `replay.FETCH_START`; `--start` bounds
only which dates are evaluated**, via `rebalance_dates(..., since=)`. The two
are separate because signals with a trailing lookback are NaN until it fills —
`compute_ma_structure` needs 200 bars for `above_200dma` — so evaluation
beginning on the first fetched bar scores its opening months on a degraded
signal set.

This is not hypothetical. Both entry points passed the evaluation start
straight to `fetch_prices`, and in `scripts/horizon_sweep.py` it **inverted the
horizon preset ranking**: starved, `M/5/7` beat `M/5/4` by 1.9pp CAGR; warm, it
lost by 0.8pp. The two harnesses disagreed by 2.1pp on an identical cell, which
is how the bug was found. The filtering happens *after* period grouping, so a `2M` calendar cannot
silently shift onto the other month parity.

**What the guard does and does not promise.** `validate_eval_start()` rejects an
evaluation start closer than `WARMUP_DAYS` to `FETCH_START` — i.e. it proves the
*fetch* was not truncated. It cannot promise every ticker is warm, because
warm-up is bounded by each fund's own inception, not by the fetch: at the
default run's first evaluated date (2008-03-31, set by ACWI's inception) only 7
of 18 tickers have 200 bars behind them. The other 11 did not exist yet. That is
the same limitation the dashboard states as "today's universe is replayed
backwards", and no fetch window can fix it — `score_calendar`'s
`min_members=top_n` is what keeps a thin date from producing a track.

`backtest.py` additionally refuses to overwrite the committed `backtests/`
artifact with a windowed run, and refuses to write at all when every track came
back empty — that used to replace a good artifact with nulls and exit 0.

Read the results with the caveats the dashboard states: today's universe is
replayed backwards (several ETFs did not exist in 2008), and the presets were
fitted on this same history.

---

## 10. Dashboard (`dashboard/`)

`build.py` reads the DB and renders a static site into `docs/` with **Jinja2**
and embedded **Plotly** figures. Self-contained and offline-capable.

Split into focused modules: `rows.py` (leaderboard rows + badges),
`figures.py` (Plotly), `breakdown.py` (drill-down panel), `correlation.py`
(heatmap), `sentiment.py`, `macro.py`, `health.py`, `validation.py`,
`badges.py` (scorecard), `gating.py`, `reports.py`,
`data_export.py`.

### Pages

- **`docs/index.html`** — Leaderboard, RRG, Drill-down, Movers, History,
  Backtest, Correlation. EN/SV toggle.
- **`docs/sentiment.html`** — FinBERT news sentiment (info-only).
- **`docs/reports/report_<scan_id>.md`** — per-scan Markdown snapshots.

### i18n

`templates/i18n/*.js.j2`. Strings carrying markup **must** go in `SV_HTML`
(applied via `innerHTML`) and use `data-i18n-html`; plain strings go in `SV`
(applied via `textContent`). Putting markup in `SV` renders literal tags as
visible text.

---

## 11. Alerts

`src/alerts.py` detects **band crossings** — a theme entering `rank <= top_n`
or leaving `rank > top_n + buffer` — not band membership. Membership would
re-send the same holdings every day. `src/personal_alerts.py` routes events to
users by their starred positions and preferences. Alerts use the **default**
horizon; per-user horizon is queued.

---

## 12. Backups

Before each scan, a full zip of all tables is uploaded to the private Supabase
Storage bucket `db-backups`. Requires `SUPABASE_SERVICE_KEY`. Restore with
`python restore.py` (latest) / `--list` / `--local <dir>`.

A second private bucket, `trends-cache`, holds a durable day-cache so
re-triggered scans reuse already-fetched batches. Fail-open: a missing bucket
or key only means scans run uncached.

---

## 13. CI/CD (`.github/workflows/`)

| Workflow | Trigger | What it does |
|---|---|---|
| `scan.yml` | daily cron `0 6 * * *` UTC + manual | `pytest`, then `scan.py --no-dashboard`, then `dashboard/build.py`, deploys `docs/` as a Pages artifact |
| `build-docs.yml` | push to `main` touching dashboard/src/config/backtests + manual | rebuilds and redeploys the Pages artifact |
| `test.yml` | pushes and PRs | `pytest`, including DB-backed tests against a `postgres:17` service container |
| `code-review.yml` | PRs | automated review |

`scan.yml` and `build-docs.yml` share a `pages-deploy` concurrency group so
their deployments don't race.

**`docs/` is gitignored, not committed.** Each workflow rebuilds it from the
database and deploys directly via `upload-pages-artifact` + `deploy-pages`.

Note the daily cron runs seven days a week against a five-day market, so
weekend scans are exact duplicates of Friday's.

---

## 14. Data flow

```
config/themes.yaml ────┐
config/weights.yaml ───┤
                       ▼
                   scan.py
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
    yfinance                  GDELT + FinBERT
    (prices)                    (sentiment)
         │                           │
         ▼                           │
  align_cohort_asof                  │
   (one as-of date)                  │
         │                           │
         ▼                           │
   src/pipeline.py                   │
  (8 signals/theme)                  │
         │                           │
         ▼                           │
   src/scoring.py  ◄─────────────────┘  (stored, not blended)
 (z -> level/change -> composite -> rank)
         │
         ▼
   src/state.py ──────────► Supabase/Postgres
         │
         ├──────────────► src/alerts.py (band crossings -> email)
         ▼
  dashboard/build.py ────► docs/ ────► GitHub Pages
```

---

## 15. Tech stack

Python 3.11+, `pandas`, `numpy`, `scipy`, `psycopg2`, `transformers` + `torch`
(FinBERT), `plotly` + `jinja2`, `pyyaml`, `requests`, `python-dotenv`,
`pyarrow`, `fredapi`, `lxml`. Runtime deps in `requirements.txt`, test deps in
`requirements-dev.txt`, exact pins in the `.lock` files.

Hosting: **Supabase** (Postgres + Storage + Auth), **GitHub Actions**,
**GitHub Pages**. All free tier.
