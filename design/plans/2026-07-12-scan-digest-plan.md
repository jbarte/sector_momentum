# "What Changed Today" Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a summary strip above the sector leaderboard listing new top-5 entries and biggest rank movers vs the previous scan.

**Architecture:** A new `dashboard/assets/scan-digest.js` computes the diff entirely client-side from the already-shipped `SCAN_HISTORY` JSON blob (no Python/schema changes). It exposes `window.renderScanDigest(scanId)`, called on page load and hooked into `scan-history.js`'s existing `showScan`/`restoreLatest` so the digest also updates when browsing historical scans.

**Tech Stack:** Vanilla JS (client-side), Jinja2 templates, pytest (render test only — no JS test harness in this repo)

## Global Constraints

- Branch: `feature/scan-digest` (already created; design spec committed at `4dfbfb5`)
- Never commit `docs/` from feature branches (CI rebuilds on main)
- Follow conventional commits (`feat:`, `fix:`, `chore:`)
- All i18n strings need both EN (in template) and SV (in `_i18n.html.j2`)
- JS must be vanilla — no external dependencies
- No JS unit test harness exists in this repo — verify new JS via syntax check (`node --check`) and a Python render test that the markup/script tag land correctly, matching the precedent set by `scan-history.js` (no dedicated JS tests, browser-verified)
- `SCAN_HISTORY` blob already ships every scan's `rank`/`composite`/`level`/`change`/`data`/`sentiment` per `region|sector` key (see `dashboard/figures.py::_build_scan_history_data`) — reuse as-is, no new JSON blob or template context key

---

### Task 1: `dashboard/assets/scan-digest.js` — digest computation + rendering

**Files:**
- Create: `dashboard/assets/scan-digest.js`

**Interfaces:**
- Consumes: global `SCAN_HISTORY` (already defined by the template — shape: `{"scans": [{"id": int, "date": str, "sectors": int, "top": str}, ...] (newest first), "scores": {"<scan_id>": {"<region>|<sector>": {"rank": int, "composite": float, "level": float, "change": float, "data": float, "sentiment": float}}}}`)
- Consumes: DOM elements `#scan-digest-banner`, `#digest-chips-entries`, `#digest-chips-up`, `#digest-chips-down`, and `.digest-cluster[data-cluster="entries"|"up"|"down"]` inside the banner (created by Task 3 — this file guards against their absence and no-ops)
- Produces: global function `window.renderScanDigest(scanId)`, called by Task 2's hook in `scan-history.js` and by this file itself on load

- [ ] **Step 1: Create the file**

