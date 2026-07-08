# Thematic ETF momentum — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rank-Δ and trajectory columns to the Themes leaderboard, matching the sector board, by reusing the sector board's build-time delta/trajectory derivations over theme history.

**Architecture:** A new `get_theme_scan_history` loader returns theme scores across scans aliased to `region="THEME"`/`gics_sector=<theme>`, so `_compute_rank_trajectories` reuses verbatim. `_build_theme_leaderboard_rows` is refactored to take that history + a trajectories dict, computing delta-rank/arrow/emerging inline (mirroring `_build_leaderboard_rows`). No schema change, no `scan.py` change — deltas/trajectory are pure build-time derivations.

**Tech Stack:** Python 3.13, pandas, PyYAML, Jinja2, pytest.

## Global Constraints

- Phase 2 = leaderboard parity only: **deltas + trajectory**. RRG scatter, history chart, and Trends sentiment for themes are deferred to Phase 3.
- **No schema change, no `scan.py` change, no new stored columns.** Deltas/trajectory are computed at dashboard-build time from theme history, identical to sectors.
- Theme history is aliased `region="THEME"`, `gics_sector=<theme>` so `_compute_rank_trajectories(history_df)` and the delta-merge on `["region","gics_sector"]` reuse without modification.
- The Phase 1 `_build_theme_leaderboard_rows` signature changes from `(scores_df, signals_df, themes_cfg, weights)` to `(history_df, signals_df, themes_cfg, weights, trajectories)`; its Phase 1 tests are updated to match (intentional).
- No change to the sector track. Do **not** `git add docs/`. Use `python3` for pytest. Conventional commits, subject < 72 chars, footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Record the branch test baseline with `python3 -m pytest -q` before Task 1 (6 skips are the psycopg2-less DB modules).
- Spec: `design/specs/2026-07-07-themes-phase2-design.md`. Branch: `feature/themes-phase2` (based on merged `main` — Phase 1 is present).

## File Structure

- `dashboard/build.py` — refactor `_build_theme_leaderboard_rows` (history + trajectory + deltas); wire theme history + trajectories into `main()`.
- `src/state.py` — add `get_theme_scan_history`.
- `dashboard/templates/themes.html.j2` — add Rank-Δ + Trend columns + emerging badge; bump colspans.
- `tests/test_theme_dashboard.py` — update to the new signature; add delta/trajectory assertions.
- `BACKLOG.md` — Phase 2 Done entry.

---

### Task 1: Refactor `_build_theme_leaderboard_rows` for deltas + trajectory

**Files:**
- Modify: `dashboard/build.py` (`_build_theme_leaderboard_rows`)
- Test: `tests/test_theme_dashboard.py` (rewrite for the new signature)

**Interfaces:**
- Consumes: existing `_build_breakdown_html`, `_safe_float`.
- Produces: `_build_theme_leaderboard_rows(history_df, signals_df, themes_cfg: dict, weights: dict, trajectories: dict) -> list[dict]` — rows sorted by rank, each with `rank, theme, sector_id, composite, level_score, change_score, data_score, delta_rank, arrow, arrow_class, emerging, trajectory_label, trajectory_state, breakdown_html`. `history_df` uses `region`/`gics_sector` columns (gics_sector = theme name); latest scan = max scan_id; deltas vs the previous scan_id.

- [ ] **Step 1: Rewrite the tests for the new signature**

Replace `tests/test_theme_dashboard.py` with:

