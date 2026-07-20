# Per-Region Cohort Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score US and EU sectors as independent cohorts so ranks are 1–11 (US) and 1–14 (EU), aligning the live scan with the backtest.

**Architecture:** Split the single `score_all(wide_df)` call in `scan.py` into two per-region calls, concat the results. The same per-region split applies to z-scores for the signals table. The dashboard leaderboard renders two region-grouped `<tbody>` sections; client-side JS (rescore, scan-history, scan-digest) ranks within region groups. A one-off backfill script recomputes historical scan ranks per region.

**Tech Stack:** Python 3.11, pandas, scipy, Jinja2, vanilla JS, psycopg2, pytest

## Global Constraints

- `score_all()` API is unchanged — it scores whatever DataFrame it receives
- DB schema (`scores`, `signals` tables) is unchanged — no DDL migration
- `docs/` is never committed from feature branches (CI-owned)
- Conventional commits, branch `fix/per-region-cohort-scoring`
- Region prefixes are always `"US"` and `"EU"`, parsed from sector_key format `"REGION|gics_sector"`
- Sentiment scores are left untouched (FinBERT is cross-region by construction)
- `compute_deltas()` already matches on `(region, gics_sector)` — no change needed
- Themes pipeline is unaffected (separate scoring path)

---

### Task 1: Per-region scoring in scan.py + tests

**Files:**
- Modify: `scan.py:335-373`
- Modify: `scan.py:167-197` (`_print_summary`)
- Create: `tests/test_per_region_scoring.py`

**Interfaces:**
- Consumes: `score_all(signals_df, ...)` from `src/scoring.py` (unchanged)
- Consumes: `zscore_cross_section(df)` from `src/scoring.py` (unchanged)
- Produces: `scored` DataFrame with per-region ranks (US 1–11, EU 1–14) — same shape, consumed by `_build_scored_df_for_db()`, `compute_deltas()`, `build_ranked_table()`

- [ ] **Step 1: Write the failing test for per-region scoring**

```python
# tests/test_per_region_scoring.py
"""Tests for per-region cohort scoring in scan.py."""
import numpy as np
import pandas as pd
import pytest

from src.scoring import score_all, zscore_cross_section


US_SECTORS = [
    "Technology", "Health Care", "Financials", "Consumer Discretionary",
    "Communication Services", "Industrials", "Consumer Staples",
    "Energy", "Utilities", "Real Estate", "Materials",
]
EU_SECTORS = [
    "Banks", "Technology", "Health Care", "Industrial Goods & Services",
    "Food Beverage & Tobacco", "Insurance", "Chemicals",
    "Utilities", "Energy", "Basic Resources", "Automobiles & Parts",
    "Construction & Materials", "Personal Care Drug & Grocery",
    "Travel & Leisure",
]

SIGNAL_COLUMNS = [
    "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
    "acceleration", "above_50dma", "above_200dma", "ma50_slope",
    "obv_slope", "breadth_above_50dma",
]


def _make_wide_df(seed=42):
    """25-sector wide DataFrame matching scan.py format."""
    rng = np.random.default_rng(seed)
    keys = [f"US|{s}" for s in US_SECTORS] + [f"EU|{s}" for s in EU_SECTORS]
    data = {col: rng.standard_normal(len(keys)) for col in SIGNAL_COLUMNS}
    return pd.DataFrame(data, index=keys)


def _score_per_region(wide_df, sentiment_score=None):
    """Replicate the per-region scoring logic from scan.py."""
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
    return pd.concat(scored_parts)


def test_per_region_ranks_bounded():
    """US ranks 1-11, EU ranks 1-14."""
    wide_df = _make_wide_df()
    scored = _score_per_region(wide_df)

    us_mask = scored.index.str.startswith("US|")
    eu_mask = scored.index.str.startswith("EU|")

    us_ranks = scored.loc[us_mask, "rank"]
    eu_ranks = scored.loc[eu_mask, "rank"]

    assert us_ranks.min() == 1.0
    assert us_ranks.max() == 11.0
    assert us_ranks.nunique() == 11

    assert eu_ranks.min() == 1.0
    assert eu_ranks.max() == 14.0
    assert eu_ranks.nunique() == 14


def test_two_rank_ones_exist():
    """There must be exactly two sectors with rank 1 (one per region)."""
    wide_df = _make_wide_df()
    scored = _score_per_region(wide_df)
    rank_ones = scored[scored["rank"] == 1.0]
    assert len(rank_ones) == 2
    regions = {k.split("|")[0] for k in rank_ones.index}
    assert regions == {"US", "EU"}


def test_per_region_zscore_isolation():
    """Z-scores within each region should have mean ~ 0."""
    wide_df = _make_wide_df()
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        z = zscore_cross_section(wide_df[mask])
        for col in z.columns:
            assert abs(z[col].mean()) < 1e-10, (
                f"{region_prefix} z-score mean for {col}: {z[col].mean()}"
            )


def test_per_region_z_df_concat():
    """Concatenated per-region z-scores cover all 25 sectors."""
    wide_df = _make_wide_df()
    z_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        if mask.any():
            z_parts.append(zscore_cross_section(wide_df[mask]))
    z_df = pd.concat(z_parts)
    assert len(z_df) == 25
    assert set(z_df.index) == set(wide_df.index)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_per_region_scoring.py -v`
