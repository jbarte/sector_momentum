# Sector View Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a leaderboard toggle that switches between the current region-split view (22 `region|sector` rows) and a composite view (11 GICS-sector rows combining US+EU by simple mean), recomputing rank/ΔRank/trajectory/emerging client-side.

**Architecture:** Mirror the existing sentiment toggle: server-renders both row sets into one `<tbody>`, a CSS class on the table shows one set at a time, and `rescore.js` recomputes scores client-side. The composite view merges the two regions' per-scan `data_score`/`sentiment_score` by mean (`mergeComposite`) and feeds the result to the existing `rescore()`. Composite rows reuse the existing server-side builders via a sentinel region `ALL`.

**Tech Stack:** Python 3.11 (pandas, scipy, Jinja2), vanilla JS (no framework), pytest, Node (parity tests only).

## Global Constraints

- Composite key convention is `ALL|<sector>` everywhere — server rows (`data-sector-key`), `sector_id`, and `rescore.js` `mergeComposite` output keys must all use it identically.
- Combine rule is **simple mean** of the two regions' `data_score` and `sentiment_score` per scan. No weighting, no global re-pooling. No changes to `scan.py`, `src/scoring.py` scoring math, or the DB.
- Rank semantics must match `src/scoring.py:rank_sectors` → `scipy.stats.rankdata(-composite, method="average")` (descending, average tie-break). `rescore.js` already mirrors this via `rankAverage`.
- Default view is region-split (`"split"`); persisted in `localStorage` under key `sectorView`.
- The toggle affects the **leaderboard only** — not the RRG, sentiment scatter, or drilldown tabs.
- Composite breakdown shows a one-line header + the two existing per-region breakdown panels (reused HTML), per the approved spec.
- 11 US and 11 EU sectors are fully matched (verified); every composite entry has both regions. Guard `mergeComposite` to only emit a sector when both `US|<s>` and `EU|<s>` are present.
- NaN scores are already coerced to `0.0` by `_build_rescore_data`; `mergeComposite` averages those zeros (neutral, matches existing behavior).
- Use `.venv/bin/python` and `.venv/bin/pytest`. **Never run the full suite without first confirming `tests/test_state_smoke.py` SKIPs** (it wipes production Supabase otherwise) — run only the named test files in each task.

---

### Task 1: `mergeComposite` in rescore.js + parity test

**Files:**
- Modify: `dashboard/assets/rescore.js` (add `mergeComposite`, export it)
- Test: `tests/test_rescore_parity.py` (add composite-mode parity case)

**Interfaces:**
- Consumes: existing `RESCORE_DATA` shape `{scans:[{scan_id,run_at}], sectors:[key], data:{key:[float]}, sentiment:{key:[float]}}` where keys are `"US|<s>"` / `"EU|<s>"`.
- Produces: `Rescore.mergeComposite(data)` → a new object of the **same shape** whose `sectors` are `"ALL|<s>"` and whose `data`/`sentiment` arrays are the per-scan mean of the two regions. Feeds directly into the existing `Rescore.rescore(merged, W)`.

- [ ] **Step 1: Write the failing parity test**

Add to `tests/test_rescore_parity.py` (after the existing tests). The Python reference averages the two regions per scan, then reuses the existing `_py_reference` for ranking:

```python
def _make_split_data(n_scans=4):
    """Two regions × 3 sectors, deterministic values."""
    sectors = ["US|Technology", "EU|Technology",
               "US|Energy", "EU|Energy",
               "US|Health Care", "EU|Health Care"]
    scans = [{"scan_id": i + 1, "run_at": f"2026-06-0{i+1}"} for i in range(n_scans)]
    data, sentiment = {}, {}
    for j, s in enumerate(sectors):
        data[s] = [round(0.5 * j + 0.1 * i, 3) for i in range(n_scans)]
        sentiment[s] = [round(0.2 * j - 0.05 * i, 3) for i in range(n_scans)]
    return {"scans": scans, "sectors": sectors, "data": data, "sentiment": sentiment}


def _py_merge_composite(data):
    sectors = data["sectors"]
    bare = sorted({k.split("|", 1)[1] for k in sectors})
    n = len(data["scans"])
    out = {"scans": data["scans"], "sectors": [f"ALL|{b}" for b in bare],
           "data": {}, "sentiment": {}}
    for b in bare:
        us, eu = f"US|{b}", f"EU|{b}"
        out["data"][f"ALL|{b}"] = [(data["data"][us][i] + data["data"][eu][i]) / 2 for i in range(n)]
        out["sentiment"][f"ALL|{b}"] = [(data["sentiment"][us][i] + data["sentiment"][eu][i]) / 2 for i in range(n)]
    return out


@pytest.mark.parametrize("W", [0.0, 0.3, 1.0])
def test_merge_composite_parity(tmp_path, W):
    data = _make_split_data()
    # JS: mergeComposite then rescore
    script = f"""
      const R = require({json.dumps(str(_RESCORE_JS))});
      const data = {json.dumps(data)};
      const merged = R.mergeComposite(data);
      console.log(JSON.stringify(R.rescore(merged, {W})));
    """
    js_out = json.loads(subprocess.run(["node", "-e", script],
                                       capture_output=True, text=True, check=True).stdout)
    py_merged = _py_merge_composite(data)
    py_out = _py_reference(py_merged, W)
    assert set(js_out.keys()) == set(py_out.keys())
    for k in py_out:
        assert js_out[k]["rank"] == pytest.approx(py_out[k]["rank"])
        assert js_out[k]["composite"] == pytest.approx(py_out[k]["composite"], abs=1e-9)
        assert js_out[k]["trajectory_label"] == py_out[k]["trajectory_label"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_rescore_parity.py::test_merge_composite_parity -v`