```javascript
// "What changed today" digest.
// Summarizes new top-5 entries and biggest rank movers between the viewed
// scan and its predecessor, using data already shipped in SCAN_HISTORY.
(function () {
  "use strict";
  if (typeof SCAN_HISTORY === "undefined" || !SCAN_HISTORY.scans.length) return;

  var banner = document.getElementById("scan-digest-banner");
  if (!banner) return;

  function findPrevScanId(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId && i + 1 < SCAN_HISTORY.scans.length) {
        return SCAN_HISTORY.scans[i + 1].id;
      }
    }
    return null;
  }

  function computeDigest(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return null;
    var prevId = findPrevScanId(scanId);
    if (prevId === null) return null;
    var prevScores = SCAN_HISTORY.scores[String(prevId)];
    if (!prevScores) return null;

    var entries = [];
    var movers = [];
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var s = scores[key];
      var parts = key.split("|");
      var region = parts[0];
      var sector = parts[1];
      var prev = prevScores[key];

      if (s.rank <= 5 && (!prev || prev.rank > 5)) {
        entries.push({ key: key, sector: sector, region: region, rank: s.rank });
      }

      if (prev) {
        var delta = prev.rank - s.rank;
        if (delta !== 0) {
          movers.push({ key: key, sector: sector, region: region, rank: s.rank, delta: delta });
        }
      }
    }

    entries.sort(function (a, b) { return a.rank - b.rank; });
    movers.sort(function (a, b) {
      var diff = Math.abs(b.delta) - Math.abs(a.delta);
      return diff !== 0 ? diff : a.rank - b.rank;
    });

    return {
      entries: entries,
      up: movers.filter(function (m) { return m.delta > 0; }).slice(0, 3),
      down: movers.filter(function (m) { return m.delta < 0; }).slice(0, 3),
    };
  }

  function fmtChip(item, isMover) {
    var label = item.sector + " (" + item.region + ")";
    if (!isMover) return label + " #" + item.rank;
    var cls = item.delta > 0 ? "up" : "down";
    var arrow = item.delta > 0 ? "▲" : "▼";
    return label + ' <span class="arrow ' + cls + '">' + arrow + "</span>" + Math.abs(item.delta);
  }

  function renderCluster(clusterKey, items, isMover) {
    var cluster = banner.querySelector('[data-cluster="' + clusterKey + '"]');
    var container = document.getElementById("digest-chips-" + clusterKey);
    if (!cluster || !container) return;
    if (!items.length) {
      cluster.style.display = "none";
      container.innerHTML = "";
      return;
    }
    cluster.style.display = "";
    container.innerHTML = items
      .map(function (item) { return '<span class="digest-chip">' + fmtChip(item, isMover) + "</span>"; })
      .join("");
  }

  window.renderScanDigest = function (scanId) {
    var digest = computeDigest(scanId);
    if (!digest) {
      banner.style.display = "none";
      return;
    }
    renderCluster("entries", digest.entries, false);
    renderCluster("up", digest.up, true);
    renderCluster("down", digest.down, true);
    var hasAny = digest.entries.length || digest.up.length || digest.down.length;
    banner.style.display = hasAny ? "" : "none";
  };

  window.renderScanDigest(SCAN_HISTORY.scans[0].id);
})();
```

- [ ] **Step 2: Verify syntax and size**

Run: `node --check dashboard/assets/scan-digest.js && wc -l dashboard/assets/scan-digest.js`
Expected: no output from `node --check` (valid syntax), `~95` lines from `wc -l`

- [ ] **Step 3: Commit**

```bash
git add dashboard/assets/scan-digest.js
git commit -m "feat: add scan-digest.js for client-side what-changed summary"
```

---

### Task 2: Hook `scan-history.js` into the digest

**Files:**
- Modify: `dashboard/assets/scan-history.js:110-140`

**Interfaces:**
- Consumes: `window.renderScanDigest(scanId)` from Task 1 (guarded with `typeof` — this file has no hard dependency on `scan-digest.js` being present)
- Consumes: `latestScanId` (already an existing local variable in this file, line 18: `var latestScanId = SCAN_HISTORY.scans[0].id;`)

- [ ] **Step 1: Add the digest call to `showScan`**

In `dashboard/assets/scan-history.js`, find:

```javascript
    if (sentimentToggle) sentimentToggle.disabled = true;
    if (sentimentControl) sentimentControl.style.opacity = "0.4";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
  };

  window.restoreLatest = function () {
```

Replace with:

```javascript
    if (sentimentToggle) sentimentToggle.disabled = true;
    if (sentimentControl) sentimentControl.style.opacity = "0.4";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
    if (typeof window.renderScanDigest === "function") window.renderScanDigest(scanId);
  };

  window.restoreLatest = function () {
```

- [ ] **Step 2: Add the digest call to `restoreLatest`**

Find:

```javascript
    if (sentimentControl) sentimentControl.style.opacity = "";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
  };

  // Delegated click + keyboard on scan-index table
```

Replace with:

```javascript
    if (sentimentControl) sentimentControl.style.opacity = "";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
    if (typeof window.renderScanDigest === "function") window.renderScanDigest(latestScanId);
  };

  // Delegated click + keyboard on scan-index table
```

- [ ] **Step 3: Verify syntax**

Run: `node --check dashboard/assets/scan-history.js`
Expected: no output (valid syntax)

- [ ] **Step 4: Run the existing dashboard test suite (regression check)**