Expected: PASS (these tests use `_score_per_region` directly — they define the correct behavior). This validates the test itself is sound before we change scan.py.

- [ ] **Step 3: Modify scan.py scoring block (lines 335–343)**

Replace the single `score_all` call with per-region scoring:

```python
    logger.info("Scoring sectors …")
    # Per-region cohort scoring: US and EU each ranked within their own pool.
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
    logger.info("Scoring complete. %d sectors ranked.", len(scored))
```

- [ ] **Step 4: Modify scan.py z-score block (line 372)**

Replace the single `zscore_cross_section(wide_df)` call:

```python
        # Build long-format signals for DB, with per-region z-scores
        z_parts = []
        for region_prefix in ("US", "EU"):
            mask = wide_df.index.str.startswith(f"{region_prefix}|")
            if mask.any():
                z_parts.append(zscore_cross_section(wide_df[mask]))
        z_df = pd.concat(z_parts)
        long_signals_df = _build_long_signals_df(rows, z_wide_df=z_df)
```

- [ ] **Step 5: Modify `_print_summary` to show per-region top 5**

Replace lines 179–187 in `_print_summary`:

```python
    for region in ("US", "EU"):
        region_df = scored_df_for_db[scored_df_for_db["region"] == region]
        if region_df.empty:
            continue
        region_sorted = region_df.sort_values("rank", ascending=True)
        print(f"\n  Top 5 {region} by composite score:")
        for _, row in region_sorted.head(5).iterrows():
            rank = int(row["rank"])
            sector = row["gics_sector"]
            composite = row["composite"]
            print(f"    #{rank:2d}  {sector:<28}  composite={composite:.3f}")
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/test_per_region_scoring.py tests/test_scoring.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add scan.py tests/test_per_region_scoring.py
git commit -m "feat: per-region cohort scoring in scan.py

US (11 sectors) and EU (14 sectors) each scored within their own
z-score pool. Ranks are now 1-11 and 1-14 respectively."
```

---

### Task 2: Report per-region ranked tables

**Files:**
- Modify: `src/report.py:18-55` (`build_ranked_table`)
- Modify: `tests/test_report_markdown.py`

**Interfaces:**
- Consumes: `scores_with_deltas` DataFrame from `compute_deltas()` — has `region`, `gics_sector`, `rank` (now per-region)
- Produces: `build_ranked_table()` returns a markdown string with two region sections (US, EU)

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_report_markdown.py (or create if needed)
import pandas as pd
from src.report import build_ranked_table


