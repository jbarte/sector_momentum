# Per-Region Cohort Scoring — Design Spec

**Date:** 2026-07-20
**Status:** Draft
**Backlog item:** Cohort mismatch — z-score within region, not globally

## Problem

The live scan scores all 25 sectors (11 US + 14 EU) in a single `score_all()`
call. This means z-scores, composites, and ranks are computed across a mixed
pool where US and EU sectors compete directly. The backtest already scores
per-region. The mismatch makes live ranks incomparable with backtest ranks and
muddies cross-region comparisons (EU's 14-sector pool dilutes US z-scores and
vice versa).

## Decision

Score US and EU as independent cohorts. Each region gets its own z-scores,
composite scores, and rank sequence (US: 1–11, EU: 1–14). This aligns the live
scan with the backtest and produces ranks that are meaningful within each
region's opportunity set.

## Scope

### In scope

1. **scan.py** — split `wide_df` by region, call `score_all()` twice, concat
2. **z-scores** — `zscore_cross_section()` called per region so `signals.z_value` is region-local
3. **Dashboard leaderboard** — two stacked tables (US then EU) instead of one mixed table
4. **rescore.js** — rank within region groups when sentiment toggle re-scores
5. **scan-history.js** — render per-region tables when viewing historical scans
6. **scan-digest.js** — "New in Top 5" / movers computed per region
7. **History backfill script** — recompute per-region scores/ranks/z-values for every stored scan
8. **Report** — markdown ranked table becomes two sections (US, EU)
9. **BACKLOG.md** — delete queued item, add Done entry

### Out of scope / unchanged

- **`score_all()` itself** — no API change; it already scores whatever DataFrame it receives
- **`compute_deltas()`** — already matches on `(region, gics_sector)`, works unchanged
- **RRG chart** — plots RS-ratio vs RS-momentum per sector, not rank-ordered
- **Drill-down/breakdown panels** — show per-sector signal values, not rank-ordered
- **Movers bar chart** — shows delta_composite which is region-independent
- **Badges/alerts** (`src/alerts.py`, `dashboard/badges.py`) — already operate per `region|sector` key; per-region ranks make their top-5 semantics more meaningful without code changes
- **Forward-return validation** (`dashboard/validation.py`) — already filters by region
- **Feed** (`dashboard/feed.py`) — `_top_n_by_region()` already groups by region
- **Themes** — themes have their own scoring pipeline, unaffected
- **Sentiment** — FinBERT scores are cross-region by construction; stored values unchanged
- **DB schema** — `scores` table already has `region` + `gics_sector` columns; no DDL change

## Design

### 1. scan.py — per-region scoring

Currently (lines 287–343):

```python
wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
# ... sentiment ...
scored = score_all(wide_df, ...)
```

Change to:

```python
wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
# ... sentiment unchanged ...

# Score per region
scored_parts = []
for region_prefix in ("US", "EU"):
    mask = wide_df.index.str.startswith(f"{region_prefix}|")
    region_df = wide_df[mask]
    if region_df.empty:
        continue
    region_sentiment = sentiment_score[mask] if sentiment_score is not None else None
    region_scored = score_all(
        region_df,
        weights_path="config/weights.yaml",
        sentiment_score=region_sentiment,
        blend_sentiment=False,
    )
    scored_parts.append(region_scored)
scored = pd.concat(scored_parts)
```

The `score_all()` function is unchanged — it z-scores whatever it receives.
With 11 US rows, ranks are 1–11. With 14 EU rows, ranks are 1–14.

### 2. z-scores for signals table

Currently (line 372):

```python
z_df = zscore_cross_section(wide_df)
```

Must also be split per region so `signals.z_value` matches the cohort:

```python
z_parts = []
for region_prefix in ("US", "EU"):
    mask = wide_df.index.str.startswith(f"{region_prefix}|")
    if mask.any():
        z_parts.append(zscore_cross_section(wide_df[mask]))
z_df = pd.concat(z_parts)
```

### 3. Dashboard leaderboard — two stacked tables

The `index.html.j2` template currently renders one `<tbody>` from
`leaderboard_rows`. Change to two region sections:

```
┌─────────────────────────────────────────────────┐
│ US Sectors                                       │
├────┬──────────────────┬────────┬─────────┬──────┤
│ #  │ Sector           │ Comp.  │ Trend   │ Δ    │
├────┼──────────────────┼────────┼─────────┼──────┤
│ 1  │ Technology       │ +1.23  │ ↑↑      │ +2   │
│ …  │ …                │ …      │ …       │ …    │
│ 11 │ Utilities        │ −0.89  │ ↓       │ −1   │
├────┴──────────────────┴────────┴─────────┴──────┤
│ EU Sectors                                       │
├────┬──────────────────┬────────┬─────────┬──────┤
│ 1  │ Banks            │ +1.45  │ ↑       │ +3   │
│ …  │ …                │ …      │ …       │ …    │
│ 14 │ Travel & Leisure │ −0.67  │ →       │ 0    │
└────┴──────────────────┴────────┴─────────┴──────┘
```

