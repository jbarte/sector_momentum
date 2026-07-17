# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

**How this file stays in sync (read before editing):**

- **One item = one `##` section** under Queued (or one bullet in a grouped
  sweep). Keep sections self-contained — union merge combines *additions*
  cleanly but silently doubles concurrent *edits* to the same paragraph.
- **Shipping an item: delete its Queued section entirely** and add one entry
  at the **top of Done** — in the same branch/PR as the code. Never
  strikethrough-in-place in Queued; half-struck sections are exactly how this
  file drifted before.
- **Partially shipping:** rewrite the Queued section so it describes *only
  what remains*, and record the shipped part in Done.
- **Done is append-at-top and never edited** — it's the permanent record.
- Run `/backlog-sync` to audit Queued against git history and the code when
  drift is suspected (e.g. after merge conflicts touching this file).

---

# Queued

## Multilingual news sentiment (FinBERT) — recommended sentiment direction

**What:** Signed sentiment (positive/negative polarity, not just attention)
from a finance-tuned model over a free news feed — **GDELT** (global tone) or
**Alpha Vantage** `NEWS_SENTIMENT` (free tier). Local inference
(`transformers` + `torch`, ~400 MB model, CPU, no API key) fits the free-only
constraint but is a heavier dependency than anything in the current stack.

**Why:** The 2026-06-26 Trends validation showed search-attention is noisy,
directionless, and ambiguous-ticker-contaminated. FinBERT sidesteps search-term
ambiguity entirely and adds direction. Base FinBERT is English-only; EU/Swedish
needs a multilingual variant or translate-then-score.

## Forward-return validation (do the rankings predict anything?)

**What:** For each historical scan, compute the forward 1-month return of that
day's top-5 sectors vs the region benchmark, and surface a rolling hit-rate /
average-edge panel (likely a new tab or a section in Backtest).

**Why:** The backtest is a one-off artifact over a fixed history; this
continuously validates *live* scans as they age. All inputs already exist:
per-scan ranks in the DB (`get_scan_history`), prices in the cache. Compute at
dashboard-build time; scans younger than the horizon show "pending".

**Design notes:** pick the horizon (1M to match the rotation backtest), handle
the price-cache `start` truncation issue (see Maintenance sweep) if longer
history is needed, and keep it info-only.

## Holding-period stats (how long to hold a position)

**What:** Historical stats on how long a sector typically stays "worth
holding" once it enters the top ranks — e.g. median/typical scan-count (or
calendar days) a sector spends in the top 5 before falling out, distribution
of holding periods, and how that compares to the Entry/Exit badge timing.

**Why:** Rank and trajectory tell you *that* a sector is a leader; this tells
you *how long that tends to last*, which is the practical question someone
actually holding a position asks. Same data family as forward-return
validation and the Entry/Exit scorecard above — all three answer variations
of "what happens after a signal fires," and likely share plumbing (per-sector
top-N run-length from `get_scan_history`, no new data source). Worth designing
together with those two rather than separately, to avoid three overlapping
historical-stats panels.

**Design notes:** define "holding a position" precisely (in top-5? top-3?
above a composite threshold?) before implementing — that choice drives the
whole stat. Info-only, no scoring impact.

---

# Parked

## Symbol-based Trends sentiment — Phase 2 (US constituents)

**Parked 2026-06-26 after Phase 1 validation.** Adding constituent tickers
(more, lower-volume, more-ambiguous terms) makes ticker-collision
contamination worse, not better. Key findings kept for the record:

- Mechanism works for liquid US ETFs (full 13/13 coverage on `XLK/VGT` etc.);
  EU `.DE` tickers are dead on Trends (0/13).
- Ambiguous tickers dominate the cross-sectional z (`VOX` → Vox Media z +4.16,
  `LOGS` → the English word z +1.27). Blocklisting is whack-a-mole; the real
  fixes are entity mids (since shipped for sectors, 2026-07-04) or the FinBERT
  pivot (queued above).