Expected: FAIL — `node` errors with `R.mergeComposite is not a function`.

- [ ] **Step 3: Implement `mergeComposite` in rescore.js**

In `dashboard/assets/rescore.js`, add this function before the `var api = {...}` line:

```javascript
  // Merge a split-region dataset into composite (GICS-only) entries keyed
  // "ALL|<sector>". Each per-scan value is the mean of the US and EU series.
  // Only sectors present in BOTH regions are emitted.
  function mergeComposite(data) {
    var bare = {};
    data.sectors.forEach(function (key) {
      var parts = key.split("|");
      var region = parts[0], sector = parts.slice(1).join("|");
      if (!bare[sector]) { bare[sector] = {}; }
      bare[sector][region] = key;
    });
    var nScans = data.scans.length;
    var sectors = [];
    var outData = {}, outSent = {};
    Object.keys(bare).sort().forEach(function (sector) {
      var us = bare[sector].US, eu = bare[sector].EU;
      if (!us || !eu) { return; } // require both regions
      var ck = "ALL|" + sector;
      sectors.push(ck);
      var d = [], s = [];
      for (var i = 0; i < nScans; i++) {
        d.push((data.data[us][i] + data.data[eu][i]) / 2);
        s.push((data.sentiment[us][i] + data.sentiment[eu][i]) / 2);
      }
      outData[ck] = d; outSent[ck] = s;
    });
    return { scans: data.scans, sectors: sectors, data: outData, sentiment: outSent };
  }
```

Then add it to the exports object:

```javascript
  var api = { rankAverage: rankAverage, olsSlope: olsSlope,
              trajectoryLabel: trajectoryLabel, rescore: rescore,
              mergeComposite: mergeComposite };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_rescore_parity.py -v`
Expected: PASS (existing parity tests + the 3 new parametrized composite cases).

- [ ] **Step 5: Commit**

```bash
git add dashboard/assets/rescore.js tests/test_rescore_parity.py
git commit -m "feat: add mergeComposite to rescore.js for composite leaderboard view"
```

---

### Task 2: Composite history aggregation in build.py + unit test

**Files:**
- Modify: `dashboard/build.py` (add `_build_composite_history`)
- Test: `tests/test_dashboard_composite.py` (new)

**Interfaces:**
- Consumes: `history_df` with columns `scan_id, run_at, region, gics_sector, composite, data_score, level_score, change_score, sentiment_score, rank` (the shape returned by `get_scan_history`).
- Produces: `_build_composite_history(history_df) -> pd.DataFrame` with the **same columns**, but one row per `(scan_id, gics_sector)` with `region == "ALL"`, score columns equal to the cross-region mean, and `rank` recomputed per scan over the averaged `composite` via `scipy.stats.rankdata(-composite, method="average")`. Downstream this feeds the existing `_build_leaderboard_rows` and `_compute_rank_trajectories` unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_composite.py`:

```python
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.build import _build_composite_history