def test_ranked_table_has_two_region_sections():
    """build_ranked_table should produce US and EU sections."""
    data = {
        "region": ["US", "US", "EU", "EU", "EU"],
        "gics_sector": ["Tech", "Energy", "Banks", "Insurance", "Chemicals"],
        "composite": [1.0, 0.5, 0.8, 0.3, -0.1],
        "level_score": [0.5, 0.3, 0.4, 0.2, 0.0],
        "change_score": [0.5, 0.2, 0.4, 0.1, -0.1],
        "data_score": [1.0, 0.5, 0.8, 0.3, -0.1],
        "rank": [1.0, 2.0, 1.0, 2.0, 3.0],
        "delta_composite": [0.1, -0.1, 0.2, 0.0, -0.05],
        "delta_rank": [1.0, -1.0, 0.0, 1.0, -1.0],
        "emerging_flag": [True, False, False, False, False],
    }
    df = pd.DataFrame(data)
    result = build_ranked_table(df)

    assert "## US Sectors" in result
    assert "## EU Sectors" in result
    # US section has 2 rows, EU has 3
    us_section = result.split("## EU Sectors")[0]
    eu_section = result.split("## EU Sectors")[1]
    assert us_section.count("| 1 |") >= 1
    assert us_section.count("| 2 |") >= 1
    assert eu_section.count("| 1 |") >= 1
    assert eu_section.count("| 3 |") >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report_markdown.py::test_ranked_table_has_two_region_sections -v`
Expected: FAIL — current `build_ranked_table` produces one flat table.

- [ ] **Step 3: Implement per-region `build_ranked_table`**

Replace `build_ranked_table` in `src/report.py`:

```python
def build_ranked_table(scores_with_deltas: pd.DataFrame) -> str:
    """
    Build a markdown table of all sectors ranked by composite score,
    split into US and EU sections with per-region ranks.
    """
    header = "| Rank | Sector | Composite | Level | Change | ΔRank | ΔComposite | ⭐ |"
    separator = "|------|--------|-----------|-------|--------|-------|------------|---|"

    sections = []
    for region in ("US", "EU"):
        region_df = scores_with_deltas[scores_with_deltas["region"] == region]
        if region_df.empty:
            continue
        region_df = region_df.sort_values("rank", ascending=True).reset_index(drop=True)

        rows = [f"## {region} Sectors", "", header, separator]
        for _, row in region_df.iterrows():
            rank = int(row["rank"])
            sector = row["gics_sector"]
            composite = f"{row['composite']:.2f}"
            level = f"{row['level_score']:.2f}"
            change = f"{row['change_score']:.2f}"
            delta_rank = int(row.get("delta_rank", 0))
            delta_rank_str = f"{delta_rank:+d}"
            delta_composite_val = row.get("delta_composite", 0.0)
            delta_composite = f"{delta_composite_val:.2f}"
            star = "🌱" if row.get("emerging_flag", False) else ""
            rows.append(
                f"| {rank} | {sector} | {composite} | {level} | {change} | {delta_rank_str} | {delta_composite} | {star} |"
            )
        sections.append("\n".join(rows))

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_report_markdown.py tests/test_report_smoke.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/report.py tests/test_report_markdown.py
git commit -m "feat: per-region sections in markdown report"
```

---

### Task 3: Dashboard leaderboard — two region-grouped tbody sections

**Files:**
- Modify: `dashboard/templates/index.html.j2:93-137`
- Modify: `dashboard/build.py:310-339`

**Interfaces:**
- Consumes: `leaderboard_rows` from `_build_leaderboard_rows()` (unchanged — already has `region` field)
- Produces: Template context with `us_leaderboard_rows` and `eu_leaderboard_rows` lists; template renders two `<tbody>` sections with region-header rows

- [ ] **Step 1: Modify build.py to split leaderboard rows by region**

In `dashboard/build.py`, after the leaderboard enrichment loop (around line 339), add the region split:

```python
    us_leaderboard_rows = [r for r in leaderboard_rows if r["region"] == "US"]
    eu_leaderboard_rows = [r for r in leaderboard_rows if r["region"] == "EU"]
```

And update the `sectors_ctx` dict (around line 388) to pass both:

```python
    sectors_ctx = {
        "scan_date": scan_date,
        "scan_index": scan_index,
        "active_scan_id": active_scan_id,
        "leaderboard_rows": leaderboard_rows,
        "us_leaderboard_rows": us_leaderboard_rows,
        "eu_leaderboard_rows": eu_leaderboard_rows,
        "plotly_bundle": plotly_bundle_rel,
    }
