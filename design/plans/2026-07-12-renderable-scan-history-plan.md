# Renderable Scan History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make past scans viewable in the dashboard — clicking a scan row in the History tab rebuilds the Leaderboard table with that scan's scores.

**Architecture:** An embedded `SCAN_HISTORY` JSON blob ships all scan scores with the page. A new `scan-history.js` file handles click events on scan-index rows, rebuilds the leaderboard tbody client-side, and provides a "Back to latest" restore. No new data loaders or schema changes.

**Tech Stack:** Python (dashboard builder), vanilla JS (client-side), Jinja2 templates, pytest

## Global Constraints

- Branch: `feature/renderable-scan-history` (already created)
- Never commit `docs/` from feature branches (CI rebuilds on main)
- Follow conventional commits (`feat:`, `fix:`, `chore:`)
- All i18n strings need both EN (in template) and SV (in `_i18n.html.j2`)
- JS must be vanilla — no external dependencies
- Keyboard accessible (tabindex, Enter/Space handlers)

---

### Task 1: `_build_scan_history_data` builder + tests

**Files:**
- Modify: `dashboard/build.py:52-71` (add import for new function)
- Modify: `dashboard/figures.py` (add `_build_scan_history_data` after `_build_rescore_data`)
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `all_scores_df` DataFrame from `get_scan_history(conn, n_scans=None)` — columns: `scan_id, run_at, region, gics_sector, level_score, change_score, data_score, sentiment_score, composite, rank`
- Produces: `_build_scan_history_data(all_scores_df) -> dict` with keys `scans` (list of scan metadata dicts) and `scores` (dict keyed by string scan_id, each value a dict keyed by `region|sector`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard_js.py`:

```python
def test_build_scan_history_data_shape():
    """_build_scan_history_data returns scans and scores with correct structure."""
    from dashboard.figures import _build_scan_history_data

    rows = []
    for scan_id, run_at in [(1, "2026-06-22T00:00:00"), (2, "2026-06-23T00:00:00")]:
        for region, sector, comp, lvl, chg, data, sent, rank in [
            ("US", "Technology", 0.8, 0.7, 0.4, 0.55, 0.2, 1.0),
            ("EU", "Energy", 0.3, 0.2, 0.1, 0.15, 0.0, 2.0),
        ]:
            rows.append({
                "scan_id": scan_id, "run_at": run_at, "region": region,
                "gics_sector": sector, "level_score": lvl, "change_score": chg,
                "data_score": data, "sentiment_score": sent, "composite": comp,
                "rank": rank,
            })
    df = pd.DataFrame(rows)
    result = _build_scan_history_data(df)

    assert "scans" in result
    assert "scores" in result
    assert len(result["scans"]) == 2
    # Newest first
    assert result["scans"][0]["id"] == 2
    assert result["scans"][1]["id"] == 1
    # Each scan entry has required fields
    for s in result["scans"]:
        assert "id" in s and "date" in s and "sectors" in s and "top" in s
    # Scores keyed by string scan_id
    assert "2" in result["scores"]
    assert "1" in result["scores"]
    # Each sector present
    assert "US|Technology" in result["scores"]["2"]
    assert "EU|Energy" in result["scores"]["2"]
    # Required score fields
    for key, sc in result["scores"]["2"].items():
        for field in ("rank", "composite", "level", "change", "data", "sentiment"):
            assert field in sc, f"Missing {field} in {key}"


def test_build_scan_history_data_empty():
    """Empty DataFrame returns empty structure."""
    from dashboard.figures import _build_scan_history_data

    df = pd.DataFrame(columns=[
        "scan_id", "run_at", "region", "gics_sector", "level_score",
        "change_score", "data_score", "sentiment_score", "composite", "rank",
    ])
    result = _build_scan_history_data(df)
    assert result == {"scans": [], "scores": {}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_js.py::test_build_scan_history_data_shape tests/test_dashboard_js.py::test_build_scan_history_data_empty -v`
Expected: FAIL with `ImportError: cannot import name '_build_scan_history_data'`

- [ ] **Step 3: Implement `_build_scan_history_data`**

Add to `dashboard/figures.py` after `_build_rescore_data` (after line 531):

```python
def _build_scan_history_data(all_scores_df) -> dict:
    """Per-scan scores for every sector, for the client-side scan-history viewer."""
    if all_scores_df.empty:
        return {"scans": [], "scores": {}}

    df = all_scores_df.copy()

    scan_ids = sorted(df["scan_id"].unique(), reverse=True)
    scans = []
    for sid in scan_ids:
        g = df[df["scan_id"] == sid]
        run_at_raw = str(g["run_at"].iloc[0])
        try:
            disp = pd.to_datetime(run_at_raw).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            disp = run_at_raw
        top = g.loc[g["rank"].idxmin()]
        scans.append({
            "id": int(sid),
            "date": disp,
            "sectors": int(len(g)),
            "top": f"{top['gics_sector']} ({top['region']})",
        })

    scores = {}
    for sid in scan_ids:
        g = df[df["scan_id"] == sid]
        sid_scores = {}
        for _, row in g.iterrows():
            key = f"{row['region']}|{row['gics_sector']}"
            sf = _safe_float
            sid_scores[key] = {
                "rank": int(sf(row["rank"])) if sf(row["rank"]) is not None else 99,
                "composite": round(sf(row["composite"]) or 0.0, 3),
                "level": round(sf(row["level_score"]) or 0.0, 3),
                "change": round(sf(row["change_score"]) or 0.0, 3),
                "data": round(sf(row["data_score"]) or 0.0, 3),
                "sentiment": round(sf(row["sentiment_score"]) or 0.0, 3),
            }
        scores[str(int(sid))] = sid_scores

    return {"scans": scans, "scores": scores}
```

- [ ] **Step 4: Add the re-export in `dashboard/build.py`**

In `dashboard/build.py`, add `_build_scan_history_data` to the import from `dashboard.figures` (line 52-64):

```python
from dashboard.figures import (                   # noqa: E402, F401
    _build_rrg_figure,
    _build_sentiment_scatter_figure,
    _build_drilldown_data,
    _build_movers_figure,
    _build_history_figure,
    _build_backtest_figures,
    _build_rotation_figures,
    _build_backtest_context,
    _build_rescore_data,
    _build_scan_history_data,
    _WARM_PALETTE,
    _SCORE_SIGNAL_COLORS,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_js.py::test_build_scan_history_data_shape tests/test_dashboard_js.py::test_build_scan_history_data_empty -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/figures.py dashboard/build.py tests/test_dashboard_js.py
git commit -m "feat: add _build_scan_history_data builder for scan history JSON"
```

---

### Task 2: Wire builder into build.py + template context

**Files:**
- Modify: `dashboard/build.py:186-318` (add builder call and pass to template context)
- Modify: `dashboard/build.py:280-290` (copy scan-history.js to docs/assets/)

**Interfaces:**
- Consumes: `_build_scan_history_data(all_scores_df) -> dict` from Task 1
- Produces: `scan_history_json` key in the template render context

- [ ] **Step 1: Add builder call in `main()`**

In `dashboard/build.py`, after line 190 (`_generate_scan_reports(all_scores_df, out_dir / "reports")`), add:

```python
    logger.info("Building scan history data …")
    scan_history_data = _build_scan_history_data(all_scores_df)
```

- [ ] **Step 2: Pass `scan_history_json` to the template context**

In the `_render()` call for `index.html.j2` (line 296-318), add `scan_history_json` to the context dict, after `rescore_data_json`:

```python
            rescore_data_json=rescore_data_json,
            scan_history_json=json.dumps(scan_history_data),
```

- [ ] **Step 3: Copy `scan-history.js` to `docs/assets/`**

After the existing `rescore.js` copy block (lines 288-289), add:

```python
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
```

- [ ] **Step 4: Add the `SCAN_HISTORY` variable and script tag to `index.html.j2`**

In `dashboard/templates/index.html.j2`, after line 8 (`<script src="assets/rescore.js"></script>`), add:

```html
<script src="assets/scan-history.js"></script>
```

In the existing `<script>` block (after line 394 `var RESCORE_DATA = {{ rescore_data_json | js_json }};`), add:

```javascript
var SCAN_HISTORY = {{ scan_history_json | js_json }};
```

- [ ] **Step 5: Verify the render-context test still passes**

Run: `python3 -m pytest tests/test_dashboard_js.py::test_render_context_covers_all_template_js_vars -v`
Expected: PASS (the test finds all `_render()` calls and their context keys)

- [ ] **Step 6: Commit**

```bash
git add dashboard/build.py dashboard/templates/index.html.j2
git commit -m "feat: wire scan history JSON into build pipeline and template"
```

---

### Task 3: `scan-history.js` — client-side leaderboard rebuild

**Files:**
- Create: `dashboard/assets/scan-history.js`

**Interfaces:**
- Consumes: global `SCAN_HISTORY` variable (set by template), global `switchTab` function (existing tab switcher in index.html.j2)
- Produces: global functions `showScan(scanId)`, `restoreLatest()` called by delegated event handlers

- [ ] **Step 1: Create `dashboard/assets/scan-history.js`**

```javascript
// Client-side scan history viewer.
// Rebuilds the leaderboard table from SCAN_HISTORY data when the user
// clicks a past scan in the History tab's scan index.
(function () {
  "use strict";
  if (typeof SCAN_HISTORY === "undefined" || !SCAN_HISTORY.scans.length) return;

  var table = document.getElementById("leaderboard-table");
  if (!table) return;
  var tbody = table.querySelector("tbody");
  var originalTbody = tbody.innerHTML;
  var banner = document.getElementById("scan-history-banner");
  var bannerText = banner ? banner.querySelector(".scan-history-text") : null;
  var headerDate = document.querySelector(".scan-date");
  var originalDate = headerDate ? headerDate.innerHTML : "";
  var sentimentToggle = document.getElementById("sentiment-toggle");
  var sentimentControl = document.getElementById("sentiment-control");
  var latestScanId = SCAN_HISTORY.scans[0].id;

  function fmtScore(v) {
    return v.toFixed(3);
  }

  function fmtDelta(d) {
    if (d === 0) return "—";
    return (d > 0 ? "+" : "") + d.toFixed(1);
  }

  function findPrevScanId(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId && i + 1 < SCAN_HISTORY.scans.length) {
        return SCAN_HISTORY.scans[i + 1].id;
      }
    }
    return null;
  }

  function renderScanLeaderboard(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return;

    var prevId = findPrevScanId(scanId);
    var prevScores = prevId ? SCAN_HISTORY.scores[String(prevId)] : null;

    var entries = [];
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var s = scores[key];
      var delta = 0;
      if (prevScores && prevScores[key]) {
        delta = prevScores[key].rank - s.rank;
      }
      entries.push({ key: key, scores: s, delta: delta });
    }
    entries.sort(function (a, b) { return a.scores.rank - b.scores.rank; });

    var html = "";
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var sc = e.scores;
      var parts = e.key.split("|");
      var region = parts[0];
      var sector = parts[1];
      var rankClass = sc.rank <= 3 ? " top3" : "";
      var arrow = "";
      var arrowClass = "";
      if (e.delta > 0) { arrow = "▲"; arrowClass = "up"; }
      else if (e.delta < 0) { arrow = "▼"; arrowClass = "down"; }
      var arrowHtml = arrow ? '<span class="arrow ' + arrowClass + '">' + arrow + "</span> " : "";

      html += '<tr class="leaderboard-row">'
        + '<td class="rank-cell"><span class="rank-badge' + rankClass + '">' + sc.rank + "</span></td>"
        + "<td>" + sector + "</td>"
        + '<td><span class="tag-region">' + region + "</span></td>"
        + '<td class="composite-cell">' + fmtScore(sc.composite) + "</td>"
        + "<td>" + fmtScore(sc.level) + "</td>"
        + "<td>" + fmtScore(sc.change) + "</td>"
        + "<td>" + fmtScore(sc.data) + "</td>"
        + '<td class="sentiment-cell">' + fmtScore(sc.sentiment) + "</td>"
        + '<td class="delta-cell">' + arrowHtml + fmtDelta(e.delta) + "</td>"
        + "<td>—</td>"
        + "</tr>";
    }
    tbody.innerHTML = html;
  }

  function updateShowingBadge(scanId) {
    var scanTable = document.querySelector(".scan-index table");
    if (!scanTable) return;
    var rows = scanTable.querySelectorAll("tbody tr");
    rows.forEach(function (tr) {
      var sid = tr.getAttribute("data-scan-id");
      var badgeCell = tr.querySelector("td:first-child");
      tr.classList.remove("active-scan");
      if (badgeCell) badgeCell.innerHTML = "";
      if (sid && parseInt(sid, 10) === scanId) {
        tr.classList.add("active-scan");
        if (badgeCell) badgeCell.innerHTML = '<span class="showing-badge">● Showing</span>';
      }
    });
  }

  function findScanMeta(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId) return SCAN_HISTORY.scans[i];
    }
    return null;
  }

  window.showScan = function (scanId) {
    renderScanLeaderboard(scanId);
    updateShowingBadge(scanId);
    var meta = findScanMeta(scanId);
    if (headerDate && meta) {
      headerDate.innerHTML = '<span data-i18n="lastScan">Last scan:</span> #' + scanId + " · " + meta.date;
    }
    if (banner) banner.style.display = "";
    if (bannerText) {
      var prefix = bannerText.getAttribute("data-en-prefix") || "Viewing scan #";
      bannerText.textContent = prefix + scanId;
    }
    if (sentimentToggle) sentimentToggle.disabled = true;
    if (sentimentControl) sentimentControl.style.opacity = "0.4";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
  };

  window.restoreLatest = function () {
    tbody.innerHTML = originalTbody;
    updateShowingBadge(latestScanId);
    if (headerDate) headerDate.innerHTML = originalDate;
    if (banner) banner.style.display = "none";
    if (sentimentToggle) sentimentToggle.disabled = false;
    if (sentimentControl) sentimentControl.style.opacity = "";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
  };

  // Delegated click + keyboard on scan-index table
  var scanTable = document.querySelector(".scan-index table");
  if (scanTable) {
    scanTable.addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-scan-id]");
      if (!tr) return;
      var sid = parseInt(tr.getAttribute("data-scan-id"), 10);
      if (sid === latestScanId) { window.restoreLatest(); return; }
      window.showScan(sid);
    });
    scanTable.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var tr = e.target.closest("tr[data-scan-id]");
      if (!tr) return;
      e.preventDefault();
      var sid = parseInt(tr.getAttribute("data-scan-id"), 10);
      if (sid === latestScanId) { window.restoreLatest(); return; }
      window.showScan(sid);
    });
  }
})();
```

- [ ] **Step 2: Verify file exists and is well-formed**

Run: `wc -l dashboard/assets/scan-history.js`
Expected: ~130 lines

- [ ] **Step 3: Commit**

```bash
git add dashboard/assets/scan-history.js
git commit -m "feat: add scan-history.js for client-side historical scan viewing"
```

---

### Task 4: Template changes — banner, clickable scan rows, i18n, CSS

**Files:**
- Modify: `dashboard/templates/index.html.j2:60,186-225` (banner div + scan row attributes)
- Modify: `dashboard/templates/_i18n.html.j2` (SV translations)
- Modify: `dashboard/templates/_style.html.j2:196-198` (scan-index + banner CSS)
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `scan_history_json` template variable (from Task 2), `showScan`/`restoreLatest` globals (from Task 3)
- Produces: Clickable scan-index rows, history banner, render test

- [ ] **Step 1: Add the banner div above the leaderboard table**

In `dashboard/templates/index.html.j2`, after the sentiment-control div (after line 60, before `<div class="table-wrap">`), add:

```html
  <div id="scan-history-banner" style="display:none">
    <span class="scan-history-text" data-i18n="scan_viewing" data-en-prefix="Viewing scan #">Viewing scan #</span>
    <a href="javascript:void(0)" onclick="restoreLatest()" data-i18n="scan_back_to_latest">Back to latest</a>
  </div>
