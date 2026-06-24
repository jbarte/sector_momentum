# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

---

## Sentiment methodology explanation

Surface a plain-English explanation of how the sentiment score is calculated and which data sources feed it, accessible from the dashboard (e.g. an info tooltip or expandable panel near the Data ↔ Sentiment tab or the leaderboard sentiment column header).

**Why:** Users need to understand what they're looking at — is it Google Trends? News sentiment? — before trusting it enough to act on. Currently no in-dashboard explanation exists.

**Notes:** Pure static HTML — no pipeline changes needed. Write explanation based on `src/signals/sentiment.py` and `src/data/trends.py`, render as a collapsible `<details>` block or an `ℹ` tooltip in the template.

---

## Sentiment module — Google Trends only, as a dedicated tab

**What:** Build a search-interest ("attention") feature powered **solely by Google
Trends**, presented as its own dashboard tab and kept out of the core momentum score.

**Why Google Trends only:** We tried the other free sentiment sources and they don't
work for us:
- **Reddit** — free scraping / public JSON is unreliable and rate-limited; no usable
  free access.
- **Finnhub (news)** — free tier is too limited (US-only ETF news, tight quotas).
- **Google Trends** — the **only** free source that reliably returns useful data, and
  it needs no API key.

So we drop Reddit + Finnhub entirely and focus all effort on getting the most out of
Trends.

**Status:** Not started (tab/UI). A Trends-based scoring engine partly exists but is
dormant — see below.

**Current state of the code:**
- `compute_sentiment_score` (`src/signals/sentiment.py`) is built and unit-tested but
  **not wired into the live scan** — `scan.py` calls `score_all(wide_df, ...)` without
  a `sentiment_score`, so composite = pure data pillar and `sentiment_score` is stored
  as `NaN`. (`config/weights.yaml` declares `sentiment: 0.30`, never applied.)
- The Reddit + Finnhub fetchers can be removed; only `fetch_trends`
  (`src/data/trends.py`) is relevant going forward.
- `fetch_trends` today is thin: pulls only the **primary keyword** per sector
  (`config/sentiment_keywords.yaml`), `today 3-m` (~13 weeks), worldwide (`geo=""`),
  and the engine reduces each series to a single OLS slope.

**Getting the most out of Google Trends (ideas to explore):**
- **Use all keywords, not just the first.** Each sector lists several terms; combine
  them (mean or max normalized interest) instead of discarding all but `keywords[s][0]`.
  Prefer Trends *topics* (entity mids) over raw strings for ambiguous terms (e.g. "AI",
  "auto", "cloud").
- **Region-aware pulls.** Fetch `geo="US"` for `US|` sectors and per-country geos for
  `EU|` sectors (DE/FR/GB…). This gives genuine region-specific attention and finally
  fills the EU gap that Finnhub couldn't.
- **Comparative (cross-sector) interest.** Trends normalizes 0–100 *within a payload*,
  so putting sectors in the same `build_payload` yields a true head-to-head attention
  ranking — more meaningful than independently-scaled series.
- **Multiple derived signals from one series**, not just slope:
  - *Momentum* — OLS slope (current)
  - *Acceleration* — recent slope vs earlier slope (2nd derivative)
  - *Level / range position* — latest value vs its own 13-week min–max (percentile)
  - *Attention spike* — z-score of the latest point vs trailing mean (breakout in
    interest)
  - *Volatility* — stability of interest over the window
- **Longer window for a seasonal baseline.** Pull 12 months to compute current interest
  vs its seasonal norm (YoY), reducing false momentum from recurring seasonality.
- **Rising / breakout queries.** `pytrends.related_queries()` surfaces "rising" search
  terms per topic — could flag emerging themes within a sector for the tab.

**To activate:** enrich `fetch_trends` along the above lines, compute the derived
signals in a Trends-only scorer, surface them on the dedicated tab, and (optionally)
feed a single blended Trends score back into `score_all(..., sentiment_score=...)` if
we later decide it should influence the composite.

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
- ~~Data inventory & coverage statistics~~ — `stats.py` CLI script: scan count + date
  range, cadence gaps, per-region/per-sector coverage, signal NULL rates, table row
  counts. *(2026-06-24)*
- ~~Sentiment toggle~~ — dashboard toggle + weight field blends Google Trends sentiment
  into the leaderboard ranking client-side (`rescore.js`); canonical composite stays
  pure-data (`score_all(..., blend_sentiment=False)`). Thin Trends wired into the scan;
  rich Trends tab still pending. *(2026-06-24)*