```python
import pandas as pd
from dashboard.build import _build_theme_leaderboard_rows


def _history_two_scans():
    # scan 1 (older): Space rank 1, Semis rank 2. scan 2 (newer): Semis rank 1, Space rank 2.
    return pd.DataFrame([
        {"scan_id": 1, "run_at": "2026-07-06", "region": "THEME", "gics_sector": "Space",
         "level_score": 1.0, "change_score": 0.5, "data_score": 0.8, "sentiment_score": None,
         "composite": 1.0, "rank": 1.0},
        {"scan_id": 1, "run_at": "2026-07-06", "region": "THEME", "gics_sector": "Semiconductors",
         "level_score": 0.4, "change_score": 0.3, "data_score": 0.5, "sentiment_score": None,
         "composite": 0.5, "rank": 2.0},
        {"scan_id": 2, "run_at": "2026-07-07", "region": "THEME", "gics_sector": "Space",
         "level_score": 0.9, "change_score": 0.2, "data_score": 0.6, "sentiment_score": None,
         "composite": 0.6, "rank": 2.0},
        {"scan_id": 2, "run_at": "2026-07-07", "region": "THEME", "gics_sector": "Semiconductors",
         "level_score": 1.5, "change_score": 0.9, "data_score": 1.2, "sentiment_score": None,
         "composite": 1.2, "rank": 1.0},
    ])


def _signals():
    return pd.DataFrame([
        {"theme": "Space", "signal_name": "rs_ratio", "raw_value": 101.0, "z_value": 0.4},
        {"theme": "Semiconductors", "signal_name": "rs_ratio", "raw_value": 104.0, "z_value": 1.1},
    ])


def test_theme_rows_deltas_and_trajectory():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Semiconductors": "SOXX"}}
    traj = {"THEME|Semiconductors": {"label": "↑↑", "state": "strong_up"},
            "THEME|Space": {"label": "↓", "state": "down"}}
    rows = _build_theme_leaderboard_rows(_history_two_scans(), _signals(), cfg, weights={}, trajectories=traj)
    assert [r["theme"] for r in rows] == ["Semiconductors", "Space"]   # sorted by latest rank
    semis = rows[0]
    assert semis["rank"] == 1
    assert semis["arrow"] == "▲"                       # 2 -> 1, improved
    assert semis["delta_rank"] == "+1.0"
    assert semis["emerging"] is True                    # rank up AND composite up (0.5 -> 1.2)
    assert semis["trajectory_label"] == "↑↑"
    assert semis["trajectory_state"] == "strong_up"
    space = rows[1]
    assert space["arrow"] == "▼"                         # 1 -> 2, dropped
    assert "SOXX" in semis["breakdown_html"]            # breakdown still rendered


def test_theme_rows_single_scan_no_delta():
    hist = _history_two_scans()
    hist = hist[hist["scan_id"] == 2]                    # only the latest scan
    rows = _build_theme_leaderboard_rows(hist, _signals(), {}, {}, {})
    assert all(r["delta_rank"] == "—" and r["arrow"] == "" for r in rows)
    assert all(r["trajectory_label"] == "→" for r in rows)   # default flat when no traj passed


def test_theme_rows_empty_history():
    assert _build_theme_leaderboard_rows(pd.DataFrame(), pd.DataFrame(), {}, {}, {}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_theme_dashboard.py -v`
Expected: FAIL — the current `_build_theme_leaderboard_rows(scores_df, signals_df, themes_cfg, weights)` takes 4 args, so calls with 5 args / `trajectories=` raise `TypeError`, and it reads `s["theme"]`/`s["rank"]` from a latest-only frame (no delta/trajectory keys in output).

- [ ] **Step 3: Rewrite the function**

Replace `_build_theme_leaderboard_rows` in `dashboard/build.py` with:

```python
def _build_theme_leaderboard_rows(history_df, signals_df, themes_cfg: dict, weights: dict, trajectories: dict) -> list[dict]:
    """Themes leaderboard rows with build-time deltas + trajectory, sorted by rank.

    history_df: theme scores across scans (region="THEME", gics_sector=<theme>),
    from get_theme_scan_history. Deltas are computed vs the previous scan_id;
    trajectories is keyed "THEME|<theme>" (from _compute_rank_trajectories).
    """
    import pandas as pd

    if history_df is None or history_df.empty:
        return []

    def _fv(v):
        f = _safe_float(v)
        return f"{f:.3f}" if f is not None else "—"

    latest_id = history_df["scan_id"].max()
    latest = history_df[history_df["scan_id"] == latest_id].copy()

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) >= 2:
        prev = history_df[history_df["scan_id"] == scan_ids[-2]][
            ["region", "gics_sector", "rank", "composite"]
        ].rename(columns={"rank": "rank_prev", "composite": "comp_prev"})
        latest = latest.merge(prev, on=["region", "gics_sector"], how="left")
        latest["delta_rank"] = (latest["rank_prev"] - latest["rank"]).fillna(0)
        latest["delta_composite"] = (latest["composite"] - latest["comp_prev"]).fillna(0)
    else:
        latest["delta_rank"] = 0.0
        latest["delta_composite"] = 0.0

    rows = []
    for _, s in latest.sort_values("rank").iterrows():
        theme = s["gics_sector"]
        key = f"THEME|{theme}"
        row_signals = (
            signals_df[signals_df["theme"] == theme].to_dict("records")
            if signals_df is not None and not signals_df.empty else []
        )
        breakdown = _build_breakdown_html(
            key, s.to_dict(), row_signals, universe={}, weights=weights,
            sector_etfs=None, themes_cfg=themes_cfg,
        )
        delta = _safe_float(s.get("delta_rank", 0)) or 0.0
        delta_comp = _safe_float(s.get("delta_composite", 0)) or 0.0
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        rank = _safe_float(s.get("rank"))
        rows.append({
            "rank": int(rank) if rank is not None else "—",
            "theme": theme,
            "sector_id": key.replace("|", "-").replace(" ", "_"),
            "composite": _fv(s["composite"]),
            "level_score": _fv(s["level_score"]),
            "change_score": _fv(s["change_score"]),
            "data_score": _fv(s["data_score"]),
            "delta_rank": f"{delta:+.1f}" if delta != 0 else "—",
            "arrow": "▲" if delta > 0 else ("▼" if delta < 0 else ""),
            "arrow_class": "up" if delta > 0 else ("down" if delta < 0 else ""),
            "emerging": delta > 0 and delta_comp > 0,
            "trajectory_label": traj["label"],
            "trajectory_state": traj["state"],
            "breakdown_html": breakdown,
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_theme_dashboard.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/build.py tests/test_theme_dashboard.py
git commit -m "feat: theme leaderboard deltas and trajectory (build-time)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `get_theme_scan_history` loader + `main()` wiring

**Files:**
- Modify: `src/state.py` (add `get_theme_scan_history`)
- Modify: `dashboard/build.py` (`main()` — load history, compute trajectories, call the refactored builder; update the state import)

**Interfaces:**
- Consumes: `_compute_rank_trajectories` (existing); `_build_theme_leaderboard_rows` (Task 1); `get_theme_signals_for_latest_scan` (Phase 1).
- Produces: `get_theme_scan_history(conn, n_scans: int | None = None) -> pd.DataFrame` — columns `scan_id, run_at, region ("THEME"), gics_sector (=theme), level_score, change_score, data_score, sentiment_score, composite, rank`, ordered `run_at ASC, theme`. `n_scans=None` → all scans.

- [ ] **Step 1: Add the loader**

In `src/state.py`, after `get_theme_signals_for_latest_scan`, add:

```python
def get_theme_scan_history(
    conn: psycopg2.extensions.connection,
    n_scans: int | None = None,
) -> pd.DataFrame:
    """Theme scores across scans, aliased region="THEME"/gics_sector=theme for reuse.

    Columns: scan_id, run_at, region, gics_sector, level_score, change_score,
    data_score, sentiment_score, composite, rank. Ordered by run_at ASC, theme.
    n_scans=None returns all scans. Empty DataFrame if no theme rows exist.
    """
    base = """
        SELECT sc.scan_id, sc.run_at, 'THEME' AS region, ts.theme AS gics_sector,
               ts.level_score, ts.change_score, ts.data_score, ts.sentiment_score,
               ts.composite, ts.rank
        FROM theme_scores ts
        JOIN scans sc ON sc.scan_id = ts.scan_id
        {scan_filter}
        ORDER BY sc.run_at ASC, ts.theme
    """
    if n_scans is None:
        return pd.read_sql_query(base.format(scan_filter=""), conn)
    return pd.read_sql_query(
        base.format(
            scan_filter="WHERE sc.scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)"
        ),
        conn,
        params=(n_scans,),
    )