```

- [ ] **Step 2: Make scan-index rows clickable**

In `dashboard/templates/index.html.j2`, replace the scan-index `<tr>` (lines 210-211):

From:
```html
        <tr{% if s.scan_id == active_scan_id %} class="active-scan"{% endif %}>
          <td>{% if s.scan_id == active_scan_id %}<span class="showing-badge">● Showing</span>{% endif %}</td>
```

To:
```html
        <tr data-scan-id="{{ s.scan_id }}" tabindex="0"{% if s.scan_id == active_scan_id %} class="active-scan"{% endif %}>
          <td>{% if s.scan_id == active_scan_id %}<span class="showing-badge">● Showing</span>{% endif %}</td>
```

- [ ] **Step 3: Add SV translations to `_i18n.html.j2`**

In `dashboard/templates/_i18n.html.j2`, after the `sent_col_seasonal` line (around line 67), add:

```javascript
    scan_viewing: "Visar skanning #",
    scan_back_to_latest: "Tillbaka till senaste",
```

- [ ] **Step 4: Add CSS styles to `_style.html.j2`**

In `dashboard/templates/_style.html.j2`, after line 198 (`.showing-badge {...}`), add:

```css
.scan-index tr[data-scan-id] { cursor: pointer; }
.scan-index tr[data-scan-id]:hover { background: var(--bg-raised); }
.scan-index tr[data-scan-id]:focus-visible { outline: 2px solid var(--brand-strong); outline-offset: -2px; }
#scan-history-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  margin-bottom: 8px;
  background: var(--bg-raised);
  border-left: 3px solid var(--brand-strong);
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--fg3);
}
#scan-history-banner a {
  color: var(--brand-strong);
  font-weight: 500;
  text-decoration: none;
}
#scan-history-banner a:hover { text-decoration: underline; }
```

- [ ] **Step 5: Write render test**

Add to `tests/test_dashboard_js.py`:

```python
def test_scan_history_json_in_rendered_output(tmp_path):
    """Rendered index.html contains SCAN_HISTORY variable with valid JSON."""
    scan_history = {
        "scans": [{"id": 2, "date": "2026-07-12 06:00 UTC", "sectors": 22, "top": "Technology (US)"}],
        "scores": {"2": {"US|Technology": {"rank": 1, "composite": 0.8, "level": 0.7, "change": 0.4, "data": 0.55, "sentiment": 0.2}}},
    }
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-07-12",
            scan_index=[{"scan_id": 2, "run_at_display": "2026-07-12 06:00 UTC",
                         "run_at_raw": "2026-07-12T06:00:00", "sector_count": 22,
                         "top_sector": "Technology", "top_region": "US"}],
            active_scan_id=2,
            leaderboard_rows=[],
            rrg_data_json=_make_mock_plotly_json(),
            drilldown_data=json.dumps({}),
            sector_keys=[],
            movers_json=_make_mock_plotly_json(),
            history_json=_make_mock_plotly_json(),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            scan_history_json=json.dumps(scan_history),
            signals_list=[],
            plotly_bundle="assets/plotly.min.js",
            backtest_json=json.dumps({}),
            backtest_metrics=[],
            has_backtest=False,
            rotation_json=json.dumps([]),
            has_rotations=False,
        ),
    )
    html = out.read_text()
    assert "var SCAN_HISTORY =" in html
    assert "scan-history-banner" in html
    assert 'data-scan-id="2"' in html
    # Extract and parse the JSON
    start = html.index("var SCAN_HISTORY =") + len("var SCAN_HISTORY =")
    end = html.index(";", start)
    parsed = json.loads(html[start:end].strip())
    assert "scans" in parsed
    assert "scores" in parsed
    assert parsed["scans"][0]["id"] == 2
