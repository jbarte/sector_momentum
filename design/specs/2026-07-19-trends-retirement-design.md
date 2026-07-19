# Retire Google Trends Sentiment — Design

**Date:** 2026-07-19
**Status:** Approved
**Backlog item:** "Retire (or demote) Google Trends sentiment" (queued 2026-07-19)

## Problem

Google Trends has been effectively dead from CI since ~2026-07-14: every batch
429-rate-limited (0–1/25 sectors live per scan), so the honesty guard NULLs all
Trends scores anyway. The fetch + comparative-interest + rising-queries passes
burn ~90 minutes of every scan run in backoff sleeps for nothing. FinBERT
(shipped 2026-07-17, first successful production run 2026-07-19, scan 135) now
feeds `sentiment_score` in the composite path for sectors.

Separately, GDELT's own rate limiting capped FinBERT at 6/11 sectors scored on
its first production run — the retirement frees enough scan-time budget to fix
that too.

## Decisions (settled during brainstorming)

1. **Theme sentiment is dropped.** Themes are Trends-only (FinBERT covers
   sectors only); extending FinBERT to themes is real new work and worsens the
   GDELT budget. Themes keep price-pillar scoring; `theme_scores.sentiment_score`
   saves as NULL going forward.
2. **The Trends code is removed entirely**, not gated. Git history and the
   Parked "Symbol-based Trends sentiment — Phase 2" backlog item preserve the
   record. Dead-but-gated code rots.
3. **Historical data stays.** `sentiment_signals` and `theme_sentiment_signals`
   keep their DDL definitions and existing rows. `theme_sentiment_signals`
   stops receiving rows entirely; `sentiment_signals` keeps receiving the
   FinBERT news_* rows but no Trends-derived rows. No DDL change, no
   backup/restore change, FK child list unchanged.
4. **A small GDELT mitigation ships in the same PR** (parameter changes only,
   no new architecture).

## Design

### 1. Scan pipeline (`scan.py`)

Delete:

- Step 8 sector Trends fetch, scoring, live-coverage guard, and the
  Trends-z assignment to `sentiment_score`
- Derived-Trends-signals build (long-format rows for `sentiment_signals`)
- Comparative-interest pass (`fetch_comparative_interest`, `attention_level` rows)
- Rising-queries pass (`fetch_rising_queries`, `rising_queries` rows)
- Trends day-cache load/save and the `--no-cache` CLI flag
- The themes block's Trends fetch (`build_theme_symbol_map`,
  `load_theme_entities`, `_MIN_LIVE_THEMES`, theme sentiment rows)

New sentiment flow: `sentiment_score` starts NULL for all sectors. Step 8d
(GDELT + FinBERT) fills it via the existing parent-map path. If FinBERT fails,
scores stay NULL — honest, and the dashboard already renders NULL sentiment as
faded points at y=0. The log message "continuing with Trends score" becomes
"sentiment stays NULL for this scan".

**`sentiment_signals` stays live — for FinBERT rows only.** Step 8d already
appends the FinBERT info rows (`news_polarity`, `news_count`,
`news_positive_pct`, `news_negative_pct`) to `sentiment_signals_df`; that
persistence continues unchanged. What stops is everything Trends-derived
(momentum/acceleration/range/spike/volatility/seasonal, `attention_level`,
`rising_queries`). `save_scan`'s signature is unchanged. `save_theme_scan`
stops receiving `sentiment_signals_df` (theme sentiment rows end entirely).

### 2. GDELT mitigation (`src/data/news_sentiment.py`)

`fetch_news_headlines` parameter changes:

- `sleep_s`: 5.0 → **20.0** (pause between the 11 sector queries)
- `max_retries`: 3 → **4** (backoff ladder 60/120/240/480s)

Also fix in passing: a 429 on the final attempt currently falls out of the
retry loop silently via `continue` — log the give-up the same way the
exception path does ("GDELT fetch failed for %s after %d retries").

Worst case (all 11 sectors exhaust 4 retries) is ~2.9h of backoff, but the
observed pattern (2026-07-19 run: 5 of 11 sectors throttled at 3 retries) puts
the realistic added cost at ~15 min against the ~90 min the retirement frees.
The step stays non-fatal; no total-budget guard is added (YAGNI — revisit only
if a real run approaches the 6h workflow timeout).

### 3. Deletions

