# Fetch history & per-scan export — design

**Date:** 2026-06-25
**Status:** Approved design
**Branch:** `feature/fetch-history-export`

## Goal

Add a browsable list of **all** scans to the dashboard's History tab, clearly
marking which scan the dashboard is currently showing, and let the user download
any scan's report (the same Markdown `scan.py` produces) via a pre-rendered file.

## Why

Today only the latest scan is visible and only its report is generated. There's
no way to see what fetches have happened or pull a past scan's data. The primary
need is a **snapshot index** — see every scan and, crucially, which one the
leaderboard/charts reflect (so debug scans are obvious). Downloading the actual
per-scan data is the occasional case.

## Placement

The dashboard already has a **History tab** (`#tab-history`, currently a
rank-over-time Plotly chart via `renderHistory()`). Add the scan-history list
there, **above** the existing chart. The header's "Last scan: \<date\>" line
gains the scan id (e.g. `Last scan: #112 · 2026-06-25 12:24 UTC`) so the active
scan is identified on page load.

## The history list — all scans, active one marked

A server-rendered HTML table in the History tab, **newest first, one row per
scan** (every scan in the DB):

| (marker) | Scan | Run (UTC) | Sectors | Top sector | Report |
|---|---|---|---|---|---|
| **● Showing** | **#112** | 2026-06-25 12:24 | 22 | Technology (US) | download |
| | #111 | 2026-06-25 06:00 | 22 | Technology (US) | download |

- The row whose `scan_id` equals the dashboard's rendered scan — always the
  **latest / MAX scan_id** — gets a **"● Showing"** badge and a highlighted row.
  This is the explicit "what the leaderboard and charts reflect" marker; debug
  scans appear as their own rows so it's obvious which is live.
- Built from a new lightweight aggregate query `get_scan_index(conn)` (one row
  per scan: `scan_id`, `run_at`, sector count, top sector) — cheap even with
  hundreds of scans. Read-only, no schema change.
- The list is static HTML rendered at build time; `renderHistory()` (the Plotly
  chart) is unchanged.

## Per-scan export — pre-rendered Markdown, one file per scan

At build time, for **every** scan, regenerate its report via the existing
`src/report.py` builders and write `docs/reports/report_<scan_id>.md`. Each list
row's "download" links to its file.

- Reuses `build_ranked_table`, `build_movers`, `build_swedish_overlay` exactly —
  no formatting duplication.
- **Filenames are scan-id-based** (`report_<scan_id>.md`), NOT date-based:
  `write_report` names by date, which collides for multiple scans on the same day
  (debug runs). The build composes the three sections and writes the file
  directly (or via a `write_report` variant that takes an explicit path), keying
  on `scan_id`.
- **Deltas** for a historical scan are computed against its immediately-prior
  scan (by `scan_id`). The oldest scan has no prior → blank/zero deltas (the
  builders already tolerate missing `delta_*`/`emerging_flag`).
- Requires a full-history scores load (all scans) at build time — bounded by data
  size (hundreds of scans × 22 rows is small). Read-only, no schema change.

## .gitignore change (required)

`.gitignore` currently has an **unanchored** `reports/`, which also matches
`docs/reports/` and would prevent the published reports from being committed.
Anchor it to the repo root — change `reports/` to `/reports/` — so only
`scan.py`'s local on-demand `reports/` dir stays ignored while `docs/reports/`
is tracked and served by Pages.

## Tradeoff (accepted)

"All scans" means `docs/reports/` grows ~one tiny (~2 KB) Markdown file per scan
(~365/yr on the daily cron), all committed. Negligible on size for years, but
unbounded. A retention cap (keep last N) is a trivial future addition if it ever
feels noisy; out of scope here per the "list all" decision.

## Data flow

`build.py:main()` gains:
1. `get_scan_index(conn)` → rows for the history list.
2. A full-history scores load → for each scan, build deltas vs prior and write
   `docs/reports/report_<scan_id>.md`.
3. Pass the list rows + the active `scan_id` (MAX) into the template context.

The History-tab template renders the list above the existing chart. The header
line includes the active scan id.

## Error handling

- Report generation per scan is wrapped so one bad scan's report failure logs a
  warning and skips that file without aborting the whole build (the list still
  renders; that row's link may 404 until the next good build).
- Empty DB → empty list, no reports, build still succeeds (existing early-exit
  path is unaffected).

## Testing

- `get_scan_index`: a multi-scan fixture yields one row per scan with correct
  sector count and top sector, ordered newest-first.
- Per-scan report regeneration: a known 2–3 scan fixture produces the expected
  Markdown; deltas vs the prior scan are correct; the oldest scan's deltas are
  blank/zero.
- Filenames are `report_<scan_id>.md` (two same-day scans produce two distinct
  files — no collision).
- Active-scan marker: the badge attaches to exactly the MAX `scan_id` row.
- Render-context test (`test_dashboard_js`): the History tab contains the scan
  list and the report links; any new top-level template var is in the context.
- DB-touching tests reuse the hardened `TEST_DATABASE_URL`-gated fixture (skipped
  by default); list/report-generation logic is tested against in-memory
  DataFrames where possible.

## Out of scope

- Filtering or deleting debug scans (they appear as labelled rows).
- Report retention/pruning, search/pagination of the list.
- Any change to scoring, the leaderboard, or the DB schema.
- Changing what `scan.py` writes at scan time (it keeps its single dated report;
  the per-scan-id reports are produced by the dashboard build).
