# Thematic ETF momentum — Phase 2 design (leaderboard parity: deltas + trajectory)

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/themes-phase2` (stacked on `feature/themes-phase1` / PR #56)

## Problem

The Phase 1 Themes leaderboard is a static snapshot: rank, composite, and the
per-signal breakdown for the latest scan only. The sector board additionally shows
**rank-Δ** (movement vs the previous scan) and a **trajectory** badge (rank slope
over the last 5 scans) — the two most actionable "is this rotating in or out?"
signals. Themes should have the same.

## Goal (Phase 2 only)

Add **rank-Δ** and **trajectory** columns to the Themes leaderboard, matching the
sector board, by reusing the sector board's existing *build-time* derivations. No
new stored data, no `scan.py` change.

**Deferred to Phase 3:** RRG scatter, composite-history chart, and Trends sentiment
for themes. No change to the sector track.

## Key insight

The sector leaderboard computes `delta_rank`, `arrow`, `emerging`, and `trajectory`
**at dashboard-build time from multi-scan history** (`_build_leaderboard_rows` merges
the two latest scans; `_compute_rank_trajectories` fits a rank slope over the last 5)
— nothing delta-related is persisted. So Phase 2 needs only a **theme-history
loader**; the delta/trajectory logic is reused directly. No schema change, no new
columns, no `scan.py` change.

## Key decisions

1. **Theme history is aliased to `region="THEME"`, `gics_sector=<theme>` in SQL**, so
   the theme history DataFrame is drop-in compatible with `_compute_rank_trajectories`
   (which keys on `region + "|" + gics_sector`) and the delta-merge (on
   `["region", "gics_sector"]`) — maximal reuse, no per-theme reimplementation.
2. **Deltas/trajectory are build-time derivations**, computed from theme history each
   build — identical model to sectors. Nothing new is written at scan time.
3. **Reuse `_compute_rank_trajectories` unchanged**; extend
   `_build_theme_leaderboard_rows` to consume history + a trajectories dict.

## Components

### 1. `src/state.py` — `get_theme_scan_history`

```python
def get_theme_scan_history(conn, n_scans: int | None = None) -> pd.DataFrame:
    """Theme scores across scans, newest-last, aliased for reuse.

    Columns: scan_id, run_at, region ("THEME"), gics_sector (=theme), composite,
    rank, level_score, change_score, data_score. `n_scans=None` returns all scans;
    an int limits to the most recent N. Empty DataFrame if no theme rows exist.
    """
```

SQL mirrors `get_scan_history` but selects from `theme_scores` joined to `scans`,
with `'THEME' AS region, ts.theme AS gics_sector`. Ordering `scan_id ASC` (as
`get_scan_history` does) so "latest" = max scan_id and trajectory sees chronological
order.

### 2. `dashboard/build.py` — extend `_build_theme_leaderboard_rows`

New signature:

```python
def _build_theme_leaderboard_rows(
    history_df, signals_df, themes_cfg: dict, weights: dict, trajectories: dict,
) -> list[dict]:
```

- `history_df` = theme history (from `get_theme_scan_history`). Latest scan =
  `history_df["scan_id"].max()`. If empty → `[]`.
- **Delta computation** mirrors `_build_leaderboard_rows` (lines ~1150-1163): if ≥2
  scans, merge the previous scan's `rank`/`composite` on `["region","gics_sector"]`
  and compute `delta_rank = rank_prev - rank`, `delta_composite = composite - comp_prev`;
  else deltas are 0. Row fields: `delta_rank` (`"{d:+.1f}"` or `"—"`), `arrow`
  (▲/▼/""), `arrow_class` (up/down/""), `emerging` (`delta_rank>0 and delta_composite>0`).
- **Trajectory** per theme from `trajectories.get(f"THEME|{theme}", {"label":"→","state":"flat"})`
  → `trajectory_label`, `trajectory_state`.
- **Breakdown** unchanged from Phase 1: `_build_breakdown_html(key, score_row,
  row_signals, universe={}, weights=weights, sector_etfs=None, themes_cfg=themes_cfg)`,
  with `row_signals` filtered from the latest scan's `signals_df`.
- `score_row` for the breakdown = the theme's latest history row as a dict (has
  composite/level/change/data).

### 3. `dashboard/build.py` — `main()` wiring

- Load `theme_history = get_theme_scan_history(conn)` alongside the other theme
  loaders (before `conn.close()`).
- `theme_trajectories = _compute_rank_trajectories(theme_history)` — reused verbatim.
- Call `_build_theme_leaderboard_rows(theme_history, theme_signals_df, _themes_cfg,
  _weights, theme_trajectories)`.
- `theme_scores_df` (Phase 1's latest-only loader) is no longer needed for the rows —
  the latest slice comes from `theme_history`. Drop the `get_theme_scores_for_latest_scan`
  call from the build if it becomes unused (keep the function itself).

### 4. `themes.html.j2` — two new columns

Add **Rank Δ** and **Trend** header cells and body cells, mirroring the sector
table's markup (arrow + `traj-badge traj-<state>`), and an emerging badge on the
theme name. Bump the breakdown-row `colspan` 6 → 8 and the empty-state colspan.
Reuse existing i18n keys `col_rankdelta`, `col_trend`.

## Data flow

```
get_theme_scan_history(conn)  ──▶ theme_history (region=THEME, gics_sector=theme, per scan)
   ├─ _compute_rank_trajectories(theme_history)  ──▶ {"THEME|<name>": {label,state}}
   └─ _build_theme_leaderboard_rows(theme_history, theme_signals_df, cfg, weights, trajectories)
         latest slice → delta vs prev scan + trajectory + breakdown ──▶ themes.html rows
```

## Error handling / degradation

- No theme history (fresh DB / pre-Phase-1 scans) → `theme_history` empty →
  `_build_theme_leaderboard_rows` returns `[]` → themes page shows the empty state.
- Single scan only → delta "—", trajectory "→" (flat), same as the sector board's
  first-run behavior.
- A theme present this scan but absent last scan → `rank_prev` NaN → `delta_rank`
  fills to 0 → "—" (same `fillna(0)` behavior as sectors).

## Testing

- `get_theme_scan_history` shaping is exercised via the dashboard build; a focused
  unit test is optional (thin SQL wrapper like the Phase 1 loaders).
- `_build_theme_leaderboard_rows` with a **2-scan** theme history: a theme whose rank
  improved shows `arrow="▲"` and positive `delta_rank`; a worsened one shows `▼`; a
  passed `trajectories` entry renders as `trajectory_label`/`trajectory_state`.
- **Single-scan** history → `delta_rank == "—"`, `arrow == ""`.
- Empty history → `[]`.
- `themes.html.j2` renders the Rank-Δ and Trend columns for a sample row and still
  renders the empty state (colspan correct).
- **The Phase 1 `test_theme_dashboard.py` tests are updated** to the new
  `_build_theme_leaderboard_rows(history_df, …, trajectories)` signature (the change
  from a latest-only `scores_df` to a history frame is intentional).
- Full suite + scan smoke stay green.

## Out of scope / follow-ups

- Phase 3: RRG scatter, composite-history chart, Trends sentiment for themes.
- No `scan.py`, schema, or stored-column changes in Phase 2.