```

- [ ] **Step 6: Run all tests**

Run: `python3 -m pytest tests/test_dashboard_js.py -v`
Expected: All PASS (including the new render test and the existing `test_render_context_covers_all_template_js_vars` which now sees `scan_history_json` in the context)

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/index.html.j2 dashboard/templates/_i18n.html.j2 dashboard/templates/_style.html.j2 tests/test_dashboard_js.py
git commit -m "feat: clickable scan-index rows, history banner, i18n, CSS, and render test"
```

---

### Task 5: BACKLOG.md update + full test suite + push + PR

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: All changes from Tasks 1-4
- Produces: PR against `main`

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest -v`
Expected: All pass, 0 failures

- [ ] **Step 2: Update BACKLOG.md**

Move the "Renderable scan history" item from the queued section to Done. In the queued section (around line 327), add a strikethrough note. In the Done section (before the first existing Done entry), add:

```markdown
- ~~Renderable scan history~~ — clicking any scan row in the History tab rebuilds
  the Leaderboard with that scan's scores via an embedded `SCAN_HISTORY` JSON blob
  and client-side JS table rebuild. Sectors page only; charts stay multi-scan as-is.
  Keyboard accessible (tabindex + Enter/Space), i18n (EN+SV), "Back to latest"
  restore. *(2026-07-12)*