def _df():
    # 1 scan, 2 sectors × 2 regions. Composite means: Tech=(0.8+0.2)/2=0.5, Energy=(0.6+0.6)/2=0.6
    rows = [
        dict(scan_id=1, run_at="2026-06-01", region="US", gics_sector="Technology",
             composite=0.8, data_score=0.8, level_score=0.7, change_score=0.9, sentiment_score=0.0, rank=1.0),
        dict(scan_id=1, run_at="2026-06-01", region="EU", gics_sector="Technology",
             composite=0.2, data_score=0.2, level_score=0.1, change_score=0.3, sentiment_score=0.0, rank=2.0),
        dict(scan_id=1, run_at="2026-06-01", region="US", gics_sector="Energy",
             composite=0.6, data_score=0.6, level_score=0.5, change_score=0.7, sentiment_score=0.0, rank=2.0),
        dict(scan_id=1, run_at="2026-06-01", region="EU", gics_sector="Energy",
             composite=0.6, data_score=0.6, level_score=0.6, change_score=0.6, sentiment_score=0.0, rank=1.0),
    ]
    return pd.DataFrame(rows)


def test_composite_history_means_and_rank():
    out = _build_composite_history(_df())
    assert len(out) == 2                       # 2 sectors, 1 scan
    assert set(out["region"]) == {"ALL"}
    tech = out[out["gics_sector"] == "Technology"].iloc[0]
    energy = out[out["gics_sector"] == "Energy"].iloc[0]
    assert tech["composite"] == pytest.approx(0.5)
    assert tech["data_score"] == pytest.approx(0.5)
    assert energy["composite"] == pytest.approx(0.6)
    # Energy (0.6) outranks Technology (0.5): Energy rank 1, Tech rank 2
    assert energy["rank"] == pytest.approx(1.0)
    assert tech["rank"] == pytest.approx(2.0)


def test_composite_history_empty():
    out = _build_composite_history(pd.DataFrame())
    assert out.empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard_composite.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_composite_history'`.

- [ ] **Step 3: Implement `_build_composite_history`**

In `dashboard/build.py`, add after `_compute_rank_trajectories` (around line 201):

```python
def _build_composite_history(history_df):
    """Collapse region-split history into composite (GICS-only) rows.

    One row per (scan_id, gics_sector) with region="ALL", score columns set to
    the cross-region mean, and rank recomputed per scan over the averaged
    composite (mirrors src/scoring.py:rank_sectors).
    """
    import pandas as pd
    from scipy.stats import rankdata

    if history_df.empty:
        return history_df.copy()

    score_cols = ["composite", "data_score", "level_score", "change_score", "sentiment_score"]
    present = [c for c in score_cols if c in history_df.columns]

    grouped = (
        history_df.groupby(["scan_id", "gics_sector"], as_index=False)
        .agg({**{c: "mean" for c in present}, "run_at": "first"})
    )
    grouped["region"] = "ALL"

    # Recompute rank within each scan over the averaged composite.
    parts = []
    for sid, g in grouped.groupby("scan_id"):
        g = g.copy()
        g["rank"] = rankdata(-g["composite"].values, method="average")
        parts.append(g)
    return pd.concat(parts, ignore_index=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dashboard_composite.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/build.py tests/test_dashboard_composite.py
git commit -m "feat: composite history aggregation for sector view toggle"
```

---

### Task 3: Render composite rows + breakdown in build.py

**Files:**
- Modify: `dashboard/build.py` (`main()` — build/enrich composite rows; add to render context)
- Test: `tests/test_dashboard_composite.py` (add a build-context test)