| Path | What |
|---|---|
| `src/data/trends_symbols.py` | whole module (777 lines) |
| `src/data/trends_cache.py` | whole module (day-cache; the failing-400 bucket load dies with it) |
| `tests/test_trends_cache.py`, `test_trends_symbols_entities.py`, `test_trends_symbols_fetch.py`, `test_trends_symbols_map.py`, `test_trends_symbols_region.py`, `test_trends_symbols_score.py`, `test_trends_symbols_transforms.py` | all Trends tests (~700 lines) |
| `config/trends_geo.yaml`, `config/trends_entities.yaml`, `config/trends_blocklist.yaml` | Trends config |
| `config/themes.yaml` | only the `trends:` and `trends_entities:` sections |
| `scripts/resolve_trends_entities.py` | dev-only entity-mid resolver |
| `requirements.txt` | the `pytrends==4.9.2` pin; regenerate both lockfiles with `uv pip compile … --python-platform x86_64-unknown-linux-gnu --upgrade` |

The `trends-cache` Supabase Storage bucket becomes unused; deleting the bucket
itself is a manual post-merge step in the Supabase dashboard (optional,
harmless to leave).

### 4. DB & state (`src/state.py`)

- DDL for `sentiment_signals` and `theme_sentiment_signals` stays; historical
  rows stay; `_SCAN_CHILD_TABLES` stays as-is (same-day replace must still
  delete child rows for scans that have them).
- `get_sentiment_signals_for_latest_scan` **stays** (the dashboard reads the
  FinBERT news_* rows through it). Delete only
  `get_theme_sentiment_signals_for_latest_scan`.
- `save_scan` / `save_theme_scan` signatures are unchanged; only what scan.py
  passes changes (FinBERT-only rows for sectors, nothing for themes).

### 5. Dashboard

Sentiment page (`docs/sentiment.html` via `dashboard/sentiment.py` +
`sentiment.html.j2`) becomes FinBERT-only:

- **Keep:** Data ⇄ Sentiment scatter (sectors), FinBERT info columns
  (Polarity, Articles, Pos%, Neg%), guide text (updated to drop Trends
  references).
- **Delete:** derived-Trends-signals table (momentum/accel/range/spike/vol/
  seasonal), Attention column, rising-queries expandable panels, the
  Sectors ⇄ Themes toggle and the theme scatter series (themes no longer have
  sentiment), and all their i18n keys (EN+SV).
- `dashboard/sentiment.py` keeps its pivot but only over the news_* signal
  names (the FinBERT columns read from `sentiment_signals` via
  `get_sentiment_signals_for_latest_scan`); the Trends-metric formatting and
  rising-queries JSON parsing are deleted.
- Themes page: no visible change (its backtest note already says sentiment
  excluded); theme leaderboard's sentiment field renders "—" for NULL as today.

### 6. Docs & backlog

- BACKLOG.md: delete the Queued section, add Done entry at top (same PR).
- ARCHITECTURE.md + README: remove Trends from the data-flow description.
- Sentiment-page guide: FinBERT-only wording.
- The Parked "Symbol-based Trends sentiment — Phase 2" item stays untouched as
  the historical record.

## Error handling

- FinBERT/GDELT failure → `sentiment_score` NULL for that scan; scan continues
  (existing non-fatal step 8d behavior, unchanged).
- No Trends failure modes remain.

## Testing

- Existing FinBERT tests (`tests/test_news_sentiment.py`, 22 tests) continue
  to cover the surviving sentiment path; add one test for the new
  final-attempt-429 logging if practical.
- Full `pytest` must pass with the 7 Trends test files deleted (CI verifies).
- Grep-clean check: no remaining references to `trends_symbols`,
  `trends_cache`, `pytrends`, `fetch_comparative_interest`,
  `fetch_rising_queries`, `attention_level`, `rising_queries` outside
  BACKLOG.md history, design docs, and the two dormant DDL blocks.
- Local `python3 dashboard/build.py` run against the live DB (which still
  holds historical Trends rows) must succeed — proves the dashboard no longer
  reads the dormant tables.

## Success criteria

1. Daily scan wall-clock drops by roughly an hour or more.
2. FinBERT sector coverage improves toward 11/11 on typical runs.
3. Sentiment page renders FinBERT data only; no Trends artifacts.
4. No DDL changes; restore of an old backup still works.