- If ever revived: needs top-N liquidity ranking (no market-cap source in
  `fetch_sp500_constituents()`), aggregation weighting, and the Trends
  day-cache (since shipped, 2026-07-07).

Phase 1 design + plan: `design/{specs,plans}/2026-06-26-symbol-trends-*`.

## Streamlit live drill-down

Optional interactive drill-down UI. Carried from early planning; the static
dashboard's drill-down tab covers most of the need.

---

# Done

- **Threshold alerts (daily scan notifications)** — post-scan step (Step 15 in
  `scan.py`) compares the two latest scans and sends a ntfy.sh push notification
  when a sector or theme enters or exits the top 3 ranks. Covers both US/EU
  sectors and themes. "No events, no noise" — nothing sent if the top 3 is
  unchanged. `src/alerts.py` handles event detection, formatting, and delivery
  (stdlib `urllib`, no new dependency). Fail-open: missing `NTFY_TOPIC` env var
  silently skips; `--no-alerts` CLI flag to suppress. CI wired via
  `scan.yml` secret. *(2026-07-17)*

- **Macro regime context bar** — a thin info strip below the dashboard header showing
  SPY vs 200-DMA (above/below + distance %) and VIX band (Calm/Elevated/Stressed).
  Fetched at dashboard build time via the existing price cache, non-fatal if unavailable.
  `dashboard/macro.py` computes the indicators; `_macro_bar.html.j2` renders the strip
  on all three pages. Info-only, no scoring impact. *(2026-07-15)*

- **Entry/Exit badge scorecard** — historical hit-rate table for all 7 badge
  types (Entry, Exit, 5 trajectory states) plus a no-badge baseline. For each
  badge that appeared on a past scan, computes the 5-trading-day forward ETF
  return and aggregates count, hit rate, mean, and median. Displayed in the
  Backtest tab below the equity curves. Computed at `build.py` time from
  `get_scan_history(n_scans=None)` + cached prices; no new DB tables.
  `dashboard/badges.py` holds the logic. EN+SV i18n. Info-only — no scoring
  impact. *(2026-07-16)*
- **Dashboard UX redesign** — compact command bar (scan info + page nav +
  disclaimer + guide + lang toggle), card-shell surfaces on all three pages
  (sectors, themes, sentiment), utility-row pattern for tab actions, footer
  with version/feed/GitHub links. Deleted macro bar (absorbed into command
  bar), guide modal, and several legacy layout patterns. Full CSS variable
  foundation. *(2026-07-16)*
- **Macro regime context bar** — risk-on/risk-off context bar: SPY vs 200-DMA
  (above/below + distance) and VIX band (calm/elevated/stressed) from cached
  daily prices. Displayed in the command bar's macro context section.
  `dashboard/macro.py` builds the context; info-only, no scoring impact.
  *(2026-07-15)*
- **RSS/Atom feed of scan results** — `build.py` now emits `docs/feed.xml`, an Atom
  feed with one entry per scan (last 30). Each entry lists the top-5 sectors per region
  and biggest rank movers. All three HTML pages link to the feed via
  `<link rel="alternate">`. New module `dashboard/feed.py` builds entries from
  `all_scores_df`; template `dashboard/templates/feed.xml.j2` renders the Atom XML.
  No schema changes, no JS. *(2026-07-15)*

- **Theme backtest tab** — the Themes page now has a Backtest tab matching the
  sector page. `score_themes_as_of` (`src/backtest/replay.py`) replays the theme
  scoring pipeline as-of any historical date; `run_theme_track`
  (`src/backtest/engine.py`) runs a monthly top-N rotation strategy against ACWI
  (default top-3, configurable via `--theme-top-n`). `backtest.py` now fetches
  theme tickers alongside sectors and writes theme results to
  `backtests_themes/summary.json` (separate from the sector backtest).
  `_build_theme_backtest_context` (`dashboard/figures.py`) loads the results and
  builds a Plotly equity curve; `themes.html.j2` renders the Backtest tab with
  metrics table + chart, EN+SV i18n, and Guide section. No rotations event-study
  for themes (no curated historical events yet). *(2026-07-15)*