Run: `python3 -m pytest tests/test_dashboard_js.py -v`
Expected: all pass (this file isn't rendered by Python, but confirms nothing else broke)

- [ ] **Step 5: Commit**

```bash
git add dashboard/assets/scan-history.js
git commit -m "feat: hook scan-history.js into the what-changed digest"
```

---

### Task 3: Template — banner markup, CSS, i18n, asset copy, render test

**Files:**
- Modify: `dashboard/templates/index.html.j2:60-64,624`
- Modify: `dashboard/templates/_style.html.j2:219`
- Modify: `dashboard/templates/_i18n.html.j2:66`
- Modify: `dashboard/build.py:294-296`
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `window.renderScanDigest` from Task 1, `SCAN_HISTORY`/`scan_history_json` template context (already exists — no new context key)
- Produces: `#scan-digest-banner` markup that Task 1's JS targets

- [ ] **Step 1: Add the banner markup**

In `dashboard/templates/index.html.j2`, the sentiment-control block ends at line 60 and the existing scan-history banner starts at line 61:

```html
  </div>
  <div id="scan-history-banner" style="display:none">
```

Replace with (inserting the new banner before the existing one):

```html
  </div>
  <div id="scan-digest-banner" style="display:none">
    <span class="digest-cluster" data-cluster="entries">
      <span data-i18n="digest_new_top5">New in Top 5:</span>
      <span class="digest-chips" id="digest-chips-entries"></span>
    </span>
    <span class="digest-cluster" data-cluster="up">
      <span data-i18n="digest_gains">Biggest gains:</span>
      <span class="digest-chips" id="digest-chips-up"></span>
    </span>
    <span class="digest-cluster" data-cluster="down">
      <span data-i18n="digest_drops">Biggest drops:</span>
      <span class="digest-chips" id="digest-chips-down"></span>
    </span>
  </div>
  <div id="scan-history-banner" style="display:none">
```

- [ ] **Step 2: Add the script tag**

In `dashboard/templates/index.html.j2`, find:

```html
<script src="assets/scan-history.js"></script>
```

Replace with:

```html
<script src="assets/scan-history.js"></script>
<script src="assets/scan-digest.js"></script>
```

- [ ] **Step 3: Add CSS**

In `dashboard/templates/_style.html.j2`, find the last line of the existing scan-history-banner block:

```css
#scan-history-banner a:hover { text-decoration: underline; }
```

Replace with:

```css
#scan-history-banner a:hover { text-decoration: underline; }
#scan-digest-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 16px;
  padding: 8px 16px;
  margin-bottom: 8px;
  background: var(--bg-raised);
  border-left: 3px solid var(--brand-strong);
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--fg3);
}
.digest-cluster { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.digest-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.digest-chip { white-space: nowrap; }
```

- [ ] **Step 4: Add SV translations**

In `dashboard/templates/_i18n.html.j2`, find:

```javascript
    scan_viewing: "Visar skanning #",
    scan_back_to_latest: "Tillbaka till senaste",
```

Replace with:

```javascript
    scan_viewing: "Visar skanning #",
    scan_back_to_latest: "Tillbaka till senaste",
    digest_new_top5: "Nya i topp 5:",
    digest_gains: "Störst uppgång:",
    digest_drops: "Störst nedgång:",
```

- [ ] **Step 5: Copy `scan-digest.js` to `docs/assets/` at build time**

In `dashboard/build.py`, find:

```python
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
```

Replace with:

```python
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
    scan_digest_src = _ASSETS_DIR / "scan-digest.js"
    if scan_digest_src.exists():
        shutil.copy2(scan_digest_src, docs_assets / "scan-digest.js")
```

- [ ] **Step 6: Write the render test**

Add to `tests/test_dashboard_js.py` (same context-dict pattern as `test_scan_history_json_in_rendered_output`, which precedes this test in the same file):

```python
def test_scan_digest_markup_in_rendered_output(tmp_path):
    """Rendered index.html contains the scan-digest banner, script tag, and i18n keys."""
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
    assert 'id="scan-digest-banner"' in html
    assert "assets/scan-digest.js" in html
    assert 'data-i18n="digest_new_top5"' in html
    assert 'data-i18n="digest_gains"' in html
    assert 'data-i18n="digest_drops"' in html
    assert 'id="digest-chips-entries"' in html
    assert 'id="digest-chips-up"' in html
    assert 'id="digest-chips-down"' in html
```

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_dashboard_js.py -v`
Expected: all pass, including the new `test_scan_digest_markup_in_rendered_output` and the existing `test_render_context_covers_all_template_js_vars` (unaffected — no new context key was added)

- [ ] **Step 8: Commit**

```bash
git add dashboard/templates/index.html.j2 dashboard/templates/_style.html.j2 dashboard/templates/_i18n.html.j2 dashboard/build.py tests/test_dashboard_js.py
git commit -m "feat: wire scan-digest banner into template, CSS, i18n, and build"
```

---

### Task 4: BACKLOG.md + full test suite + push + PR

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: all changes from Tasks 1-3
- Produces: PR against `main`

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest -v`
Expected: all pass, 0 failures

- [ ] **Step 2: Manual browser verification**

Run: `python3 dashboard/build.py` then open `docs/index.html` in a browser (or serve `docs/` locally). Confirm:
- The digest banner appears above the leaderboard when there's a prior scan with rank changes
- Clicking a historical scan row in the History tab's scan index updates the digest to that scan's diff
- Clicking "Back to latest" restores the latest scan's digest
- If the DB only has one scan, the banner stays hidden (no predecessor to diff against)

- [ ] **Step 3: Update `BACKLOG.md`**

Per this repo's lifecycle rules (top of `BACKLOG.md`): delete the shipped item's Queued section entirely and add a Done entry at the top of Done.

Delete this Queued section (currently under `# Queued`):

```markdown
## "What changed today" digest

**What:** A summary strip above the sector leaderboard: new top-5 entries,
biggest rank jumps/drops, and trajectory flips vs the previous scan.

**Why:** Users currently diff scans mentally. The `SCAN_HISTORY` blob (shipped
2026-07-12) already ships every scan's scores to the client, so this is pure
client-side JS — compare `scans[0]` vs `scans[1]`, render a few chips. Zero
pipeline or schema changes. Cheapest high-visibility win on the list.
```

Add this entry at the top of the `# Done` section:

```markdown
- ~~"What changed today" digest~~ — a summary strip above the sector
  leaderboard shows new top-5 entries and the biggest rank movers (up to 3
  gains, 3 drops) vs the previous scan. Entirely client-side
  (`dashboard/assets/scan-digest.js`), reusing the `SCAN_HISTORY` blob already
  shipped by renderable-scan-history — no pipeline or schema changes. Updates
  live when browsing historical scans via the existing scan-history viewer.
  Trajectory flips deferred (would need porting the server-side trailing-slope
  algorithm to JS). *(2026-07-12)*
```

- [ ] **Step 4: Commit the backlog update**

```bash
git add BACKLOG.md
git commit -m "chore: move what-changed digest to Done in BACKLOG.md"
```

- [ ] **Step 5: Push and create the PR**

```bash
git push -u origin feature/scan-digest
```

```bash
gh pr create --title "feat: what-changed digest above the sector leaderboard" --body "$(cat <<'EOF'
## Summary

- **`scan-digest.js`**: new client-side module that diffs the viewed scan against its predecessor using the already-shipped `SCAN_HISTORY` blob — no pipeline or schema changes
- **New top-5 entries**: sectors whose rank crossed into the top 5 this scan
- **Biggest movers**: up to 3 rank gains and 3 rank drops by absolute delta
- **Historical-scan aware**: hooks into `scan-history.js`'s `showScan`/`restoreLatest` so the digest updates when browsing past scans, and restores on "Back to latest"
- **i18n**: SV translations for the three cluster labels
- Trajectory flips deferred to a fast-follow (needs porting the server-side trailing-slope algorithm to JS — see design spec)

## Test plan

- [x] `test_scan_digest_markup_in_rendered_output` — banner markup, script tag, and i18n keys present in rendered output
- [x] Full suite passes
- [x] Manual browser check: digest renders for the latest scan, updates on historical scan clicks, restores on "Back to latest", hides gracefully with only one scan in the DB

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