**Interfaces:**
- Consumes: `_build_composite_history` (Task 2), existing `_build_leaderboard_rows`, `_compute_rank_trajectories`, `_build_breakdown_html`, and the per-region `breakdown_html` strings produced in the existing split-row enrichment loop.
- Produces: a `composite_rows` list (same row-dict shape as `leaderboard_rows`, with `key="ALL|<sector>"`, `sector_id="ALL-<sector_>"`, `breakdown_html` = composite header + the two regional panels) passed to the template context as `composite_rows`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dashboard_composite.py`:

```python
def test_build_composite_rows_helper():
    """_build_composite_rows returns 11-style rows keyed ALL|<sector> with a
    breakdown that embeds both regional panels."""
    from dashboard.build import _build_composite_rows
    df = _df()
    split_breakdowns = {
        "US|Technology": "<div>US-TECH-PANEL</div>",
        "EU|Technology": "<div>EU-TECH-PANEL</div>",
        "US|Energy": "<div>US-EN-PANEL</div>",
        "EU|Energy": "<div>EU-EN-PANEL</div>",
    }
    rows = _build_composite_rows(df, split_breakdowns)
    assert len(rows) == 2
    tech = next(r for r in rows if r["sector"] == "Technology")
    assert tech["key"] == "ALL|Technology"
    assert tech["sector_id"] == "ALL-Technology"
    # breakdown embeds BOTH regional panels
    assert "US-TECH-PANEL" in tech["breakdown_html"]
    assert "EU-TECH-PANEL" in tech["breakdown_html"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard_composite.py::test_build_composite_rows_helper -v`
Expected: FAIL — `ImportError: cannot import name '_build_composite_rows'`.

- [ ] **Step 3: Implement `_build_composite_rows`**

In `dashboard/build.py`, add after `_build_composite_history`:

```python
def _build_composite_rows(history_df, split_breakdowns: dict) -> list[dict]:
    """Build enriched composite leaderboard rows.

    `split_breakdowns` maps "US|<sector>"/"EU|<sector>" → that region's
    pre-rendered breakdown HTML (reused verbatim inside the composite panel).
    """
    import html as _html

    composite_df = _build_composite_history(history_df)
    rows, _ = _build_leaderboard_rows(composite_df)
    trajectories = _compute_rank_trajectories(composite_df)

    for row in rows:
        sector = row["sector"]
        key = f"ALL|{sector}"
        row["key"] = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        row["trajectory_label"] = traj["label"]
        row["trajectory_state"] = traj["state"]

        us_panel = split_breakdowns.get(f"US|{sector}", "")
        eu_panel = split_breakdowns.get(f"EU|{sector}", "")
        header = (
            f'<div class="composite-bd-header" data-sector="{_html.escape(sector)}">'
            f'<span class="cbh-label">Composite of</span> '
            f'<span class="cbh-us">US</span> + <span class="cbh-eu">EU</span> '
            f'<span class="cbh-note">(mean)</span>'
            f'</div>'
        )
        row["breakdown_html"] = (
            f'<div class="composite-breakdown">{header}'
            f'<div class="composite-bd-panels">'
            f'<div class="composite-bd-region"><div class="cbh-region-tag">US</div>{us_panel}</div>'
            f'<div class="composite-bd-region"><div class="cbh-region-tag">EU</div>{eu_panel}</div>'
            f'</div></div>'
        )
    return rows
```

- [ ] **Step 4: Wire it into `main()` and the render context**

In `dashboard/build.py:main()`, the existing split-enrichment loop (lines ~1033-1056) builds `row["breakdown_html"]` per split row. Capture those into a dict, then build composite rows. Replace the loop's tail and the `_render(... context=dict(...))` call as follows.

First, just before the split-enrichment `for row in leaderboard_rows:` loop, add:

```python
    split_breakdowns = {}
```

Inside that loop, after the line `row["breakdown_html"] = _build_breakdown_html(...)`, add:

```python
        split_breakdowns[key] = row["breakdown_html"]
```

After the split loop completes (before the `# 4. Copy plotly` comment), add:

```python
    logger.info("Building composite leaderboard rows …")
    composite_rows = _build_composite_rows(history_df, split_breakdowns)
```

Then in the `_render(context=dict(...))` call, add one line alongside `leaderboard_rows=leaderboard_rows,`:

```python
            composite_rows=composite_rows,
```

- [ ] **Step 5: Add a context-presence test**

Add to `tests/test_dashboard_composite.py`:

```python
def test_main_passes_composite_rows(monkeypatch):
    """build.py's render context includes composite_rows (guards the wiring)."""
    import re
    text = (Path(__file__).parent.parent / "dashboard" / "build.py").read_text()
    assert "composite_rows=composite_rows" in text
    assert "_build_composite_rows(history_df, split_breakdowns)" in text
```

- [ ] **Step 6: Run tests + a real build**

Run: `.venv/bin/pytest tests/test_dashboard_composite.py -v`
Expected: PASS (all composite tests).

Run: `.venv/bin/python dashboard/build.py`
Expected: prints `Dashboard built: …/docs/index.html`, no traceback.

- [ ] **Step 7: Commit**

```bash
git add dashboard/build.py tests/test_dashboard_composite.py
git commit -m "feat: render composite leaderboard rows with dual-region breakdown"
```

---

### Task 4: Template — both row sets, view toggle control, CSS, view-aware JS

**Files:**
- Modify: `dashboard/templates/index.html.j2` (control, composite rows, CSS, JS)
- Test: `tests/test_dashboard_js.py` (assert built HTML contains both row sets and the control)

**Interfaces:**
- Consumes: `composite_rows` from the render context (Task 3); `Rescore.mergeComposite` (Task 1); existing `RESCORE_DATA`, `Rescore.rescore`, `applyRanking`, `sortTable`, `toggleBreakdown`.
- Produces: a working `#sector-view-toggle` control persisted to `localStorage.sectorView`; `#leaderboard-table[data-view]` drives row visibility; `applyRanking()` becomes view-aware.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dashboard_js.py` (these build the real template against a tiny in-memory history and assert on the HTML; follow the file's existing `_render`/`_build_leaderboard_rows` import style):

```python
def test_built_html_has_both_row_sets_and_toggle(tmp_path):
    """The rendered leaderboard contains split rows, composite rows, and the
    view-toggle control."""
    import json as _json
    from dashboard.build import _render, _build_leaderboard_rows, _build_composite_rows

    # Minimal 1-scan, 1-sector × 2-region history
    import pandas as pd
    rows_df = pd.DataFrame([
        dict(scan_id=1, run_at="2026-06-01 00:00", region="US", gics_sector="Technology",
             composite=0.8, data_score=0.8, level_score=0.7, change_score=0.9,
             sentiment_score=0.0, rank=1.0),
        dict(scan_id=1, run_at="2026-06-01 00:00", region="EU", gics_sector="Technology",
             composite=0.2, data_score=0.2, level_score=0.1, change_score=0.3,
             sentiment_score=0.0, rank=2.0),
    ])
    lb_rows, scan_date = _build_leaderboard_rows(rows_df)
    for r in lb_rows:
        r["key"] = f"{r['region']}|{r['sector']}"
        r["sector_id"] = r["key"].replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "→"; r["trajectory_state"] = "flat"
        r["breakdown_html"] = f"<div>PANEL {r['key']}</div>"
    split_breakdowns = {r["key"]: r["breakdown_html"] for r in lb_rows}
    comp_rows = _build_composite_rows(rows_df, split_breakdowns)

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows, composite_rows=comp_rows,
        rrg_data_json="{}", drilldown_data="{}", sector_keys=[], movers_json="{}",
        history_json="{}", sentiment_scatter_json="{}",
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
    ))
    html = out.read_text()
    assert 'data-view="split"' in html
    assert 'data-view="composite"' in html
    assert 'id="sector-view-toggle"' in html
    assert 'data-sector-key="ALL|Technology"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard_js.py::test_built_html_has_both_row_sets_and_toggle -v`
