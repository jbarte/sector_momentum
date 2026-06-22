# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

---

## Data inventory & coverage statistics

**What:** A way to inspect what data we actually have in the database and summary
statistics about coverage — how much data, over what date range, and where gaps exist.

**Why:** As scans accumulate in Supabase, it's hard to know at a glance how complete
the dataset is — how many scans exist, which sectors/regions are covered, the earliest
and latest scan dates, and whether any runs are missing signals or scores.

**Possible scope:**
- Count of scans, and first/last `run_at` dates (overall and per region)
- Cadence check — average gap between scans, and any missing-day gaps vs the
  every-2-days schedule
- Per-sector / per-region coverage: how many scans each sector appears in
- Signal completeness: which `signal_name`s are present, and the count of
  NULL `raw_value` / `z_value` rows (data-quality view)
- Row counts per table (`scans`, `signals`, `scores`)

**Possible delivery:**
- A CLI command (e.g. `python scan.py --stats` or a small `stats.py` script) that
  prints the summary, querying Supabase via the existing `src/state.py` helpers
- Optionally surface the same numbers as a small panel/tab on the dashboard

**Notes:** Read-only — no schema changes needed. Builds directly on the
Supabase-backed `src/state.py` data layer.

---

## Sentiment module as a dedicated tab

**What:** Pull the sentiment functionality out of the core scoring pipeline and
present it as a separate dashboard tab.

**Why:** Sentiment should be excluded from the main scoring functionality and treated
as its own feature. Until that's built, a placeholder tab with "upcoming feature" info
is acceptable.

**Status:** Not started (tab/UI). The scoring engine, however, is **already
implemented but dormant** — see below.

**Current state of the sentiment code:**
- The module is fully built and unit-tested (`src/signals/sentiment.py`,
  `compute_sentiment_score`) but **not wired into the live scan**. `scan.py` calls
  `score_all(wide_df, ...)` without a `sentiment_score`, so `compute_composite`
  returns the pure data pillar and the stored `sentiment_score` column is `NaN`.
  `config/weights.yaml` declares `sentiment: 0.30`, but that weight is never applied.
- The three data fetchers (`fetch_reddit`, `fetch_trends`, `fetch_finnhub_news` in
  `src/data/`) are defined but **never called** by `scan.py` — no sentiment data is
  retrieved during a run.
- When wired up, `compute_sentiment_score` blends three cross-sectionally z-scored
  signals (averaged, ignoring NaNs):
  - **Mention velocity** — Reddit 7d/30d mention counts: `(7d/7) / (30d/30 + 1)`
  - **Search momentum** — OLS slope of a 13-week Google Trends interest series
  - **News sentiment** — mean VADER compound score over Finnhub headlines (**US only**;
    EU sectors always `NaN` on the free tier)
- Requires env vars: `FINNHUB_TOKEN` (news; returns `None` without it),
  `REDDIT_USER_AGENT` (mentions). Trends needs no key. All three cache to `data/cache/`.

**To activate later:** fetch the three sources in `scan.py`, pass the resulting
Series into `score_all(..., sentiment_score=...)`, and surface it on the dedicated tab.

---

## Phase 3 features

Carried over from earlier planning — not started:

- **Swedish overlay polish** — refine the Swedish-market overlay view
- **Multilingual sentiment polarity (FinBERT)** — replace/augment VADER with a
  finance-tuned, multilingual sentiment model
- **Constituent breadth** — true breadth from sector constituents (vs the current
  proxy)
- **Backtest against past rotations** — validate signals against historical sector
  rotations (e.g. energy 2021–22)
- **Streamlit live drill-down** (optional) — interactive drill-down UI

---

## Done

- ~~Data persistence & sync strategy~~ — migrated from a git-committed SQLite blob to
  Supabase (Postgres) so the DB stays in sync across local dev and CI. *(2026-06-22)*
