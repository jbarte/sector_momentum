# Renderable Scan History

**Date:** 2026-07-12
**Status:** Approved
**Backlog item:** Renderable scan history (view past scans in the dashboard)

## Summary

Make past scans viewable in the dashboard: clicking any scan row in the
History tab's scan index rebuilds the Leaderboard table with that scan's
scores. Instant client-side switching via an embedded JSON blob — no
server, no navigation, no new pages.

Sectors page only. Charts (RRG, movers, drilldown, history) stay
unchanged — they already show multi-scan trends. Themes and sentiment
pages are out of scope.

## Data Blob

### Shape

A new `SCAN_HISTORY` JSON variable embedded in `index.html.j2`:

```json
{
  "scans": [
    {"id": 142, "date": "2026-07-12 06:00 UTC", "sectors": 22, "top": "Technology (US)"},
    {"id": 141, "date": "2026-07-11 06:01 UTC", "sectors": 22, "top": "Financials (US)"}
  ],
  "scores": {
    "142": {
      "US|Technology": {"rank": 1, "composite": 0.85, "level": 0.7, "change": 0.4, "data": 0.55, "sentiment": 0.2},
      "EU|Energy": {"rank": 2, "composite": 0.6, "level": 0.3, "change": 0.2, "data": 0.1, "sentiment": 0.0}
    },
    "141": { ... }
  }
}
```

- `scans` array: ordered newest-first, one entry per scan with id, formatted
  date, sector count, and top sector label.
- `scores` object: keyed by scan_id (string), each value is an object keyed
  by `region|sector` with the six score fields.
- Rank-delta is computed client-side (current scan rank minus previous scan
  rank from the `scans` ordering) rather than shipped per scan.

### Size

~3 KB per scan (22 sectors × ~130 bytes). At 200 scans ≈ 600 KB — well
within budget for a page already shipping ~1 MB of Plotly.

### Builder

New function `_build_scan_history_data(all_scores_df)` in
`dashboard/build.py` (alongside `_build_rescore_data`, which follows the
same pattern — transform scores DataFrame into a JSON-serializable dict).
Input: the existing `all_scores_df` (loaded with
`get_scan_history(conn, n_scans=None)`). Output: the dict above. Serialized
with `json.dumps` and passed to the template as `scan_history_json`.

No new data loaders in `src/state.py` — the existing `get_scan_history`
already provides all the scores data needed.

## Scan Index Interaction

### Click to view a scan

The scan-index table rows in the History tab gain `data-scan-id` attributes
and become clickable (delegated click handler, same pattern as leaderboard
row expansion). Keyboard accessible (Enter/Space on focused rows via
`tabindex="0"`).

Clicking a scan row:

1. Reads `SCAN_HISTORY.scores[scanId]` for that scan
2. Rebuilds the leaderboard `<tbody>` — rank, sector, region, composite,
   level, change, data, sentiment, rank-delta (computed vs the scan
   immediately before it in `SCAN_HISTORY.scans`), rank badge (top-3 class)
3. Moves the "Showing" badge to the clicked scan row
4. Switches to the Leaderboard tab
5. Updates the header scan-date display
6. Shows a "Viewing scan #N — Back to latest" banner above the leaderboard
7. Disables the sentiment-toggle control (not applicable to historical views)

### What is NOT shown for historical scans

- **Trajectory badges** — need 3-5 scans of context, not worth computing
  client-side. Cell shows "—".
- **Entry/Exit setup badges** — derived from trajectory. Hidden.
- **Breakdown panels** — require per-scan signal data not shipped. Rows are
  not expandable; the chevron is hidden.
- **Sentiment toggle rescoring** — only meaningful for the latest scan.
  The toggle is disabled while viewing a historical scan.

### Restoring latest

The original server-rendered `<tbody>` innerHTML is cached in a JS variable
on page load. A "Back to latest" link in the banner:

1. Restores the cached original tbody
2. Hides the banner
3. Moves "Showing" back to the latest scan row
4. Restores the original header date
5. Re-enables the sentiment toggle

## JS Implementation

New file: `dashboard/assets/scan-history.js`, copied to `docs/assets/` at
build time (same pattern as `rescore.js`). Loaded via a `<script>` tag in
`index.html.j2`.

### Core functions

**`renderScanLeaderboard(scanId)`**
- Looks up scores from `SCAN_HISTORY.scores[scanId]`
- Finds the previous scan from `SCAN_HISTORY.scans` ordering for delta
  computation
- Builds `<tr>` HTML for each sector, sorted by rank ascending
- Formats values matching the Jinja template: `0.850` for composite,
  `+0.3` for delta, `▲`/`▼` arrows, `.top3` class for rank ≤ 3
- Replaces `#leaderboard-table tbody` innerHTML

**`showScan(scanId)`**
- Calls `renderScanLeaderboard(scanId)`
- Updates the "Showing" badge position in the scan-index table
- Updates header date from `SCAN_HISTORY.scans`
- Shows the banner, switches to Leaderboard tab
- Disables sentiment toggle

**`restoreLatest()`**
- Restores cached original tbody
- Hides banner, restores header date
- Moves "Showing" back to latest
- Re-enables sentiment toggle

### Event handling

Delegated click handler on the scan-index `<table>`, same pattern as the
leaderboard breakdown toggle. Keyboard: `tabindex="0"` on scan rows,
Enter/Space triggers the click handler.

~120-150 lines of vanilla JS. No external dependencies.

## Template Changes

### `index.html.j2`

- New `<script src="assets/scan-history.js"></script>` tag
- New `var SCAN_HISTORY = {{ scan_history_json | js_json }};` in the
  existing `<script>` block
- Scan-index `<tr>` elements gain `data-scan-id="{{ s.scan_id }}"`,
  `tabindex="0"`
- New `#scan-history-banner` div above the leaderboard table (hidden by
  default), containing the "Viewing scan #N" text and "Back to latest" link

### `_i18n.html.j2`

SV translations:
- `scan_viewing: "Visar skanning #"`
- `scan_back_to_latest: "Tillbaka till senaste"`

### `_style.html.j2`

- `#scan-history-banner` — info bar styling (bg-raised, left border accent,
  padding, flex layout with justify-between)
- `.scan-index tr[data-scan-id]` — `cursor: pointer`, hover highlight
- `.scan-index tr[data-scan-id]:focus-visible` — keyboard focus ring

~15 lines of CSS.

## Testing

| Test | File | What |
|------|------|------|
| `test_build_scan_history_data_shape` | `test_dashboard_js.py` | Builder returns correct structure: scan count, score keys, required fields per sector |
| `test_scan_history_json_in_rendered_output` | `test_dashboard_js.py` | Rendered HTML contains `var SCAN_HISTORY =` that parses as valid JSON with `scans` and `scores` keys |

No new backend tests — no new data loaders or schema changes.

## Out of Scope

- Themes page scan selector (separate item if useful)
- Sentiment page scan selector (separate item)
- Per-scan signal breakdowns in the expanded panel
- Trajectory/setup badges for historical scans
- Limiting how many scans ship in the JSON blob (revisit if scan count
  exceeds ~500 and the payload becomes problematic)