Expected: FAIL — assertions miss (`data-view`, `sector-view-toggle`, composite key not in template yet).

- [ ] **Step 3: Add the view-toggle control**

In `dashboard/templates/index.html.j2`, replace the `#sentiment-control` block (lines 572-579) with the version that adds the view toggle:

```html
  <div id="sentiment-control" class="sentiment-control">
    <span class="sw-view">
      Sector view:
      <select id="sector-view-toggle">
        <option value="split">Region-split</option>
        <option value="composite">Composite (US+EU)</option>
      </select>
    </span>
    <label>
      <input type="checkbox" id="sentiment-toggle"> Include sentiment in ranking
    </label>
    <span class="sw-weight">
      Weight: <input type="number" id="sentiment-weight" min="0" max="100" step="1" value="30" disabled>%
    </span>
  </div>
```

- [ ] **Step 4: Tag split rows and add composite rows**

In the same file, update the `<tbody>` loop (lines 597-622). Add `data-view="split"` to both the split leaderboard row and its breakdown row, then append a second loop for composite rows. Replace lines 597-622 with:

```html
        {% for row in leaderboard_rows %}
        <tr class="leaderboard-row" data-view="split" data-sector-key="{{ row.key }}" data-sector-id="{{ row.sector_id }}" onclick="toggleBreakdown('{{ row.sector_id }}')">
          <td class="rank-cell">
            <span class="rank-badge{% if row.rank <= 3 %} top3{% endif %}">{{ row.rank }}</span>
            <span class="chevron" id="chev-{{ row.sector_id }}">▶</span>
          </td>
          <td>{{ row.sector }}{% if row.emerging %}<span class="emerging-badge">⬆ Emerging</span>{% endif %}</td>
          <td><span class="tag-region">{{ row.region }}</span></td>
          <td class="composite-cell">{{ row.composite }}</td>
          <td>{{ row.level_score }}</td>
          <td>{{ row.change_score }}</td>
          <td>{{ row.data_score }}</td>
          <td class="sentiment-cell">{{ row.sentiment_score }}</td>
          <td class="delta-cell">
            {% if row.arrow %}<span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {% endif %}{{ row.delta_rank }}
          </td>
          <td>
            <span class="traj-badge traj-{{ row.trajectory_state }}">{{ row.trajectory_label }}</span>
          </td>
        </tr>
        <tr class="breakdown-row" data-view="split" id="bd-{{ row.sector_id }}">
          <td colspan="10">{{ row.breakdown_html | safe }}</td>
        </tr>
        {% else %}
        <tr><td colspan="10" style="text-align:center;padding:24px;color:var(--fg4)">No data available yet.</td></tr>
        {% endfor %}
        {% for row in composite_rows %}
        <tr class="leaderboard-row" data-view="composite" data-sector-key="{{ row.key }}" data-sector-id="{{ row.sector_id }}" onclick="toggleBreakdown('{{ row.sector_id }}')">
          <td class="rank-cell">
            <span class="rank-badge{% if row.rank <= 3 %} top3{% endif %}">{{ row.rank }}</span>
            <span class="chevron" id="chev-{{ row.sector_id }}">▶</span>
          </td>
          <td>{{ row.sector }}{% if row.emerging %}<span class="emerging-badge">⬆ Emerging</span>{% endif %}</td>
          <td><span class="tag-region">Global</span></td>
          <td class="composite-cell">{{ row.composite }}</td>
          <td>{{ row.level_score }}</td>
          <td>{{ row.change_score }}</td>
          <td>{{ row.data_score }}</td>
          <td class="sentiment-cell">{{ row.sentiment_score }}</td>
          <td class="delta-cell">
            {% if row.arrow %}<span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {% endif %}{{ row.delta_rank }}
          </td>
          <td>
            <span class="traj-badge traj-{{ row.trajectory_state }}">{{ row.trajectory_label }}</span>
          </td>
        </tr>
        <tr class="breakdown-row" data-view="composite" id="bd-{{ row.sector_id }}">
          <td colspan="10">{{ row.breakdown_html | safe }}</td>
        </tr>
        {% endfor %}
```

