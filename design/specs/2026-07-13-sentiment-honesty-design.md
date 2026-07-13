# Sentiment Honesty Fixes

**Date:** 2026-07-13
**Status:** Approved
**Backlog item:** Sentiment honesty fixes (coverage collapse + silent semantics)

## Summary

Make the Google-Trends sentiment signal honest about its own data quality.
A 2026-07-13 live-DB audit found the signal broken in production: coverage
collapsed to ~1 live series of 22 since ~scan 123, so dead sectors receive
fake shared z-scores and the one live sector gets an absurd outlier z. This
spec fixes the scoring to store NULL for missing data, pins a silently-changed
slope window, corrects a health log that hid the collapse, adds cheap fetch
mitigations, and removes a dead config weight.

Signal stays **toggle-only / info-only** — it never blends into the canonical
composite. No schema change (sentiment_score is already a nullable REAL). No
dashboard code change (NaN→NULL→"—" and neutral-in-toggle already work).

## Root cause (audit findings)

- `_aggregate` (`src/data/trends_symbols.py`) emits `pd.Series([0.0]*window)`
  for a sector-key with no live symbols. That fake zero-series is
  indistinguishable from a real flat series.
- `score_symbol_sentiment` slopes every key and z-scores the whole set
  together. With 21 dead keys (slope≈0) and 1 live key, the result is a
  degenerate cross-section: live key z=+4.477, all others share a fake z.
- Onset (~scan 123, 2026-07-06) coincides with the region-aware pulls
  landing (4× API volume; EU `.DE`/geo pulls have never returned data and
  burn quota, likely triggering the 429s that zero the US batches).
- `score_symbol_sentiment` slopes over the whole series; PR #79 changed the
  series length from 13 to 52 weeks, silently turning the headline sentiment
  into a 1-year trend — contradicting its docstring (sentiment == 13-week
  `momentum`).
- The scan health log counts non-zero values *after* z-scoring (z is never
  exactly 0), so it logged "22/22 non-neutral" while coverage was ~1/22.

## 1. Coverage guard (core fix)

**File:** `src/data/trends_symbols.py` — `_aggregate`, `score_symbol_sentiment`

- `_aggregate` no longer emits a zero-series for a dead sector-key. It omits
  dead keys from its returned dict entirely (a key is dead when it has no live
  symbol — no symbol in `norm_by_symbol` with any non-zero value).
- `score_symbol_sentiment` takes the (now live-only) `trends_by_key`:
  - Computes the momentum slope (see §2) for each live key.
  - **Minimum-live threshold** `_MIN_LIVE_SECTORS = 8`: if fewer than 8 keys
    are live, return an all-`NaN` Series over the live keys (the whole
    cross-section is too thin to z-score meaningfully).
  - Otherwise z-score the live keys **against each other only**, and return a
    Series carrying those z-scores. Dead keys are simply absent from the
    returned Series.
- The returned Series therefore contains real z-scores for a healthy live
  subset, or is all-NaN / empty when under threshold. Dead keys are never
  assigned a value here.

**Downstream NaN plumbing** (`scan.py` step 8):

- Change `sentiment_score.reindex(wide_df.index, fill_value=0.0)` to
  `sentiment_score.reindex(wide_df.index)` — keys absent from the scored
  Series (dead sectors, or all keys when under threshold) become NaN, not a
  real 0.0.
- `save_scan` already maps NaN → SQL NULL via `_to_float_or_none`. No change.
- Dashboard already renders NULL sentiment as `—` (`rows.py`) and excludes it
  from the Data⇄Sentiment scatter (`figures.py:206`). No change.
- `_build_rescore_data` (`figures.py:523`) already coerces NULL → 0.0 for the
  client blob; in the toggle blend `(1-W)·data + W·sentiment`, a 0 sentiment
  contributes nothing, so a dead sector stays neutral in the ranking. This is
  the correct behavior — no change.

**Intended ripple into derived signals:** `scan.py` step 8 builds
`sentiment_signals_df` by iterating `_trends_by_key.items()`. Since dead keys
are now omitted from `_trends_by_key`, dead sectors will no longer get
(previously all-zero) derived-signal rows — they'll simply have no rows for
that scan. This is deliberate and on-theme (a sector with no Trends data
should not show fabricated zero derived signals); the sentiment page already
renders missing rows as absent/"—". Flagged here so it reads as intended, not
an accidental regression. The `attention_level` and `rising_queries` rows are
built from separate fetches (`fetch_comparative_interest`,
`fetch_rising_queries`) and are unaffected by the `_aggregate` change.