- **Theme sentiment (Google Trends for themes)** — the thematic ETF cohort now
  gets a Trends sentiment dimension, mirroring the sector path. A `trends:`
  section in `config/themes.yaml` maps each theme to a real search phrase
  (uranium, defense stocks, robotics…) with an optional `trends_entities:`
  mid override; `build_theme_symbol_map`/`load_theme_entities`
  (`src/data/trends_symbols.py`) key them as `THEME|<name>` and reuse
  `fetch_symbol_trends`/`score_symbol_sentiment`/`derived_signals`/
  `fetch_comparative_interest`/`fetch_rising_queries` verbatim (pulled
  worldwide — `THEME: [""]` in `config/trends_geo.yaml`). `scan.py`'s themes
  block fetches sentiment (isolated non-fatal try so price scores still persist),
  passes it to `score_all` to fill the existing `theme_scores.sentiment_score`
  (stored, never blended), and saves derived/attention/rising rows to a new
  `theme_sentiment_signals` table via `save_theme_scan(..., sentiment_signals_df=)`.
  Surfaced on the shared `docs/sentiment.html` behind a client-side
  **Sectors ⇄ Themes** toggle (localStorage-persisted, lazy-plotted scatters,
  EN+SV). `_rows_from_df` now normalizes NaN→NULL for text columns; scatter
  builder gained a THEME series colour. Full parity with the sector sentiment
  view. *(2026-07-15)*
- **Sentiment honesty fixes** — coverage guard (`_aggregate` omits dead
  sector keys, `score_symbol_sentiment` z-scores live subset only with
  `_MIN_LIVE_SECTORS=8` threshold, NaN for dead/under-threshold); pinned
  `_MOMENTUM_WINDOW=13` shared constant so headline sentiment == z(momentum)
  as documented; honest health log (counts live series before z-scoring, not
  post-z non-zeros); bumped default fetch `sleep_s` 20→25; removed dead
  `pillars` block from `config/weights.yaml` (closes the "Sentiment →
  composite blend decision" item — toggle-only is the permanent design until
  FinBERT); `score_all` reads pillars lazily via `.get()` defaults.
  Spec: `design/specs/2026-07-13-sentiment-honesty-design.md`. *(2026-07-13)*
- ~~Maintenance sweep~~ — deleted dead `src/data/stocktwits.py` +
  `tests/test_stocktwits.py` (superseded by symbol-based Trends sentiment);
  `_cache_is_fresh` (`src/data/prices.py`) now tolerates a 4-day gap so the
  day after a market holiday no longer triggers a spurious live re-fetch, and
  also checks that cached data covers a newly-requested longer `start` range
  (re-fetches instead of silently truncating); deduped three repeated
  patterns in `src/state.py` — a shared latest-scan query helper, a shared
  recent-scan-filter builder, and a shared DataFrame-to-rows insert helper —
  all pure refactors, same output/behavior, verified by the existing test
  suite. *(2026-07-12)*
- ~~Theme timestamp parse crash~~ — `_build_drilldown_data`/`_build_history_figure`
  (`dashboard/figures.py`) crashed on `run_at` values that mixed ISO8601
  timestamps with and without a `+00:00` timezone suffix (`pd.to_datetime`
  infers a fixed format from early rows, then chokes on later rows). Fixed by
  passing `format="ISO8601", utc=True`. Was blocking local
  `python3 dashboard/build.py` runs on the theme drilldown path. *(2026-07-12)*
- ~~"What changed today" digest~~ — a summary strip above the sector
  leaderboard shows new top-5 entries and the biggest rank movers (up to 3
  gains, 3 drops) vs the previous scan. Entirely client-side
  (`dashboard/assets/scan-digest.js`), reusing the `SCAN_HISTORY` blob already
  shipped by renderable-scan-history — no pipeline or schema changes. Updates
  live when browsing historical scans via the existing scan-history viewer.
  Trajectory flips deferred (would need porting the server-side trailing-slope
  algorithm to JS). *(2026-07-12)*