- [ ] **Step 5: Add CSS for view visibility + composite breakdown**

In the `<style>` block, near the existing `.breakdown-row` rules (around line 376), add:

```css
/* Sector-view toggle: show only rows matching the active view.
   ID-prefixed selector beats .breakdown-row.open on specificity, so
   wrong-view open breakdowns stay hidden. */
#leaderboard-table[data-view="split"] tr[data-view="composite"],
#leaderboard-table[data-view="composite"] tr[data-view="split"] { display: none; }
.sw-view select { margin-left: 4px; }
.composite-bd-panels { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 700px) { .composite-bd-panels { grid-template-columns: 1fr; } }
.composite-bd-header { font-weight: 600; margin-bottom: 8px; color: #3E392B; }
.cbh-region-tag { font-weight: 600; opacity: 0.7; margin-bottom: 4px; }
.cbh-note { opacity: 0.6; font-weight: 400; }
```

- [ ] **Step 6: Run the markup test**

Run: `.venv/bin/pytest tests/test_dashboard_js.py::test_built_html_has_both_row_sets_and_toggle -v`
Expected: PASS.

- [ ] **Step 7: Make `applyRanking` and `sortTable` view-aware**

In `dashboard/templates/index.html.j2`, replace the `sortTable` row collection so it sorts only visible rows. Change line 911 from:

```javascript
  var rows = Array.from(tbody.querySelectorAll('tr.leaderboard-row'));
```

to:

```javascript
  var view = (table.getAttribute('data-view') || 'split');
  var rows = Array.from(tbody.querySelectorAll('tr.leaderboard-row[data-view="' + view + '"]'));
```

Then rewrite the sentiment-toggle IIFE (lines 953-1058). Replace the entire `// ----- Sentiment toggle wiring -----` block with:

```javascript
// ----- Sentiment + sector-view toggle wiring -----
(function () {
  var LS_ENABLED = "sentimentEnabled", LS_WEIGHT = "sentimentWeight", LS_VIEW = "sectorView";
  var toggle = document.getElementById("sentiment-toggle");
  var weightInput = document.getElementById("sentiment-weight");
  var viewSel = document.getElementById("sector-view-toggle");
  var table = document.getElementById("leaderboard-table");
  var weightWrap = weightInput ? weightInput.closest(".sw-weight") : null;
  if (!toggle || !weightInput || !viewSel || !table || typeof Rescore === "undefined") { return; }

  function readView() {
    try { return localStorage.getItem(LS_VIEW) === "composite" ? "composite" : "split"; }
    catch (e) { return "split"; }
  }
  function fmt(n, d) { return (n === null || n === undefined) ? "—" : Number(n).toFixed(d); }

  // Update every score-tree on the page whose key is in `scored`.
  function updateTrees(scored, W) {
    var dataPct = Math.round((1 - W) * 100), sentPct = Math.round(W * 100);
    Object.keys(scored).forEach(function (key) {
      var sel = '.score-tree[data-sector-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]';
      var tree = document.querySelector(sel);
      if (!tree) { return; }
      var cv = tree.querySelector(".st-composite-val");
      if (cv) { cv.textContent = fmt(scored[key].composite, 3); }
      var dwt = tree.querySelector(".st-data-wt"); if (dwt) { dwt.textContent = "(" + dataPct + "%)"; }
      var swt = tree.querySelector(".st-sent-wt"); if (swt) { swt.textContent = "(" + sentPct + "%)"; }
    });
  }

  // Update the visible leaderboard rows' cells from `scored`.
  function updateRows(rows, scored) {
    rows.forEach(function (tr) {
      var r = scored[tr.getAttribute("data-sector-key")];
      if (!r) { return; }
      var badge = tr.querySelector(".rank-cell .rank-badge");
      if (badge) { badge.textContent = r.rank; badge.classList.toggle("top3", r.rank <= 3); }
      var compCell = tr.querySelector(".composite-cell");
      if (compCell) { compCell.textContent = fmt(r.composite, 3); }
      var deltaCell = tr.querySelector(".delta-cell");
      if (deltaCell) {
        var arrow = r.delta_rank > 0 ? "▲" : (r.delta_rank < 0 ? "▼" : "");
        var cls = r.delta_rank > 0 ? "up" : (r.delta_rank < 0 ? "down" : "");
        var dtxt = r.delta_rank === 0 ? "—" : (r.delta_rank > 0 ? "+" : "") + r.delta_rank.toFixed(1);
        deltaCell.innerHTML = '<span class="arrow ' + cls + '">' + arrow + '</span> ' + dtxt;
      }
      var traj = tr.querySelector(".traj-badge");
      if (traj) { traj.className = "traj-badge traj-" + r.trajectory_state; traj.textContent = r.trajectory_label; }
      var sectorCell = tr.querySelectorAll("td")[1];
      if (sectorCell) {
        var ebadge = sectorCell.querySelector(".emerging-badge");
        if (r.emerging && !ebadge) {
          var s = document.createElement("span");
          s.className = "emerging-badge"; s.textContent = "⬆ Emerging";
          sectorCell.appendChild(s);
        } else if (!r.emerging && ebadge) { ebadge.remove(); }
      }
    });
  }

  function sortVisibleByRank(rows, scored, tbody) {
    rows.sort(function (a, b) {
      var ra = scored[a.getAttribute("data-sector-key")];
      var rb = scored[b.getAttribute("data-sector-key")];
      return (ra ? ra.rank : 1e9) - (rb ? rb.rank : 1e9);
    });
    rows.forEach(function (tr) {
      tbody.appendChild(tr);
      var sid = tr.getAttribute("data-sector-id");
      var bd = sid ? document.getElementById("bd-" + sid) : null;
      if (bd) { tbody.appendChild(bd); }
    });
  }

  function applyRanking() {
    var enabled = toggle.checked;
    var weight = Math.min(100, Math.max(0, parseInt(weightInput.value, 10) || 0));
    var W = enabled ? weight / 100 : 0;
    var view = viewSel.value === "composite" ? "composite" : "split";
    try {
      localStorage.setItem(LS_ENABLED, enabled ? "true" : "false");
      localStorage.setItem(LS_WEIGHT, String(weight));
      localStorage.setItem(LS_VIEW, view);
    } catch (e) {}
    weightInput.disabled = !enabled;
    if (weightWrap) { weightWrap.setAttribute("data-disabled", String(!enabled)); }

    // Compute both maps so breakdown sub-panels stay live in either view.
    var scoredSplit = Rescore.rescore(RESCORE_DATA, W);
    var scoredComposite = Rescore.rescore(Rescore.mergeComposite(RESCORE_DATA), W);
    updateTrees(scoredSplit, W);
    updateTrees(scoredComposite, W);

    table.setAttribute("data-view", view);
    var activeScored = view === "composite" ? scoredComposite : scoredSplit;
    var tbody = table.querySelector("tbody");
    var rows = Array.prototype.slice.call(
      tbody.querySelectorAll('tr.leaderboard-row[data-view="' + view + '"]'));
    updateRows(rows, activeScored);
    sortVisibleByRank(rows, activeScored, tbody);
  }

  // Init from persisted state.
  var enabled0 = false, weight0 = 30;
  try {
    enabled0 = localStorage.getItem(LS_ENABLED) === "true";
    var w = parseInt(localStorage.getItem(LS_WEIGHT), 10);
    if (!isNaN(w)) { weight0 = Math.min(100, Math.max(0, w)); }
  } catch (e) {}
  toggle.checked = enabled0;
  weightInput.value = weight0;
  viewSel.value = readView();
  table.setAttribute("data-view", viewSel.value);
  toggle.addEventListener("change", applyRanking);
  weightInput.addEventListener("input", applyRanking);
  viewSel.addEventListener("change", applyRanking);
  applyRanking();
})();
```