## 2. Pin the momentum window to 13 weeks

**File:** `src/data/trends_symbols.py`

- Add module constant `_MOMENTUM_WINDOW = 13`.
- `score_symbol_sentiment` slopes the trailing `_MOMENTUM_WINDOW` weeks:
  `_slope(list(series)[-_MOMENTUM_WINDOW:])`, matching `derived_signals`'
  `momentum` exactly so `sentiment_score == z(momentum)` as documented.
- `derived_signals` uses the same constant for its `momentum`/`recent` slice
  (currently a bare `[-13:]`) so the two share one source of truth.
- The full 52-week series stays intact for `seasonal_ratio`, which
  legitimately needs the year.

## 3. Honest health log + cheap fetch mitigations

**File:** `scan.py` step 8; `src/data/trends_symbols.py` fetch defaults

- Replace the post-z-score `"%d/%d non-neutral"` log with a count of live
  series *before* scoring. Because `_aggregate` now omits dead keys (§1),
  `len(_trends_by_key)` after the fetch **is** the live count directly — the
  log reads it from there, independent of the scorer's return (which is
  all-NaN under threshold). New log, e.g.
  `"Symbol sentiment: N/22 sectors have live Trends data (guard threshold: 8)"`,
  and when `N < _MIN_LIVE_SECTORS` also log that the scan's sentiment was
  NULLed.
- **Fetch order:** ensure US geos are fetched before EU so quota exhaustion
  hits the never-working EU pulls last. `fetch_symbol_trends` iterates
  `_symbols_by_region` order; make that order deterministic US-then-EU.
- **Inter-batch sleep:** modestly raise the default `sleep_s` to reduce 429
  pressure. No retry frameworks, no proxies. If coverage recovers, good; if
  not, the guard keeps the signal honest and FinBERT (separate queued item)
  remains the real fix.

## 4. Remove the dead pillar weight

**Files:** `config/weights.yaml`, `src/scoring.py`

- Remove the `pillars: {data: 0.70, sentiment: 0.30}` block from
  `weights.yaml`, replaced by a comment: sentiment is toggle-only by design
  (composite is pure-data); revisit blending only if/when FinBERT provides
  signed polarity.
- `score_all` currently reads `cfg["pillars"]["data"]` / `["sentiment"]`
  unconditionally (`scoring.py:163-164`), even though they're only used on the
  `blend_sentiment=True` path. Make these reads lazy so a missing `pillars`
  block doesn't `KeyError` on the normal (`blend_sentiment=False`) scan: read
  them with `.get("pillars", {})` defaults (data→1.0, sentiment→0.0), or read
  them only inside the `if blend_sentiment` branch. Behavior on the
  always-used `blend_sentiment=False` path is unchanged (it already hardcodes
  `data_weight=1.0, sentiment_weight=0.0`).
- Closes the queued "Sentiment → composite blend decision" backlog item.

## Testing

`tests/test_trends_symbols_score.py` (and siblings):

- `score_symbol_sentiment`: a dead key is absent from the result; a healthy
  live subset (≥8) is z-scored among themselves (mean≈0); below threshold
  (<8 live) returns all-NaN; exactly at threshold scores normally.
- `_aggregate`: a sector-key with no live symbols is omitted from the dict
  (not returned as a zero-series).
- `_MOMENTUM_WINDOW`: a 52-long series and its trailing-13 slice produce the
  same `score_symbol_sentiment` slope (the 52-week tail beyond 13 doesn't
  affect the headline).

`tests/test_scoring.py` (or wherever `score_all` is tested):

- `score_all` with a weights file lacking a `pillars` block still runs on the
  `blend_sentiment=False` path (regression guard for the config deletion).

No new backend/schema tests — sentiment_score column already nullable.

## Out of Scope

- FinBERT / news sentiment (separate queued item; the recommended long-term
  direction).
- Disabling EU pulls entirely (chose fetch-reorder mitigation instead; revisit
  only if reorder doesn't recover coverage).
- Any dashboard code change (existing NaN handling already correct).
- Schema changes.
- **Post-merge manual check (not a code task):** verify scan 130 — the first
  production run of PR #79's `seasonal_ratio` + `rising_queries` enrichments
  and of these guard changes — actually recovers coverage and populates the
  enrichment rows. Noted in the PR body.