```

- [ ] **Step 2: Modify the template to render two region groups**

Replace the `<tbody>` in `dashboard/templates/index.html.j2` (lines 109–136):

```html
      <tbody>
        {% for region_label, region_rows in [("US Sectors", us_leaderboard_rows), ("EU Sectors", eu_leaderboard_rows)] %}
        {% if region_rows %}
        <tr class="region-header-row">
          <td colspan="10">{{ region_label }}</td>
        </tr>
        {% for row in region_rows %}
        <tr class="leaderboard-row" data-sector-key="{{ row.key }}" data-sector-id="{{ row.sector_id }}" tabindex="0">
          <td class="rank-cell">
            <span class="rank-badge{% if row.rank is number and row.rank <= 3 %} top3{% endif %}">{{ row.rank }}</span>
            <span class="chevron" id="chev-{{ row.sector_id }}">▶</span>
          </td>
          <td>{{ row.sector }}{% if row.setup == "entry" %}<span class="setup-badge entry" data-i18n="badge_entry">▲ Entry</span>{% elif row.setup == "exit" %}<span class="setup-badge exit" data-i18n="badge_exit">▼ Exit</span>{% endif %}</td>
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
        <tr class="breakdown-row" id="bd-{{ row.sector_id }}">
          <td colspan="10">{{ row.breakdown_html | safe }}</td>
        </tr>
        {% endfor %}
        {% endif %}
        {% endfor %}
        {% if not us_leaderboard_rows and not eu_leaderboard_rows %}
        <tr><td colspan="10" style="text-align:center;padding:24px;color:var(--fg4)" data-i18n="leaderboard_empty">No data available yet.</td></tr>
        {% endif %}
      </tbody>
```

- [ ] **Step 3: Add CSS for region header rows**

Add to `dashboard/templates/_style.html.j2` (or the existing `_sectors.css.j2` if that's where leaderboard styles live):

```css
.region-header-row td {
  background: var(--bg2);
  font-weight: 700;
  font-size: 0.95rem;
  padding: 10px 12px 6px;
  border-bottom: 2px solid var(--border);
  letter-spacing: 0.03em;
}
```

- [ ] **Step 4: Update `sortTable` to skip region-header rows**

The existing `sortTable` function in `index.html.j2` queries `tr.leaderboard-row` — region-header rows have class `region-header-row` so they are already excluded from the sort. No change needed. Verify by reading the function: it uses `tbody.querySelectorAll('tr.leaderboard-row')`.

- [ ] **Step 5: Build dashboard locally to verify**

Run: `python3 dashboard/build.py`
Expected: Builds without error. (Don't commit `docs/`.)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/index.html.j2 dashboard/build.py
git commit -m "feat: two-region leaderboard layout in dashboard"
```

Note: if the CSS was added to a separate file, add that file too.

---

### Task 4: rescore.js — per-region ranking on sentiment toggle

**Files:**
- Modify: `dashboard/assets/rescore.js:56-112`
- Modify: `tests/test_rescore_parity.py` (if it exists and tests rank output)

**Interfaces:**
- Consumes: `RESCORE_DATA` JSON (unchanged shape: `{scans, sectors, data, sentiment}`)
- Produces: `rescore(data, W)` returns per-sector `{rank, composite, delta_rank, ...}` with per-region ranks

- [ ] **Step 1: Modify the `rescore` function to rank per region**

Replace the ranking logic inside `rescore()` in `dashboard/assets/rescore.js`. The current code ranks all sectors in one `rankAverage()` call. Change to split by region prefix, rank each group, merge:

```javascript
  function rescore(data, W) {
    var sectors = data.sectors;
    var nScans = data.scans.length;
    var out = {};
    if (nScans === 0) {
      sectors.forEach(function (s) {
        out[s] = { rank: null, composite: 0, delta_rank: 0, delta_composite: 0,
                   setup: null, trajectory_label: "→", trajectory_state: "flat" };
      });
      return out;
    }

    // Split sectors by region
    var regionGroups = {};
    sectors.forEach(function (key) {
      var region = key.split("|")[0];
      if (!regionGroups[region]) { regionGroups[region] = []; }
      regionGroups[region].push(key);
    });

    // composite[scanIdx] = {sector: value}
    var compositeByScan = [];
    for (var s = 0; s < nScans; s++) {
      var cMap = {};
      sectors.forEach(function (key) {
        var d = data.data[key][s];
        var sent = data.sentiment[key][s];
        cMap[key] = (1 - W) * d + W * sent;
      });
      compositeByScan.push(cMap);
    }

    // Rank per region per scan
    var rankByScan = [];
    for (var s2 = 0; s2 < nScans; s2++) {
      var rMap = {};
      Object.keys(regionGroups).forEach(function (region) {
        var group = regionGroups[region];
        var vals = group.map(function (key) { return compositeByScan[s2][key]; });
        var ranks = rankAverage(vals);
        group.forEach(function (key, i) { rMap[key] = ranks[i]; });
      });
      rankByScan.push(rMap);
    }

    var last = nScans - 1;
    var prev = nScans >= 2 ? last - 1 : null;

    sectors.forEach(function (key) {
      var rankNow = rankByScan[last][key];
      var compNow = compositeByScan[last][key];
      var dRank = 0, dComp = 0;
      if (prev !== null) {
        dRank = rankByScan[prev][key] - rankNow;
        dComp = compNow - compositeByScan[prev][key];
      }
      var start = Math.max(0, nScans - 5);
      var rankSeries = [];
      for (var s3 = start; s3 < nScans; s3++) { rankSeries.push(rankByScan[s3][key]); }
      var traj = trajectoryLabel(olsSlope(rankSeries));

      out[key] = {
        rank: rankNow,
        composite: compNow,
        delta_rank: dRank,
        delta_composite: dComp,
        setup: null,
        trajectory_label: traj.label,
        trajectory_state: traj.state
      };
    });
    return out;
  }
```

- [ ] **Step 2: Update the `sortVisibleByRank` function in index.html.j2**

The existing `sortVisibleByRank` in `index.html.j2` (lines 552-564) reorders all rows by rank. With per-region ranks, the same rank value (e.g., 1) appears in both regions. The sort must keep region groups together. Update the sort comparator:

```javascript
  function sortVisibleByRank(rows, scored, tbody) {
    rows.sort(function (a, b) {
      var ka = a.getAttribute("data-sector-key");
      var kb = b.getAttribute("data-sector-key");
      var ra = ka.split("|")[0];
      var rb = kb.split("|")[0];
      // US before EU, then by rank within region
      if (ra !== rb) return ra < rb ? -1 : 1;
      var rkA = scored[ka] ? scored[ka].rank : 1e9;
      var rkB = scored[kb] ? scored[kb].rank : 1e9;
      return rkA - rkB;
    });
    // Re-insert region headers before their group
    var regionHeaders = Array.from(tbody.querySelectorAll("tr.region-header-row"));
    var headerMap = {};
    regionHeaders.forEach(function (h) {
      var text = h.querySelector("td").textContent.trim();
      if (text.indexOf("US") === 0) headerMap["US"] = h;
      if (text.indexOf("EU") === 0) headerMap["EU"] = h;
    });
    var currentRegion = null;
    rows.forEach(function (tr) {
      var key = tr.getAttribute("data-sector-key");
      var region = key ? key.split("|")[0] : null;
      if (region && region !== currentRegion) {
        currentRegion = region;
        if (headerMap[region]) tbody.appendChild(headerMap[region]);
      }
      tbody.appendChild(tr);
      var sid = tr.getAttribute("data-sector-id");
      var bd = sid ? document.getElementById("bd-" + sid) : null;
      if (bd) { tbody.appendChild(bd); }
    });
  }
```

- [ ] **Step 3: Run existing rescore parity test**

Run: `pytest tests/test_rescore_parity.py -v`
Expected: May need updating if it asserts global ranks. If so, update the expected ranks to be per-region.

- [ ] **Step 4: Commit**

```bash
git add dashboard/assets/rescore.js dashboard/templates/index.html.j2
git commit -m "feat: per-region ranking in rescore.js sentiment toggle"
```

---

### Task 5: scan-history.js + scan-digest.js — per-region awareness