- [ ] **Step 8: Rebuild and run the JS test suite**

Run: `.venv/bin/python dashboard/build.py`
Expected: builds with no error.

Run: `.venv/bin/pytest tests/test_dashboard_js.py -v`
Expected: PASS (existing render-context guards + the new both-row-sets test).

- [ ] **Step 9: Commit**

```bash
git add dashboard/templates/index.html.j2 tests/test_dashboard_js.py
git commit -m "feat: sector view toggle control, composite rows, view-aware rescoring"
```

---

### Task 5: Final verification — full build, scoped test suite, browser check

**Files:**
- None (verification only)

- [ ] **Step 1: Confirm the destructive state test SKIPs, then run the full suite**

Run: `.venv/bin/pytest tests/test_state_smoke.py -v`
Expected: all tests SKIP (production DB safe). If any RUN, STOP — do not run the full suite.

Run: `.venv/bin/pytest -q`
Expected: all pass (existing + new composite/parity/js tests); the 5 state-smoke tests SKIP.

- [ ] **Step 2: Rebuild the dashboard**

Run: `.venv/bin/python dashboard/build.py`
Expected: `Dashboard built: …/docs/index.html`.

- [ ] **Step 3: Verify in the browser**

Open `docs/index.html`. Confirm:
- Default view is Region-split (22 rows).
- Switching to "Composite (US+EU)" shows 11 rows; each sector appears once.
- Rank/Δ/Trend recompute; clicking a composite row shows both US and EU panels.
- Toggling sentiment on (weight 30%) re-ranks in both views; reload preserves the chosen view (localStorage).

Use the preview tooling (preview_start → preview_snapshot/preview_click) to capture proof, or screenshot the composite view.

- [ ] **Step 4: Commit any rebuilt docs**

```bash
git add docs/
git commit -m "chore: rebuild dashboard with sector view toggle"
```

---

## Self-Review

**1. Spec coverage:**
- Combine rule (mean) → Task 1 (`mergeComposite`) + Task 2 (`_build_composite_history`). ✓
- Reuse-not-duplication (Python via existing builders, JS via existing `rescore`) → Tasks 2, 3, 1. ✓
- DOM strategy (both row sets, CSS visibility) → Task 4 steps 4-5. ✓
- Composite key `ALL|<sector>` everywhere → Global Constraints + Tasks 1/3/4. ✓
- Composite breakdown = header + two regional panels (reused HTML) → Task 3 step 3. ✓
- UI control + localStorage `sectorView`, default split → Task 4 steps 3, 7. ✓
- `applyRanking` view-aware, compose with sentiment, update visible rows, scope sort → Task 4 step 7. ✓
- Sub-panel score-trees stay live in composite view → Task 4 step 7 (`updateTrees` for both maps). ✓
- Testing: parity (Task 1), Python aggregation (Task 2), render context + both-row-sets (Tasks 3-4). ✓
- Edge cases: symmetry guard in `mergeComposite`; NaN→0 inherited; <2 scans handled by existing `rescore`/builders. ✓
- Out of scope (weighted/re-pool, scan.py/scoring/DB, other tabs) → untouched. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**3. Type consistency:** `mergeComposite(data)→data-shaped object`, `_build_composite_history(df)→df`, `_build_composite_rows(df, dict)→list[dict]`; composite key `ALL|<sector>` and `sector_id` `ALL-<sector_>` consistent across Tasks 1/3/4. `rescore()` signature unchanged. ✓

**Note on header live-updates:** The spec mentioned the composite breakdown header could show live US/EU ranks. The plan renders a static descriptive header (`Composite of US + EU (mean)`) and keeps the *live* numbers in the two regional sub-panels' score-trees (which `updateTrees` refreshes in both views) and the composite row's own cells. This satisfies the intent (transparency into both sides) without a second per-region rescore wired into the header — a deliberate YAGNI trim. If live header numbers are later wanted, `scoredSplit` is already computed in `applyRanking` and can populate header spans.
