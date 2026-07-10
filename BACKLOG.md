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

## Sentiment page — enrichment (get more out of Google Trends)

**What:** Improve the Google-Trends-only sentiment signal shown on its dedicated
page (`docs/sentiment.html`, relocated from a dashboard tab — see Done). Still kept
out of the core momentum score.

**Current state of the code:**
- The live scan computes `sentiment_score` from **symbol-based Google Trends**
  (`src/data/trends_symbols.py` → `score_symbol_sentiment`, shipped Phase 1), passed to
  `score_all(..., blend_sentiment=False)` — toggle-only, never blended into the canonical
  composite. (`config/weights.yaml` declares `sentiment: 0.30`, never applied.)
- The old multi-source engine has been fully removed: **Finnhub** (US-only free tier),
  **StockTwits** (Cloudflare-blocked), **Reddit** (`src/data/reddit.py`), the orphaned
  `compute_sentiment_score` (+ `_mention_velocity`/`_search_momentum`), and the original
  generic-keyword Trends path (`fetch_trends`/`src/data/trends.py` +
  `config/sentiment_keywords.yaml`). Only `_cross_zscore` survived (moved into
  `trends_symbols.py`). Symbol-based Trends (`trends_symbols.py`) is now the sole source.

**Getting the most out of Google Trends (ideas to explore):**
- ~~**Trends *topics* (entity mids) over raw ticker strings.**~~ *(shipped — see Done)*
- ~~**Region-aware pulls.**~~ *(shipped — see Done)*
- ~~**Comparative (cross-sector) interest.**~~ *(shipped — see Done)*
- ~~**Multiple derived signals from one series**, not just slope~~ *(shipped — see Done:
  momentum, acceleration, range position, spike, volatility)*
- **Longer window for a seasonal baseline.** Pull 12 months to compute current interest
  vs its seasonal norm (YoY), reducing false momentum from recurring seasonality.
- **Rising / breakout queries.** `pytrends.related_queries()` surfaces "rising" search
  terms per topic — could flag emerging themes within a sector on the page.

**To activate:** enrich `fetch_symbol_trends` (`src/data/trends_symbols.py`) along the
above lines, compute the derived signals in a Trends-only scorer, surface them on
`docs/sentiment.html`, and (optionally) feed a single blended Trends score back into
`score_all(..., sentiment_score=...)` if we later decide it should influence the
composite.

---

## Symbol-based Trends sentiment — Phase 2 (underlying US constituents)

**What:** Extend the Phase 1 symbol-based Trends sentiment (ETF symbols → `region|sector`
attention, shipped 2026-06-26 in `src/data/trends_symbols.py`) by adding the **underlying
US constituent stock symbols** to each US sector's query list. EU stays ETF-only (no
constituent list). Everything flows through the existing
`fetch_symbol_trends` → `_aggregate` → `score_symbol_sentiment` path — Phase 2 only
expands the per-sector symbol set.

**Why:** Phase 1 proved the mechanism on ETF tickers; mega-cap constituent names
(`AAPL`, `MSFT`, `XOM` …) carry far more search volume than ETF tickers and are the
finance-intent terms Trends tracks best (Da/Engelberg/Gao 2011). Stays a confirmer
(toggle-only, composite unchanged).

**Phase 1 validation result (2026-06-26) — DO NOT build Phase 2 on this as-is.** A live
Trends run over the Phase 1 symbol map found:
- **Mechanism works for liquid US ETFs** — `XLK/VGT`, `XLV/VHT`, `XLY/VCR`, `XLP/VDC`,
  `XLI/VIS`, `XLC/VOX` all returned full 13/13 coverage.
- **Signal is contaminated by ambiguous-ticker false positives** that the blocklist didn't
  catch: `US|Communication Services` z **+4.16** (driven by **`VOX`** — Vox Media/party,
  not the ETF) and `EU|Energy` z **+1.27** (driven by **`LOGS`** — the English word). These
  outliers dominate the cross-sectional z.
- **EU `.DE` tickers are dead** (0/13) as predicted; EU's only "signal" is those false
  positives. The EU "alternate" tickers (`LTUG`, `LBNK`, `LOGS`, `LUTI`, `LBRE`…) are
  noise-/collision-prone.
- **Unreliable:** a Google **429** mid-run zeroed a whole batch (US Financials/Materials/
  Real Estate/Utilities → 0), so single runs need the deferred day-cache + gentler batching.

**Conclusion:** adding constituents (more, lower-volume, more-ambiguous tickers) makes the
contamination worse, not better. The real disambiguation fix is **Trends Topics (entity
mids)**, not a growing blocklist — and the better path overall is signed **news sentiment
(FinBERT)**, which sidesteps search-term ambiguity entirely. **Recommended:** park Phase 2;
pursue the FinBERT pivot (below) or Topics first. Quick stopgap if kept: expand the
blocklist (`VOX`, `LOGS`, the `L*` EU alternates) — but it's whack-a-mole.