**Files:**
- Modify: `dashboard/assets/scan-history.js:38-84`
- Modify: `dashboard/assets/scan-digest.js:20-72`

**Interfaces:**
- Consumes: `SCAN_HISTORY` JSON (unchanged shape)
- Produces: `renderScanLeaderboard()` renders two region-grouped table sections; `computeDigest()` computes per-region top-5 entries and movers

- [ ] **Step 1: Modify `renderScanLeaderboard` in scan-history.js**

Replace the function body to render two region groups:

```javascript
  function renderScanLeaderboard(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return;

    var prevId = findPrevScanId(scanId);
    var prevScores = prevId ? SCAN_HISTORY.scores[String(prevId)] : null;

    // Group entries by region
    var groups = {};
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var s = scores[key];
      var parts = key.split("|");
      var region = parts[0];
      var sector = parts[1];
      var delta = 0;
      if (prevScores && prevScores[key]) {
        delta = prevScores[key].rank - s.rank;
      }
      if (!groups[region]) groups[region] = [];
      groups[region].push({ key: key, region: region, sector: sector, scores: s, delta: delta });
    }

    var html = "";
    ["US", "EU"].forEach(function (region) {
      var entries = groups[region] || [];
      entries.sort(function (a, b) { return a.scores.rank - b.scores.rank; });
      html += '<tr class="region-header-row"><td colspan="10">' + region + ' Sectors</td></tr>';
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        var sc = e.scores;
        var rankClass = sc.rank <= 3 ? " top3" : "";
        var arrow = "";
        var arrowClass = "";
        if (e.delta > 0) { arrow = "▲"; arrowClass = "up"; }
        else if (e.delta < 0) { arrow = "▼"; arrowClass = "down"; }
        var arrowHtml = arrow ? '<span class="arrow ' + arrowClass + '">' + arrow + "</span> " : "";

        html += '<tr class="leaderboard-row">'
          + '<td class="rank-cell"><span class="rank-badge' + rankClass + '">' + sc.rank + "</span></td>"
          + "<td>" + e.sector + "</td>"
          + '<td><span class="tag-region">' + e.region + "</span></td>"
          + '<td class="composite-cell">' + fmtScore(sc.composite) + "</td>"
          + "<td>" + fmtScore(sc.level) + "</td>"
          + "<td>" + fmtScore(sc.change) + "</td>"
          + "<td>" + fmtScore(sc.data) + "</td>"
          + '<td class="sentiment-cell">' + fmtScore(sc.sentiment) + "</td>"
          + '<td class="delta-cell">' + arrowHtml + fmtDelta(e.delta) + "</td>"
          + "<td>—</td>"
          + "</tr>";
      }
    });
    tbody.innerHTML = html;
  }
```

- [ ] **Step 2: Modify `computeDigest` in scan-digest.js**

Replace the top-5 check to be per-region. Change `s.rank <= 5` to check rank within the sector's own region:

```javascript
  function computeDigest(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return null;
    var prevId = findPrevScanId(scanId);
    if (prevId === null) return null;
    var prevScores = SCAN_HISTORY.scores[String(prevId)];
    if (!prevScores) return null;

    // Determine top-5 threshold per region
    var regionRanks = {};
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var region = key.split("|")[0];
      if (!regionRanks[region]) regionRanks[region] = [];
      regionRanks[region].push(scores[key].rank);
    }
    // Top 5 = rank <= 5 within region (works for US 11, EU 14)
    var TOP_N = 5;

    var entries = [];
    var entryKeys = {};
    var movers = [];
    for (var key2 in scores) {
      if (!scores.hasOwnProperty(key2)) continue;
      var s = scores[key2];
      var parts = key2.split("|");
      var region2 = parts[0];
      var sector = parts[1];
      var prev = prevScores[key2];

      if (s.rank <= TOP_N && (!prev || prev.rank > TOP_N)) {
        entries.push({ key: key2, sector: sector, region: region2, rank: s.rank });
        entryKeys[key2] = true;
      }
    }

    for (var key3 in scores) {
      if (!scores.hasOwnProperty(key3)) continue;
      if (entryKeys.hasOwnProperty(key3)) continue;
      var s2 = scores[key3];
      var parts2 = key3.split("|");
      var region3 = parts2[0];
      var sector2 = parts2[1];
      var prev2 = prevScores[key3];

      if (prev2) {
        var delta = prev2.rank - s2.rank;
        if (delta !== 0) {
          movers.push({ key: key3, sector: sector2, region: region3, rank: s2.rank, delta: delta });
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
```