```

- [ ] **Step 2: Wire it into `main()`**

In `dashboard/build.py`'s `main()`:

(a) Extend the theme import in the `from src.state import (...)` block to add `get_theme_scan_history`:

```python
        get_theme_scores_for_latest_scan, get_theme_signals_for_latest_scan,
        get_theme_scan_history,
```

(b) Where the theme loaders are called (near `theme_signals_df = get_theme_signals_for_latest_scan(conn)`), add the history load and **remove the now-unused** `theme_scores_df = get_theme_scores_for_latest_scan(conn)` line:

```python
    theme_signals_df = get_theme_signals_for_latest_scan(conn)
    theme_history_df = get_theme_scan_history(conn)
```

(c) Where the theme rows are built (currently `theme_rows = _build_theme_leaderboard_rows(theme_scores_df, theme_signals_df, _themes_cfg, _weights)`), replace with:

```python
    theme_trajectories = _compute_rank_trajectories(theme_history_df)
    theme_rows = _build_theme_leaderboard_rows(
        theme_history_df, theme_signals_df, _themes_cfg, _weights, theme_trajectories,
    )
```

(`get_theme_scores_for_latest_scan` stays defined in `src/state.py` for potential reuse; it's just no longer called here.)

- [ ] **Step 3: Verify build.py + state.py import and parse**

Run: `python3 -c "import ast; ast.parse(open('dashboard/build.py').read()); ast.parse(open('src/state.py').read()); print('parse ok')"`
Expected: `parse ok`

- [ ] **Step 4: Run the theme + dashboard-adjacent suites**

Run: `python3 -m pytest tests/test_theme_dashboard.py tests/test_theme_pipeline.py -v`
Expected: PASS (the row-builder tests from Task 1 still pass; wiring is import-only glue).

- [ ] **Step 5: Commit**

```bash
git add src/state.py dashboard/build.py
git commit -m "feat: load theme history and wire trajectory into the themes page" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Rank-Δ + Trend columns in `themes.html.j2`

**Files:**
- Modify: `dashboard/templates/themes.html.j2`

**Interfaces:** none (template only). Reuses existing i18n keys `col_rankdelta`, `col_trend` and CSS classes `arrow`/`traj-badge`/`emerging-badge`.

- [ ] **Step 1: Add the two header cells**

In `themes.html.j2`, in the `<thead>` row, after the Data `<th>`, add:

```html
          <th data-i18n="col_rankdelta">Rank Δ</th>
          <th data-i18n="col_trend">Trend</th>
```

- [ ] **Step 2: Add the two body cells + emerging badge**

In the `{% for row in theme_rows %}` body row, add the emerging badge to the theme-name cell and two new cells after the Data cell. The data row becomes:

```html
        <tr class="leaderboard-row" data-sector-id="{{ row.sector_id }}" onclick="toggleBreakdown('{{ row.sector_id }}')">
          <td class="rank-cell">
            <span class="rank-badge{% if row.rank is number and row.rank <= 3 %} top3{% endif %}">{{ row.rank }}</span>
            <span class="chevron" id="chev-{{ row.sector_id }}">▶</span>
          </td>
          <td>{{ row.theme }}{% if row.emerging %}<span class="emerging-badge">⬆ Emerging</span>{% endif %}</td>
          <td class="composite-cell">{{ row.composite }}</td>
          <td>{{ row.level_score }}</td>
          <td>{{ row.change_score }}</td>
          <td>{{ row.data_score }}</td>
          <td class="delta-cell">
            {% if row.arrow %}<span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {% endif %}{{ row.delta_rank }}
          </td>
          <td>
            <span class="traj-badge traj-{{ row.trajectory_state }}">{{ row.trajectory_label }}</span>
          </td>
        </tr>
```

- [ ] **Step 3: Bump the colspans 6 → 8**

The breakdown row and the empty-state row both use `colspan="6"` — change both to `colspan="8"`:

```html
        <tr class="breakdown-row" id="bd-{{ row.sector_id }}">
          <td colspan="8">{{ row.breakdown_html | safe }}</td>
        </tr>
```
and
```html
        <tr><td colspan="8" style="text-align:center;padding:24px;color:var(--fg4)" data-i18n="themes_empty">No theme data yet — run a scan.</td></tr>
```

- [ ] **Step 4: Verify the template renders with the new columns**

Run:
```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('dashboard/templates'))
rows=[{'rank':1,'theme':'Semiconductors','sector_id':'THEME-Semiconductors','composite':'1.200','level_score':'1.500','change_score':'0.900','data_score':'1.200','delta_rank':'+1.0','arrow':'▲','arrow_class':'up','emerging':True,'trajectory_label':'↑↑','trajectory_state':'strong_up','breakdown_html':'<div>SOXX</div>'}]
html=env.get_template('themes.html.j2').render(scan_date='x', active_scan_id=1, theme_rows=rows, plotly_bundle='x')
assert 'col_rankdelta' in html and 'traj-strong_up' in html and 'Emerging' in html
empty=env.get_template('themes.html.j2').render(scan_date='x', active_scan_id=1, theme_rows=[], plotly_bundle='x')
assert 'colspan=\"8\"' in empty and 'No theme data yet' in empty
print('themes.html.j2 renders with delta + trend columns')
"
```
Expected: `themes.html.j2 renders with delta + trend columns`

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/themes.html.j2
git commit -m "feat: rank-delta and trend columns on the themes page" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Backlog hygiene

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Add a Phase 2 Done entry** at the top of `## Done` (the "Thematic / genre ETF momentum" queued item stays — Phase 3 remains):

```markdown
- ~~Thematic ETF momentum — Phase 2 (leaderboard deltas + trajectory)~~ — the Themes
  leaderboard now shows rank-Δ (vs the previous scan) and a trajectory badge (rank
  slope over the last 5 scans), matching the sector board. Computed at dashboard-build
  time from a new `get_theme_scan_history` loader (aliased region="THEME" so
  `_compute_rank_trajectories` and the delta-merge reuse verbatim) — no schema or
  `scan.py` change. Phase 3 (RRG scatter, composite-history chart, Trends sentiment
  for themes) remains queued above. *(2026-07-08)*
```

- [ ] **Step 2: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark thematic ETF momentum Phase 2 done" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Full suite green:** `python3 -m pytest -q` → branch baseline (no regressions; theme dashboard tests updated, not net-new count-wise).
- [ ] **No `docs/` staged:** `git status --porcelain docs/` → empty.
- [ ] **Diff source-only:** `git fetch origin -q && git diff --stat origin/main...HEAD` touches only `src/`, `dashboard/`, `tests/`, `BACKLOG.md`, `design/` (compare against `origin/main`, not a possibly-stale local `main`).
- [ ] Final whole-branch review, address findings, then `git push -u origin feature/themes-phase2` and open a PR with `gh pr create` (per CLAUDE.md — Claude opens the PR; Jonas merges). **Do not merge.** (Phase 2 targets `main` directly — Phase 1 / #56 is already merged.)
