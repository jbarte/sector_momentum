# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

---

## Thematic / genre ETF momentum (beyond sectors)

**What:** Extend the momentum engine to a second universe of **thematic / genre
ETFs** — e.g. space, defence, clean energy, crypto, AI, robotics, uranium —
ranked the same way the GICS sectors are, but as their own track alongside the
sector leaderboard.

**Why:** The current scanner is GICS-sector-only. A lot of rotation happens at
the *theme* level (defence ripping, crypto-proxy ETFs, AI), which doesn't map
cleanly onto the 11 sectors. Applying the existing momentum pillars (RS, returns,
MA distance/slope, OBV) to a thematic ETF universe surfaces those rotations
without changing the sector model.

**Why it's a natural fit:** the momentum signals operate on any price series, not
anything sector-specific — only the *universe* and the *benchmark* differ. So
this reuses `src/signals/*`, `src/scoring.py`, `src/state.py`, and most of the
dashboard, much like sentiment was added as a parallel dimension rather than a
rewrite.

**Scope / things to resolve when designing:**
- **Universe definition** — a new config (e.g. `config/themes.yaml`) listing each
  theme → its ETF ticker(s). One ETF per theme to start (vs a basket).
- **Benchmark** — themes aren't region-cohorted like sectors. Pick a single broad
  benchmark for relative strength (e.g. `ACWI`/`SPY`), or score themes purely on
  absolute price momentum (no RS). Decide which signals carry over (RS needs a
  benchmark; returns/MA/OBV don't).
- **Scoring cohort** — z-score each theme within the theme universe (its own
  cohort), separate from the sector cohorts.
- **Keying / storage** — current DB + dashboard key on `region|gics_sector`.
  Themes need a parallel key (e.g. `THEME|<name>`); decide whether to reuse the
  existing tables with a new "region"/group value or add a dimension.
- **Constituent breadth** — likely N/A for themes (no GICS constituent list);
  the breadth signal would stay sector-only.
- **Sentiment** — themes map very naturally to Google Trends keywords (space,
  defence, crypto…), so the Trends sentiment dimension extends here too.

**Possible delivery:** a dedicated **Themes** tab/leaderboard mirroring the sector
leaderboard (rank, composite, trajectory, breakdown), fed by the same scoring
pipeline over the themes universe. Could ship incrementally: universe + scoring
first (info-only table), then full leaderboard parity (deltas/trajectory), then
sentiment.

**Notes:** Parallels the sentiment build — a new dimension layered on the existing
engine, not a rewrite. Biggest design decision is the benchmark/RS question above.

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

## Symbol-based Google Trends sentiment (tickers, not theme words)

**What:** Replace the current single generic theme word per sector (`semiconductor`,
`oil`, `bank` …) with the **actual instrument symbols** as the Google Trends queries,
aggregated to a sector-level attention signal:
- the sector **benchmark** ticker(s),
- the **related/linked sector ETFs** we already map (`config/universe.yaml` primaries +
  the alternates in `config/sector_etfs.yaml`),
- and, where feasible, the **underlying constituent stock symbols** (US S&P 500
  constituents are already fetched for breadth via `src/data/constituents.py`; EU has no
  constituent list, so EU stays ETF-only).

**Why:** The current keywords measure *consumer* search, not financial attention —
"oil"/"bank"/"food"/"retail" are dominated by cooking, banking-app, grocery, and shopping
queries. Finance-intent search (ticker symbols, fund/stock names) is the form Google
Trends has actually been shown to track investor attention (Da/Engelberg/Gao, "In Search
of Attention", 2011). Querying the symbols we already trade/benchmark makes the Trends
signal defensible instead of near-noise. Stays a confirmer (toggle-only, out of the
canonical composite) — see [[the existing Google-Trends-only item above]].

**Scope / things to resolve when designing:**
- **Payload normalization.** Trends scales 0–100 *within each ≤5-term request*, so values
  from different batches aren't directly comparable. Aggregating many symbols per sector
  needs a normalization scheme (shared anchor term across payloads, or pairwise scaling),
  not naive concatenation.
- **Low search volume.** Most individual tickers/stocks have near-zero global search and
  return flat/zero series — Trends only resolves high-attention names. Likely need a volume
  floor (drop dead terms) and a fallback; **ETF symbols may be the practical sweet spot**,
  with constituent symbols only for the few liquid mega-caps.
- **Ambiguous symbols.** Many tickers collide with common words (`ALL`, `KEY`, `IT`, `ON`,
  `A`). Prefer Trends **Topics** (entity mids) over raw strings, or filter these out.
- **Aggregation rule.** How to combine per-symbol interest into one sector number — mean of
  normalized momentum, market-cap-weighted, or max — and whether to weight ETFs vs
  constituents.
- **Rate limits.** Many more terms = many more batches = heavier pytrends 429 risk; needs a
  batching + caching strategy (today's cache is one full fetch/day).
- **Region/geo.** Still worldwide (`geo=""`) today; decide whether to pull per-region geos.

**Possible delivery:** extend `config/sentiment_keywords.yaml` (or a new symbol map) +
`fetch_trends` to take per-sector **symbol lists**, fetch + normalize + aggregate into the
existing `sentiment_score` input. No composite change — keep it behind the sentiment toggle.

**Notes:** This is the concrete "Option A" upgrade from the 2026-06-26 sentiment-quality
discussion. Honest caveat to keep visible: ticker search is a better *proxy* than theme
words, but individual-name search volume is often too thin to register — validate that the
symbol series aren't mostly zeros before trusting the signal. Reuses
`src/data/constituents.py` (US breadth pipeline) for the underlying-symbol list.

---

## Unify regional benchmarks for true cross-region scoring

**What:** Re-base US and EU scoring onto a common footing so sector scores are
comparable *in absolute terms across regions*, not just within each region.

**Why (the gap):** Today each region's `data_score` is z-scored within its own
11-sector cohort, so both cohorts are centered at zero by construction. That means
any cross-region combination (e.g. the composite view's US+EU mean) measures
"leads within both regions" — it cannot see that one whole region is broadly
stronger than the other. The simple-mean composite was chosen deliberately for the
sector-view toggle for this reason; this item is the heavier, "statistically
correct" alternative if absolute cross-region strength ever becomes something we
want to rank on.

**Two layers to the fix:**
- **Global z-score re-pool** — z-score the price-based signals (returns, MA
  distances, slopes) across all 22 region-sectors in one pool instead of per
  region. These signals are already absolute, so re-pooling makes them genuinely
  cross-region comparable.
- **Common benchmark** — RS-ratio and RS-momentum are measured against each
  region's own benchmark (US `RSP`, EU `EXSA.DE`), so they stay apples-to-oranges
  no matter how you re-pool. True comparability for the relative-strength signals
  needs both regions re-based to a single global benchmark (e.g. a world/ACWI ETF).
  This is the larger part of the change.

**Cost / notes:** Touches the core scoring pipeline (`src/scoring.py`,
`scan.py`) and would emit a new globally-scored series; it breaks the pure
client-side parity the sentiment + sector-view toggles rely on. Only worth it if
absolute cross-region ranking is a real need — the within-region semantics are a
defensible (arguably preferable) default for a rotation scanner. Captured from the
sector-view-toggle design discussion (2026-06-25).

---

## Phase 3 features

Carried over from earlier planning — not started:

- ~~**Swedish overlay polish**~~ — **dropped (2026-06-26):** the overlay is a
  hand-maintained list of 30 individual Swedish stocks (`config/swedish_tickers.csv`)
  with static market caps and no live data source — not tied to any real watchlist or
  broker. The project has moved to an ETF-native sector/theme model, so the
  single-market expression layer is a vestige of the original thesis. Not worth
  maintaining.
- **Multilingual sentiment polarity (FinBERT)** — replace/augment VADER with a
  finance-tuned, multilingual sentiment model
- **Backtest against past rotations — Phase 2 (rotation event-study)** — the early-flag
  half: per-rotation rank-over-time vs forward return for a curated list of historical
  rotations (e.g. energy 2021–22). Phase 1 (edge / strategy backtest) shipped 2026-06-26.
- **Streamlit live drill-down** (optional) — interactive drill-down UI

---

## Done

- ~~Language support: Swedish (UI chrome)~~ — client-side EN⇄SV toggle (English default,
  persisted in `localStorage`) translating the dashboard chrome: tab names, table headers,
  controls, tab-notes, disclaimer. `data-i18n`-tagged elements + a Swedish dictionary in
  the template; help prose, Plotly chart labels, and GICS sector names stay English.
  Template-only. *(2026-06-26)*
- ~~Backtest against past rotations (Phase 1 — edge)~~ — US/EU monthly top-5 rotation
  backtest vs RSP/EXSA.DE; `backtest.py` CLI + committed `backtests/` artifact + dashboard
  Backtest tab (equity curves + metrics). Point-in-time (no look-ahead), price-pillars-only,
  each region scored within its own cohort. Phase 2 (rotation event-study) still pending.
  *(2026-06-26)*
- ~~Sentiment methodology explanation~~ — collapsible "How is the sentiment score
  calculated?" guide in the Data ⇄ Sentiment tab (reuses the `tab-guide` pattern):
  states it's Google Trends search-attention only, the 13-week slope→z-score method,
  and that it doesn't affect the ranking unless the toggle is on. Template-only, no
  pipeline change. *(2026-06-26)*
- ~~Fetch history & per-scan export~~ — dashboard History tab now lists every scan
  (scan index with active-scan marker) with a per-scan report link; `write_report`
  refactored into `build_report_markdown`, per-scan reports generated to
  `docs/reports/report_<scan_id>.md`, and `get_scan_history(n_scans=None)` loads all
  scans. *(2026-06-25, PR #27)*
- ~~Data persistence & sync strategy~~ — migrated from a git-committed SQLite blob to
  Supabase (Postgres) so the DB stays in sync across local dev and CI. *(2026-06-22)*
- ~~Data inventory & coverage statistics~~ — `stats.py` CLI script: scan count + date
  range, cadence gaps, per-region/per-sector coverage, signal NULL rates, table row
  counts. *(2026-06-24)*
- ~~Constituent breadth (Phase 3.1)~~ — true breadth for US sectors: % of each
  sector's S&P 500 constituents (Wikipedia GICS list, fetched with a browser UA)
  above their own 50-DMA, info-only; EU shows "—"; retired the single-ETF proxy.
  *(2026-06-24)*
- ~~Sentiment toggle~~ — dashboard toggle + weight field blends Google Trends sentiment
  into the leaderboard ranking client-side (`rescore.js`); canonical composite stays
  pure-data (`score_all(..., blend_sentiment=False)`). Thin Trends wired into the scan;
  rich Trends tab still pending. *(2026-06-24)*
- ~~Sector view toggle~~ — leaderboard toggle between region-split (22 rows) and
  composite (11 GICS rows, simple mean of US+EU) views; client-side recompute in
  `rescore.js` (`mergeComposite`), composite rows + dual-region breakdown rendered in
  `build.py`, persisted in `localStorage`, default region-split. *(2026-06-25)*
- ~~Test suite could wipe production~~ — hardened the `test_state_smoke.py` wipe guard
  to be identity-based (resolves Supabase project ref, not raw URL string) so a
  prod-equivalent `TEST_DATABASE_URL` can't slip through, plus an `_assert_disposable`
  backstop that refuses to DELETE the live DB. *(2026-06-25)*
- ~~Back up the database on every scan~~ — `src/backup.py` writes a full CSV dump
  (`scans`/`scores`/`signals` + `manifest.json`) to repo-committed `backups/` after each
  scan (non-fatal, `--no-backup`); `restore.py` loads it back (refuses non-empty DB
  unless `--force`); CI commits `backups/`. Git history = the rolling backup set.
  *(2026-06-25)*
- ~~Claude Code `/scan` command~~ — `.claude/commands/scan.md`: runs `scan.py` then
  rebuilds the dashboard, with a concise completion summary. Local-only (`.claude/` is
  gitignored). *(2026-06-25)*