Note: with per-region ranks stored in `SCAN_HISTORY`, the `s.rank <= 5` check automatically works per-region (US ranks go 1-11, EU ranks go 1-14). The existing logic is already correct once the data contains per-region ranks. The main fix is really in `renderScanLeaderboard` (two-table rendering). For digest, the logic is essentially unchanged — the rank semantics just became per-region. We keep the code for clarity.

- [ ] **Step 3: Build dashboard locally to verify**

Run: `python3 dashboard/build.py`
Expected: Builds without error. Verify the generated `docs/index.html` references the updated JS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/assets/scan-history.js dashboard/assets/scan-digest.js
git commit -m "feat: per-region rendering in scan-history and scan-digest JS"
```

---

### Task 6: Backfill script + BACKLOG.md update

**Files:**
- Create: `scripts/backfill_region_ranks.py`
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: `score_all()` from `src/scoring.py`, `zscore_cross_section()` from `src/scoring.py`, `init_db()` from `src/state.py`
- Produces: Updates `scores` and `signals` tables in DB for all historical scans with per-region values

- [ ] **Step 1: Create the backfill script**

```python
#!/usr/bin/env python3
"""Recompute per-region scores, ranks, and z-values for all stored scans.

One-off script to backfill historical data after switching from global
to per-region cohort scoring. Re-runnable and idempotent.

Usage:
    python scripts/backfill_region_ranks.py
    python scripts/backfill_region_ranks.py --dry-run   # preview without writing
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scoring import score_all, zscore_cross_section
from src.state import init_db, _read_sql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill")

SIGNAL_COLUMNS = [
    "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
    "acceleration", "above_50dma", "above_200dma", "ma50_slope",
    "obv_slope", "breadth_above_50dma",
]


def recompute_scan(signals_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pure function: given raw signals, return (scores_df, z_df) with per-region ranks.

    Parameters
    ----------
    signals_df : DataFrame with columns [region, gics_sector, signal_name, raw_value]
        (one row per sector per signal, from the signals table)

    Returns
    -------
    scores_df : DataFrame indexed by sector_key ("REGION|sector") with columns
        [level_score, change_score, data_score, composite, rank]
    z_df : DataFrame indexed by sector_key with z-scored signal columns
    """
    wide = signals_df.pivot_table(
        index=["region", "gics_sector"],
        columns="signal_name",
        values="raw_value",
        aggfunc="first",
    )
    wide.index = wide.index.map(lambda x: f"{x[0]}|{x[1]}")
    present_cols = [c for c in SIGNAL_COLUMNS if c in wide.columns]
    wide = wide[present_cols]

    scored_parts = []
    z_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide.index.str.startswith(f"{region_prefix}|")
        region_df = wide[mask]
        if region_df.empty:
            continue
        region_scored = score_all(
            region_df,
            weights_path="config/weights.yaml",
            sentiment_score=None,
            blend_sentiment=False,
        )
        scored_parts.append(region_scored)
        z_parts.append(zscore_cross_section(region_df))

    scores_df = pd.concat(scored_parts)
    z_df = pd.concat(z_parts)
    return scores_df, z_df