**Scope / things to resolve when designing:**
- **Top-N liquidity ranking.** Add the top-N (≈10) most liquid constituents per US sector.
  `fetch_sp500_constituents()` (`src/data/constituents.py`, already used by breadth)
  returns the names but **no market caps** — need a cap/volume source or a hardcoded
  mega-cap shortlist to pick "most liquid".
- **Aggregation weighting.** Phase 1 equal-weights ETFs; decide whether constituents are
  equal-weighted alongside the ETFs or down-weighted so one ETF isn't swamped by N names.
- **Volume reality.** Most constituents are thin on Trends; expect only mega-caps to
  survive the existing dead-term drop. Validate coverage before trusting.
- **Rate limits / caching.** Many more terms ⇒ many more pytrends batches ⇒ 429 risk.
  This is where the day-cache deferred in the Phase 1 plan
  (`trends_symbols_<date>.json`) becomes necessary.

**Notes:** Reuses `src/data/constituents.py`. Trends *Topics* (entity disambiguation) and
regional geo are tracked separately under [[the Google-Trends-only tab item above]], not
here. Phase 1 design + plan: `design/{specs,plans}/2026-06-26-symbol-trends-*`.

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

## Themes — EU-available ETF alternatives

**What:** The themes universe (`config/themes.yaml`) currently lists US-domiciled
ETFs (e.g. `ARKK`, `HACK`, `LIT`) that aren't purchasable from EU brokers. Add
UCITS-listed equivalents so EU-based users have actionable instruments, similar
to the sector-level EU instrument references in `config/sector_etfs.yaml`.

**Why:** The dashboard is used from the EU. Showing only US-listed theme ETFs
makes the Themes page informational but not actionable for EU investors.

**To resolve:** Research UCITS equivalents per theme (iShares, VanEck, L&G, etc.);
decide whether to score the UCITS ETF directly (different liquidity/tracking) or
keep scoring the US ETF and show the UCITS as a reference instrument.

---

## Themes — full tab parity with sectors

**What:** Give the Themes page the same tab structure as the Sectors page:
**Leaderboard** (done), **RRG**, **Drill-down**, **Movers**, **History**, and
optionally **Backtest** and **Guide**. Currently Themes only has the leaderboard
with rank-delta and trajectory badges.

**Why:** The sector page's tabs (especially RRG for rotation timing and
Drill-down/History for conviction) are where the real analytical value lives. The
Themes leaderboard alone shows current state but not trajectory context.

**Notes:** Phase 3 of the thematic ETF build (RRG scatter, composite-history
chart, Trends sentiment for themes) was already noted in the Phase 1/2 Done
entries. This formalizes it as a standalone item and expands scope to full tab
parity including Movers and Drill-down.

---

## Remove region-split / composite view toggle

**What:** Remove the Region-split vs Composite view toggle from the leaderboard
and all supporting code. Keep US and EU sectors separate at all times — no
merged "Global" composite rows.

**What to remove:** The `<select id="sector-view-toggle">` control, the
`data-view="split"` / `data-view="composite"` row attributes, the
`mergeComposite` logic in `rescore.js`, the `composite_rows` generation in
`dashboard/build.py`, the `view_split` / `view_composite` i18n keys, and the
`localStorage` persistence of the view choice.

**Why:** The composite view (simple-mean US+EU) is statistically questionable
(different benchmarks, different cohorts) and adds UI complexity. Keeping regions
separate is the defensible default for a rotation scanner.

---

## Renderable scan history (view past scans in the dashboard)

**What:** In the History tab's scan index table, make the "Showing" indicator
selectable — clicking any past scan renders the full leaderboard / dashboard
view for that scan's data, not just a downloadable raw text report.

**Why:** The per-scan Markdown reports (`docs/reports/report_<scan_id>.md`) are
hard to read as raw text. Being able to flip through past scans in the actual
dashboard UI — with the same leaderboard, scores, badges, and breakdowns — makes
it practical to compare how sectors looked at different points in time.

**To resolve:** Decide whether this is server-side (pre-render N historical
dashboard snapshots at build time) or client-side (fetch historical scores via
JSON and re-render the leaderboard dynamically). Client-side is more flexible but
requires shipping the score data as JSON; server-side is simpler but multiplies
the build output.

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
  finance-tuned, multilingual sentiment model. **Now the recommended sentiment direction**
  after the 2026-06-26 Trends validation showed search-attention is noisy, directionless,
  and ambiguous-ticker-contaminated (see the symbol-Trends Phase 2 item above). FinBERT
  gives **signed** polarity (positive/negative), not just attention, and sidesteps
  search-term ambiguity. It's the *scorer*, paired with a free news feed — **GDELT**
  (free global news tone) or **Alpha Vantage** `NEWS_SENTIMENT` (free tier). Free + local
  (`transformers` + `torch`, ~400 MB model, CPU inference, no API key) — fits "free only",
  but a heavier dependency than the current stack. Base FinBERT is English-only; EU/Swedish
  needs a multilingual model or translate-then-score.
- **Streamlit live drill-down** (optional) — interactive drill-down UI

---

## Done

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