**Implementation:** `_build_leaderboard_rows()` in `dashboard/rows.py` already
returns rows with a `region` field. In `build.py`, split
`leaderboard_rows` into `us_leaderboard_rows` and `eu_leaderboard_rows` and
pass both to the template. The template renders two `<tbody>` sections under
region-header rows within the existing `#leaderboard-table`, keeping one
sortable table (the existing `sortTable()` function continues to work). Each
region header is a full-width `<tr>` with a region label.

The "Region" column stays visible — it's still useful for scan-history views
where the user may not immediately see which region section they're in.

### 4. rescore.js — per-region ranking

Currently ranks all sectors globally in one `rankAverage()` call. Must split
`data.sectors` by region prefix, rank each group independently, then merge
results. The `RESCORE_DATA` JSON structure stays the same — the split logic
lives in the `rescore()` function.

```javascript
// Inside rescore():
var usSectors = sectors.filter(function(k) { return k.indexOf("US|") === 0; });
var euSectors = sectors.filter(function(k) { return k.indexOf("EU|") === 0; });
// rank each group separately, merge into out{}
```

### 5. scan-history.js — per-region rendering

`renderScanLeaderboard()` currently sorts all entries by rank and renders one
table. Must split entries by region, sort each group by rank, and render two
sections (matching the build-time two-table layout).

### 6. scan-digest.js — per-region digest

`computeDigest()` currently checks `s.rank <= 5` across all sectors. Must
split by region so "New in Top 5" means top 5 within each region. The digest
banner renders two groups of chips (US entries, EU entries) or prefixes each
chip with region context.

### 7. History backfill script

**`scripts/backfill_region_ranks.py`** — one-off, re-runnable script:

1. Connect to DB, take a Storage backup first
2. For each scan_id in the `scores` table:
   a. Load raw signals from `signals` table for that scan_id
   b. Build the wide signal matrix (same logic as scan.py step 7)
   c. Call `score_all()` per region (US, EU) to get new composites/ranks
   d. Call `zscore_cross_section()` per region to get new z-values
   e. UPDATE `scores` rows: set `level_score`, `change_score`, `data_score`,
      `composite`, `rank` to the new per-region values
   f. UPDATE `signals` rows: set `z_value` to the new per-region z-scores
3. Each scan_id runs in its own transaction
4. `sentiment_score` values in `scores` are left untouched

The core recomputation logic must be factored into a testable pure function
(DataFrame in, DataFrame out) so the script is a thin DB-wrapper.

### 8. Report

`build_ranked_table()` in `src/report.py` currently renders one markdown
table sorted by rank. Change to two sections:

```markdown
## US Sectors
| # | Sector | Composite | Level | Change | Rank Δ | Comp Δ | ⭐ |
|---|--------|-----------|-------|--------|--------|--------|----|
| 1 | Technology | +1.23 | ... | ... | +2 | +0.15 | 🌱 |
...

## EU Sectors
| # | Sector | Composite | Level | Change | Rank Δ | Comp Δ | ⭐ |
|---|--------|-----------|-------|--------|--------|--------|----|
| 1 | Banks | +1.45 | ... | ... | +3 | +0.22 | |
...
```

`build_movers()` is fine — it already labels movers with region.

`build_swedish_overlay()` keeps top-5 by composite across both regions. The
composites from per-region z-scoring are roughly comparable (both are
standardized to mean=0, sd=1 within their pool) and this is a shortlist, not a
ranking claim.

### 9. Console summary in scan.py

`_print_summary()` currently prints "Top 5 by composite score" from the full
25-sector list. Change to print "Top 5 US" and "Top 5 EU" separately, sorted
by rank within each region.

## Testing

- **Unit test for per-region split:** Create a 25-row synthetic signal
  DataFrame (11 US, 14 EU). Run the per-region scoring. Assert: two rank-1
  rows exist; US ranks are 1–11; EU ranks are 1–14; no rank exceeds its
  cohort size.
- **Unit test for z-score isolation:** Confirm that US z-scores have mean≈0
  across 11 sectors and EU z-scores have mean≈0 across 14 sectors. With the
  old global approach, neither region's z-scores would have mean=0.
- **Backfill logic test:** Feed a synthetic multi-scan signal DataFrame
  through the backfill recomputation function. Assert output ranks are
  per-region (max US rank = 11, max EU rank = 14).
- **rescore.js test:** Existing JS test infrastructure (if any) or manual
  verification that the sentiment toggle produces per-region ranks.
- **Template render test:** Verify the built HTML contains two region header
  elements and the correct number of rows per table.

## Rollback

The backfill script is re-runnable. If per-region scoring causes issues, revert
the scan.py change and re-run a global-cohort version of the backfill script to
restore the old ranks. No schema migration involved, so rollback is a code
revert + data recomputation.

## Migration path

1. Ship the scan.py + dashboard changes (new scans produce per-region ranks)
2. Run the backfill script (historical scans get per-region ranks)
3. Rebuild dashboard from DB (`python3 dashboard/build.py`) to pick up
   backfilled history

Steps 2–3 can run in CI after merge or manually. The dashboard is functional
immediately after step 1 (the latest scan has correct ranks; historical views
show old global ranks until backfill completes).