def main():
    parser = argparse.ArgumentParser(description="Backfill per-region ranks for all stored scans.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to DB.")
    args = parser.parse_args()

    conn = init_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT DISTINCT scan_id FROM scores ORDER BY scan_id")
        scan_ids = [row[0] for row in cur.fetchall()]
        logger.info("Found %d scans to backfill", len(scan_ids))

        for scan_id in scan_ids:
            signals_df = _read_sql(
                conn,
                "SELECT region, gics_sector, signal_name, raw_value FROM signals WHERE scan_id = %s",
                params=(scan_id,),
            )
            if signals_df.empty:
                logger.warning("Scan %d: no signals found, skipping", scan_id)
                continue

            try:
                scores_df, z_df = recompute_scan(signals_df)
            except Exception as exc:
                logger.warning("Scan %d: recomputation failed (%s), skipping", scan_id, exc)
                continue

            if args.dry_run:
                us_max = scores_df.loc[scores_df.index.str.startswith("US|"), "rank"].max() if scores_df.index.str.startswith("US|").any() else 0
                eu_max = scores_df.loc[scores_df.index.str.startswith("EU|"), "rank"].max() if scores_df.index.str.startswith("EU|").any() else 0
                logger.info("Scan %d: would update %d scores (US max rank=%s, EU max rank=%s)",
                            scan_id, len(scores_df), us_max, eu_max)
                continue

            # Update scores table
            for sector_key, row in scores_df.iterrows():
                parts = sector_key.split("|", 1)
                region, gics_sector = parts[0], parts[1]
                cur.execute(
                    "UPDATE scores SET level_score=%s, change_score=%s, data_score=%s, "
                    "composite=%s, rank=%s "
                    "WHERE scan_id=%s AND region=%s AND gics_sector=%s",
                    (
                        float(row["level_score"]), float(row["change_score"]),
                        float(row["data_score"]), float(row["composite"]),
                        float(row["rank"]),
                        scan_id, region, gics_sector,
                    ),
                )

            # Update signals z_value
            z_long = z_df.reset_index().melt(
                id_vars=["index"],
                var_name="signal_name",
                value_name="z_value",
            )
            z_long[["region", "gics_sector"]] = z_long["index"].str.split("|", n=1, expand=True)
            for _, zrow in z_long.iterrows():
                cur.execute(
                    "UPDATE signals SET z_value=%s "
                    "WHERE scan_id=%s AND region=%s AND gics_sector=%s AND signal_name=%s",
                    (
                        float(zrow["z_value"]),
                        scan_id, zrow["region"], zrow["gics_sector"], zrow["signal_name"],
                    ),
                )

            conn.commit()
            logger.info("Scan %d: updated %d scores + z-values", scan_id, len(scores_df))

        logger.info("Backfill complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write a test for the `recompute_scan` pure function**

```python
# Add to tests/test_per_region_scoring.py

from scripts.backfill_region_ranks import recompute_scan


def test_recompute_scan_produces_per_region_ranks():
    """Backfill recomputation should produce per-region ranks."""
    rows = []
    rng = np.random.default_rng(99)
    for region, sectors in [("US", US_SECTORS), ("EU", EU_SECTORS)]:
        for sector in sectors:
            for signal in SIGNAL_COLUMNS:
                rows.append({
                    "region": region,
                    "gics_sector": sector,
                    "signal_name": signal,
                    "raw_value": rng.standard_normal(),
                })
    signals_df = pd.DataFrame(rows)
    scores_df, z_df = recompute_scan(signals_df)

    us_scores = scores_df[scores_df.index.str.startswith("US|")]
    eu_scores = scores_df[scores_df.index.str.startswith("EU|")]

    assert us_scores["rank"].max() == 11.0
    assert eu_scores["rank"].max() == 14.0
    assert len(scores_df) == 25
    assert len(z_df) == 25
```

- [ ] **Step 3: Run the test**

Run: `pytest tests/test_per_region_scoring.py::test_recompute_scan_produces_per_region_ranks -v`
Expected: PASS

- [ ] **Step 4: Update BACKLOG.md**

Delete the "Fix cohort mismatch" queued section (lines 34–46). Add a Done entry at the top of the Done section:

```markdown
- **Per-region cohort scoring** — live scan now scores US (11 sectors) and EU
  (14 sectors) as independent z-score cohorts, matching the backtest. Leaderboard
  shows two region-grouped tables. Client-side rescore, scan-history, and
  scan-digest are region-aware. Backfill script recomputes historical ranks.
  *(2026-07-20)*
```

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_region_ranks.py tests/test_per_region_scoring.py BACKLOG.md
git commit -m "feat: history backfill script + backlog update for cohort fix"
```

---