```

In the queued section, update the item to mark it done:

```markdown
## ~~Renderable scan history (view past scans in the dashboard)~~

*(done — see Done)*
```

- [ ] **Step 3: Commit backlog update**

```bash
git add BACKLOG.md
git commit -m "chore: move renderable scan history to Done in BACKLOG.md"
```

- [ ] **Step 4: Push and create PR**

```bash
git push -u origin feature/renderable-scan-history
```

Then create the PR:

```bash
gh pr create --title "feat: renderable scan history — view past scans in dashboard" --body "$(cat <<'EOF'
## Summary

- **Embedded JSON blob**: `_build_scan_history_data()` serializes all scan scores into a `SCAN_HISTORY` JS variable shipped with index.html (~3KB per scan)
- **Clickable scan rows**: History tab scan-index rows gain `data-scan-id` + `tabindex="0"` for click/keyboard interaction
- **JS leaderboard rebuild**: `scan-history.js` rebuilds the leaderboard tbody client-side with the selected scan's scores (rank, composite, level, change, data, sentiment, rank-delta)
- **Back to latest**: Banner with "Back to latest" link restores the original server-rendered leaderboard, re-enables sentiment toggle
- **i18n**: SV translations for banner text
- **Scope**: Sectors page only; charts/RRG/movers stay multi-scan as-is; themes/sentiment pages out of scope

## Test plan

- [x] `test_build_scan_history_data_shape` — builder returns correct structure
- [x] `test_build_scan_history_data_empty` — empty DataFrame handled
- [x] `test_scan_history_json_in_rendered_output` — rendered HTML contains valid SCAN_HISTORY JSON + banner + data-scan-id attributes
- [x] Full suite passes

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```
