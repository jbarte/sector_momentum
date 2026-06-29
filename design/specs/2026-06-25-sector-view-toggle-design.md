# Sector view toggle — composite vs region-split

**Date:** 2026-06-25
**Status:** Approved design
**Branch:** `feature/sector-view-toggle`

## Goal

Add a dashboard toggle that switches the leaderboard between the current
**region-split** view (one row per `region|sector` — 22 rows, e.g. "Technology
(US)" and "Technology (EU)" separately) and a **composite** view (one row per GICS
sector — 11 rows — combining the US and EU entries into a single sector score).
Default stays region-split.

## Why

Sometimes the useful read is GICS-level regardless of region ("is Technology
strong globally?"); other times the region split matters. Let the user flip
between them. This pairs naturally with the existing sentiment-weight toggle —
both are leaderboard view controls.

## Architecture

Mirror the existing sentiment toggle exactly: a **client-side recompute** layered
on a server-rendered initial state. The one new idea is *merging the two regional
series into one before ranking*.

### Combine rule — simple mean

For each scan and each GICS sector, the composite entry is:

- `data_score = mean(US.data_score, EU.data_score)`
- `sentiment_score = mean(US.sentiment_score, EU.sentiment_score)`

Then the **existing** pipeline runs unchanged over the 11 merged entries:
blend `(1 − W)·data + W·sentiment`, `rankAverage` (descending, average tie-break),
ΔRank, Δcomposite, `emerging = ΔRank > 0 && Δcomposite > 0`, and the OLS rank
trajectory.

**Caveat (documented, accepted):** `data_score`/`sentiment_score` are z-scored
*within each region's 11-sector cohort*, so the mean is an average of relative
standings, not a true global z-score. This is acceptable for an info-level view
and was chosen over weighted-blend and global re-pooling for being pure
client-side with zero new data. No scoring or `scan.py` changes.

### Reuse, not duplication

The combine logic lives in two mirrored places — the same arrangement that exists
today, where `rescore.js` already mirrors `build.py`'s rank/trajectory semantics
(see the header comment in `dashboard/assets/rescore.js`):

- **Python (`dashboard/build.py`)** — build a composite DataFrame by grouping
  `history_df` on `(scan_id, gics_sector)` and averaging the two regions'
  `data_score` and `sentiment_score` per group, then run it through the existing
  `_build_leaderboard_rows` / `_compute_rank_trajectories` to render the 11
  composite rows + breakdowns for the initial sentiment-off state (W = 0), exactly
  as the region-split rows are rendered today.
- **JS (`dashboard/assets/rescore.js`)** — add `mergeComposite(data)` that takes a
  `RESCORE_DATA`-shaped object and returns a new one keyed by bare sector name,
  with each per-scan `data`/`sentiment` value the mean of the two regions. Feed the
  result to the existing `rescore()` — no change to `rescore()` itself.

## DOM strategy

Pre-render **both** row sets into the leaderboard `<tbody>`: the 22 region-split
rows (each with its breakdown row, as today) **plus** 11 composite rows (each with
its breakdown row). Every leaderboard row and breakdown row carries a
`data-view="split"` or `data-view="composite"` attribute. The toggle flips a class
on the table (or a `body`/container) that hides one set and shows the other via
CSS.

This is the strategy that lets the composite breakdown **reuse the existing
per-region breakdown HTML** rather than rebuilding it in JS.

- Composite row keys: bare sector name (`Technology`) in `data-sector-key`.
- Composite row ids: `composite-Technology` (sector name slugified the same way
  `sector_id` is today), kept distinct from the split `US-Technology` /
  `EU-Technology` ids so breakdown-toggle and score-tree selectors don't collide.

### Composite breakdown panel — two regional sub-panels

A composite row's breakdown is:

1. A one-line header summarizing the average, e.g.
   `US #2 (0.62) + EU #5 (0.41) → composite 0.52`. The composite value and the two
   regional ranks/scores update on toggle (see interaction below).
2. The two existing `breakdown-inner` blocks for `US|X` and `EU|X`, rendered
   server-side by reusing the current per-region breakdown builder.

The regional sub-panels carry their normal `data-sector-key="US|X"` /
`"EU|X"` score-trees, so the existing per-key score-tree update in
`applyRanking()` keeps them correct for free. Only the composite header's
aggregate value needs a small dedicated update.

## UI & interaction

A "Sector view" control placed beside the existing sentiment control
(`#sentiment-control` area). Two states: **Region-split** (default) and
**Composite**. Persisted in `localStorage` under key `sectorView` (values
`"split"` | `"composite"`; default `"split"` when unset or invalid).

`applyRanking()` becomes view-aware:

1. Read the sentiment weight `W` (from the existing sentiment toggle) **and** the
   active view (from the new toggle).
2. Build the dataset: split view uses `RESCORE_DATA` directly; composite view uses
   `Rescore.mergeComposite(RESCORE_DATA)`.
3. Call `Rescore.rescore(dataset, W)`.
4. Update only the **visible** row set (the rows matching the active
   `data-view`), keyed by `data-sector-key`.
5. Re-sort only the visible rows; keep each breakdown row attached after its
   leaderboard row.

The two toggles compose: the sentiment weight applies identically in either view.

### Adjacent logic scoped to visible rows

- **Sortable headers** (the existing header-click sort): scope its row collection
  to visible `tr.leaderboard-row` so it sorts the active view only.
- **Breakdown toggle** (`toggleBreakdown`): already id-based; composite ids are
  unique, so it works unchanged. Switching views hides any open breakdown along
  with its row set (acceptable — no special collapse needed).

## Testing

- **Parity** — extend `tests/test_rescore_parity.py` with a composite-mode case:
  run `rescore.js` under Node on `mergeComposite(data)` output and compare to a
  scipy/numpy reference that averages the two regions per scan then ranks. Asserts
  the JS merge + rank matches Python.
- **Python aggregation** — new unit test in the dashboard test module: the
  composite DataFrame aggregation produces exactly 11 rows per scan with
  `data_score`/`sentiment_score` equal to the mean of the two regions' values.
- **Render context** — `tests/test_dashboard_js.py` already guards that every
  `var X = {{ ... }}` has a matching render-context key; if a new template var is
  emitted (e.g. for composite rows or default view), it is covered automatically;
  add an explicit assertion if a new top-level JS var is introduced.

## Edge cases

- **Sector symmetry:** confirmed 11 US sectors and 11 EU sectors, fully matched —
  every composite entry has exactly one US and one EU side. No single-region
  fallback path is needed.
- **NaN scores:** `_build_rescore_data` already coerces missing/NaN values to
  `0.0`; `mergeComposite` averages those zeros, so a region with no sentiment this
  scan contributes 0 to the mean (same neutral behavior as today's split view).
- **Fewer than 2 scans:** `rescore()` already returns flat trajectory / zero deltas
  for `nScans < 2`; the merged dataset inherits this unchanged.

## Out of scope

- Weighted (market-cap) blending and global z-score re-pooling — explicitly
  rejected in favor of the simple mean; can be revisited later if wanted.
- Any change to the canonical stored composite, `scan.py`, scoring, or DB schema.
- Composite views on the other tabs (RRG scatter, sentiment scatter, drilldown) —
  this toggle affects the **leaderboard only**.