- ~~Backlog rewrite + drift guardrails~~ — rewrote this file (deleted the
  fully-shipped code-review-findings section and stale queued text for shipped
  work), added the lifecycle rules above, created a real
  `.claude/commands/backlog-sync.md` (CLAUDE.md referenced a command that
  didn't exist), and un-ignored `.claude/commands/` so shared commands are
  versioned. Dropped record: **Swedish overlay polish** was dropped 2026-06-26
  (hand-maintained 30-stock list with static caps, vestige of the original
  thesis). *(2026-07-12)*
- ~~Renderable scan history~~ — clicking any scan row in the History tab rebuilds
  the Leaderboard with that scan's scores via an embedded `SCAN_HISTORY` JSON blob
  and client-side JS table rebuild. Sectors page only; charts stay multi-scan as-is.
  Keyboard accessible (tabindex + Enter/Space), i18n (EN+SV), "Back to latest"
  restore. *(2026-07-12)*
- ~~Sentiment enrichment — seasonal baseline + rising queries~~ — extended Trends fetch
  from 3 months to 12 months (`today 12-m`, 52 weeks); existing derived signals still
  operate on the trailing 13 weeks. New `seasonal_ratio` signal = mean(last 13 wk) /
  mean(first 39 wk), surfaced as a "Seasonal" column (EN+SV) on the sentiment page.
  New `fetch_rising_queries()` calls `related_queries()` per sector per geo (cached,
  fail-open), top 5 results stored in a `text_value TEXT` column on `sentiment_signals`,
  displayed as expandable panels with delegated click/keyboard toggle. Both info-only —
  neither affects the composite score. *(2026-07-12)*
- ~~P4 split build.py~~ — split `dashboard/build.py` (1,459 lines) into
  `figures.py`, `rows.py`, `breakdown.py`, `sentiment.py`, `reports.py`
  (~300 lines remain in build.py as orchestrator + re-exports). Extracted
  `_base_layout()` helper eliminating ~80 lines of duplicated Plotly layout
  boilerplate per figure. Deleted unused `_CHART_STYLE` constant. Deduped
  sector/theme leaderboard row builders via shared `_build_rows_common()`.
  Extracted `_header.html.j2` (shared across all 3 pages) and `_tabs.js.j2`
  (shared between index and themes). Created `dashboard/__init__.py`. All
  existing imports preserved via re-exports in build.py. *(2026-07-12)*
- ~~P4 dead config + minor sweep~~ — clarified `config/weights.yaml` (comments
  documenting signal-list keys are dashboard display order only, scoring hardcodes
  the lists; removed unused `emerging_min_consecutive`; noted `blend_sentiment=False`
  means pillar weights are not applied). Replaced `datetime.utcnow()` with
  `datetime.now(timezone.utc)` in scan.py, backtest.py, src/backup.py, and tests.
  Removed dead imports in scan.py (math, numpy). Moved mid-file imports to top in
  trends_symbols.py and state.py. Filtered `backup_*.zip` in restore latest
  selection. GitHub Actions already pinned (first-party at major version tags,
  third-party SHA-pinned); test.yml already had `fix/**` trigger. *(2026-07-11)*
- ~~Review P4: test coverage gaps~~ — added 22 unit tests for `src/data/prices.py`
  (cache freshness, stooq→yfinance fallback, all-NaN/empty/corrupted edge cases,
  `_normalize_columns` with MultiIndex, `fetch_prices` cache-vs-live integration),
  8 tests for `src/data/macro.py` (stub contract), 13 pipeline value-range assertions
  + missing-benchmark/sector handling, and 7 render-based dashboard tests (full
  leaderboard render with breakdown panels, figure builder JSON validation,
  trajectory computation, `_safe_float`/`_format_raw_value` edge cases, multi-call
  render-context coverage). `_render_context_keys` now finds all three `_render()`
  calls (index/sentiment/themes) instead of just the first. *(2026-07-11)*
- ~~README + ARCHITECTURE docs~~ — rewrote `README.md` (purpose, disclaimer, live dashboard link, env keys, dev commands, pointers) and fully synced `ARCHITECTURE.md` to current reality (Supabase/Postgres, daily cron, Google Trends sentiment, actual module structure and data flow). *(2026-07-11)*
- ~~rs_momentum fast=1→5~~ — `compute_rrg` default changed from `fast=1` (one-day noise) to `fast=5`; configurable via `config/weights.yaml` `signal_params.rs_momentum_fast`; threaded through `latest_rrg` → `compute_signals_for_sector` → `build_signals_rows` / `build_theme_signals_rows` → `scan.py`. Expect rank shifts from the smoother momentum signal. *(2026-07-11)*
- ~~Backtest realism~~ — four fixes: (1) `--cost-bps` CLI flag debits one-way transaction costs proportional to turnover on each rebalance; (2) benchmark NaN months dropped instead of silently treated as 0%; (3) `close_at` rejects prices older than 5 trading days (returns NaN); (4) Sharpe column labelled "Sharpe (rf=0)" in EN+SV. *(2026-07-11)*
- ~~Dependency lockfile & pytrends pin~~ — split `requirements.txt` (runtime, `>=` floors) from `requirements-dev.txt` (adds pytest); `uv pip compile` generates exact-pinned `.lock` files that CI installs from (`requirements.lock` for build-docs/scan, `requirements-dev.lock` for tests); `pytrends` pinned to `==4.9.2` in the input file. Daily cron no longer installs newest versions on every run. *(2026-07-11)*
- ~~Review P1: z-score NaN handling~~ — `zscore_cross_section` now standardizes on
  non-NaN values and fills missing z-scores with 0.0 (neutral in z-space) instead
  of filling raw values with 0.0 first, which made any sector with a failed
  ~100-centred signal (rs_ratio/rs_momentum) a fake outlier that distorted the
  whole cross-section. Regression test added. *(2026-07-11)*
- ~~Review P1: backup/restore table coverage~~ — backups now include
  `sentiment_signals`, `theme_scores`, `theme_signals` (previously silently
  dropped, and `restore.py --force` failed on an FK violation deleting `scans`
  with live child rows). Deletes/loads now run in FK-safe order; old backups
  lacking the new tables restore gracefully (empty DFs). Schema-coverage test
  asserts `_COLUMNS` covers every table in the DDL. *(2026-07-11)*
- ~~Review P2: CI hardening~~ — `scan.yml` and `build-docs.yml` now share a
  `commit-to-main` concurrency group and rebase before pushing (fixes the
  lost-commit race); daily scan gated on a green `pytest`; `test.yml` also
  triggers on `fix/**`/`chore/**`; `claude-code-action` pinned to SHA. *(2026-07-11)*
- ~~Review P2: dependency fragility~~ — covered by the lockfile + pytrends pin
  entry above; pytrends already degrades gracefully, maintained replacement
  (trendspy) deferred to if/when pytrends breaks. *(2026-07-11)*
- ~~Scan robustness: coverage guard, idempotent saves, connection cleanup~~ — scan.py aborts (exit 1) if <80% of configured sectors produce signal rows; `save_scan` replaces same-UTC-day scans so CI retries don't duplicate; DB connection wrapped in try/finally; report + dashboard steps non-fatal. *(2026-07-11)*
- ~~Dashboard quick wins: movers clip, rank guard, rescore init, dead code, report skip, plotly-basic~~ — removed fixed 520px height from movers containers (both templates); added `row.rank is number` guard in index.html.j2; `applyRanking()` only runs on init when sentiment toggle is enabled; deleted dead per-signal drilldown figure loop (751-796); `_generate_scan_reports` skips reports whose file already exists; switched to plotly-basic bundle (~3.6MB → ~1MB). *(2026-07-11)*
- ~~i18n gaps + CSS vars~~ — added SV `guide_tab_themes` (full themes Guide page), `guide_body_rrg_themes`, `guide_body_drilldown_themes`, `si_download`, `leaderboard_empty`, `scans_empty`; generalized "topp-5" in `note_backtest`; fixed `--font-sans` → `--font-body`, `--brand` → `--brand-strong`, `--text-muted` → `--fg4`. *(2026-07-12)*
- ~~Accessibility~~ — tabs: `aria-selected`/`aria-controls`, `role="tabpanel"`, arrow-key nav; leaderboard rows: `tabindex="0"` + Enter/Space delegated handler; sortable `<th>`: `tabindex="0"` + keyboard trigger; `.sig-tip`: focusable + tooltip on focus; guide modal: `aria-modal="true"`, focus trap, focus restore on close. Both index.html.j2 and themes.html.j2. *(2026-07-12)*
- ~~XSS hardening~~ — `js_json` Jinja filter escapes `</` in script-block JSON; inline `onclick="toggleBreakdown()"` replaced with `data-sector-id` + delegated click listener (both pages); ETF URL scheme validation rejects non-http(s) URLs. *(2026-07-12)*
- ~~Themes — full tab parity with sectors~~ — the Themes page now has the same
  tab structure as Sectors: Leaderboard, RRG, Drill-down, Movers, History, and
  Guide. Added `get_theme_rrg_history()` in `state.py`; all other build functions
  (`_build_rrg_figure`, `_build_drilldown_data`, `_build_movers_figure`,
  `_build_history_figure`) reused as-is with `theme_history_df`. Backtest tab
  deferred (requires a separate theme backtest runner). *(2026-07-10)*
- ~~Remove region-split / composite view toggle~~ — removed the Region-split vs
  Composite `<select>` toggle, `data-view` row attributes, `mergeComposite` in
  `rescore.js`, `_build_composite_rows`/`_build_composite_history` in
  `build.py`, composite CSS rules, 3 i18n keys, and `test_dashboard_composite.py`.
  US and EU sectors stay separate — no merged "Global" rows. *(2026-07-10)*
- ~~Themes — EU-available ETF alternatives~~ — added a `ucits:` section to
  `config/themes.yaml` with one UCITS-listed equivalent per theme (10 total:
  Global X, VanEck, iShares, First Trust). Each entry has ticker, name, ISIN,
  TER, issuer, match quality (exact/close/partial), and justETF URL.
  `_build_instruments_html` now renders a "UCITS Alternative" table in theme
  breakdown panels with a colour-coded Match column. Scoring stays on US ETFs;
  UCITS shown as reference instruments only. *(2026-07-10)*
- ~~Comparative (cross-sector) interest~~ — `fetch_comparative_interest`
  (`src/data/trends_symbols.py`) pulls each region's sectors through
  anchor-chained Trends batches (`_rescale_chain`) so interest is scored
  head-to-head against all other sectors in the same region, not
  independently-scaled per-sector series. Wired into `scan.py`, persisted as
  `attention_level` rows in `sentiment_signals`, and surfaced as an "Attention"
  column (EN+SV) on `docs/sentiment.html`. Toggle-only/info-only — no composite
  or ranking impact, sectors page unchanged. *(2026-07-09)*
- ~~Thematic ETF momentum — Phase 2 (leaderboard deltas + trajectory)~~ — the Themes
  leaderboard now shows rank-Δ (vs the previous scan) and a trajectory badge (rank
  slope over the last 5 scans), matching the sector board. Computed at dashboard-build
  time from a new `get_theme_scan_history` loader (aliased region="THEME" so
  `_compute_rank_trajectories` and the delta-merge reuse verbatim) — no schema or
  `scan.py` change. Phase 3 (RRG scatter, composite-history chart, Trends sentiment
  for themes) remains queued above. *(2026-07-08)*
- ~~Thematic ETF momentum — Phase 1 (universe + score + leaderboard)~~ — a thematic
  ETF universe (`config/themes.yaml`, one ETF per theme) is scored by the existing
  momentum pillars vs a single global benchmark (ACWI, SPY fallback) in its own
  z-score cohort (`build_theme_signals_rows` + `score_all`), persisted to new
  `theme_scores`/`theme_signals` tables under the daily `scan_id`, and shown as a
  read-only **Themes** leaderboard (third header segment, reusing the breakdown panel).
  Breadth is N/A for themes; the themes pass is fully non-fatal. Phases 2 (deltas /
  trajectory / RRG / history) and 3 (Trends sentiment for themes) remain queued above.
  *(2026-07-07)*
- ~~Sentiment — durable Trends day-cache~~ — successfully-fetched Trends batches are
  cached per UTC day in Supabase Storage (bucket `trends-cache`, one
  `trends_cache_<date>.json` object) so a re-triggered CI run or same-day re-run
  reuses them and re-fetches only the batches that 429'd/failed. Cache is per
  `(geo, batch)` (`src/data/trends_cache.py`), read/written in `_fetch_geo`, and
  loaded/saved around the fetch in `scan.py`. Fully **fail-open** — any Storage error
  logs a warning and the scan runs live/uncached; `--no-cache` bypasses it. Cuts the
  429 exposure from the ~4× region-aware call volume. *(2026-07-07)*
- ~~Sentiment — region-aware Trends pulls~~ — `fetch_symbol_trends` now queries US
  sectors in `geo="US"` and EU sectors averaged across `DE`/`FR`/`GB`, normalized
  against a stable ubiquitous anchor (`YouTube`, configurable in
  `config/trends_geo.yaml`) instead of the worldwide `SPY` pull. Symbols are
  partitioned by region (`_symbols_by_region`), fetched per geo (`_fetch_geo`), and
  multi-geo regions averaged per symbol (`_average_geo_series`); `_aggregate`/scoring
  and the entity-mid path are unchanged (ticker-keyed). Toggle-only. Costs ~4× the
  Trends API calls (day-cache remains a separate backlog item), and the anchor change
  breaks comparability with pre-change stored sentiment. *(2026-07-07)*
- ~~Sentiment — Trends entity-mid resolution~~ — `fetch_symbol_trends` now queries a
  ticker's Google Knowledge Graph **entity mid** instead of the ambiguous raw string
  where one is curated in `config/trends_entities.yaml`, killing collision false-positives
  (the `VOX`→Vox Media / `LOGS`→the-word class). Per-ticker term substitution +
  column re-keying keep `_aggregate`/scoring unchanged (ticker-keyed); tickers without a
  curated mid fall back to strings, so the change is strictly additive. A dev-only
  `scripts/resolve_trends_entities.py` proposes candidates for human review; the scan
  path never calls `suggestions()`. Toggle-only. The committed config ships empty —
  real mids are added after running the script and eyeballing each entity. *(2026-07-04)*
- ~~Sentiment enrichment — derived Trends signals~~ — the sentiment page now surfaces
  four complementary read-outs alongside the headline slope, all computed from the same
  ~13-week interest series in `derived_signals()` (`src/data/trends_symbols.py`):
  **acceleration** (recent-half vs earlier-half slope), **range position** (percentile in
  the window min–max), **spike** (z of the latest point vs trailing weeks), and
  **volatility** (std of week-over-week changes). Stored per sector-key in a new additive
  `sentiment_signals` table (no schema migration; old scans simply lack rows), rendered as
  an info-only table on `docs/sentiment.html` (EN+SV). Still **toggle-only** — only
  `momentum`/slope feeds the composite via the existing toggle; the new signals never touch
  the ranking. Region-aware pulls, Trends topics/entity-mids, seasonal baseline, and rising
  queries remain queued above. *(2026-07-02)*
- ~~Sentiment moved to its own page~~ — sentiment is no longer a dashboard tab; it now
  lives on `docs/sentiment.html`, linked from the main nav ("Sentiment ↗"), decoupled
  from the Leaderboard/RRG/History/etc. tab bar. The leaderboard's "include sentiment in
  ranking" toggle and Sentiment column are unchanged — this only moves the read-only
  scatter/explainer view. Shared CSS and the EN⇄SV language toggle were extracted into
  `dashboard/templates/_style.html.j2` / `_i18n.html.j2` so both pages stay in sync.
  Enrichment ideas (region-aware Trends, more derived signals) remain queued above.
  *(2026-07-02)*
- ~~EU sector composites (Phase 1: Financials, Materials)~~ — EU Financials (Banks +
  Financial Services + Insurance) and Materials (Basic Resources + Chemicals) are now
  equal-weight composites of their STOXX supersector ETFs instead of a single sub-sector,
  making them truer GICS proxies. `eu_sectors` values are lists; `build_composite_series`
  blends a rebased-mean Close + summed Volume; single-component sectors and all US sectors
  unchanged. Phase 2 (Consumer Discretionary/Staples/Comm + Media/P&HG crosswalk) pending. *(2026-06-29)*

- ~~EU-available instruments reference~~ — the per-sector "Instruments" panel now lists one
  EU-available UCITS ETF per sector (US → iShares S&P 500 sector UCITS ETFs, Real Estate →
  iShares US Property Yield). For EU, the reference is the **same instrument the scanner
  uses** (`universe.yaml eu_sectors`) for all 11 sectors — reference == signal source; the
  three previously-Amundi sectors (Energy/Industrials/Consumer Discretionary) were realigned
  to the scanned iShares STOXX 600 funds (`EXH1/EXH4/EXH7.DE`). US can't match (scanned
  `XLV`/`XLK` aren't EU-available). Reference-only (`config/sector_etfs.yaml`); scanned
  instruments/benchmarks unchanged. *(2026-06-29)*
- ~~Stop publishing internal design docs~~ — moved `docs/superpowers/` (specs + plans) to
  repo-root `design/` so they're versioned but no longer served on the public Pages site
  (`docs/` is the published web root; static `.nojekyll` serving has no per-folder exclude).
  CLAUDE.md now points spec/plan output at `design/{specs,plans}`. *(2026-06-29)*
- ~~Published History tab stale (GitHub Pages frozen)~~ — the daily scan committed
  current `docs/`, but Pages' legacy **Jekyll** build hard-failed on Liquid brace syntax
  in `docs/superpowers/` plan snippets, freezing the published site at the last good
  deploy (scan 113 / 06-26) while `docs/` and the DB kept advancing to scan 116. Fix:
  `build.py` now emits `docs/.nojekyll` so Pages serves the static artifact as-is. *(2026-06-29)*
- ~~DB backup → Supabase Storage (pre-run)~~ — replaced the git-committed `backups/` CSV dump with a pre-run zip uploaded to a private `db-backups` Supabase Storage bucket (`src/storage_backup.py` + `backup_to_storage`/`restore_from_storage`); `scan.py` backs up before writing; `scan.yml` no longer commits `backups/`; `restore.py` pulls latest from Storage (`--list`/`--local`). One new secret `SUPABASE_SERVICE_KEY`. *(2026-06-29)*
- ~~Backtest against past rotations (Phase 2 — rotation event-study)~~ — curated rotations in `config/rotations.yaml` → `src/backtest/rotations.py` recovers each sector's point-in-time rank-over-time vs the ETF's indexed price (reusing `score_as_of`); persisted in `backtests/summary.json` and rendered as dual-axis small-multiples in the Backtest tab. Visual-only. *(2026-06-27)*
- ~~Symbol-based Google Trends sentiment (Phase 1 — ETF symbols)~~ — Trends now queries the
  sector ETF symbols (primary + linked, both regions) instead of generic theme words;
  anchor-normalized (SPY) and aggregated to a region-aware sentiment z per region|sector via
  `src/data/trends_symbols.py`. Toggle-only (composite unchanged). Phase 2 (US constituents)
  pending; live coverage of EU `.DE` tickers to be validated. *(2026-06-26)*
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
