# Sentiment Ranking Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional dashboard toggle + weight field that blends Google Trends sentiment into the leaderboard ranking client-side, while keeping the canonical server composite pure-data.

**Architecture:** `scan.py` computes thin Google Trends sentiment and stores `sentiment_score` per sector, but the stored `composite`/`rank` stay pure-data (via a new `blend_sentiment=False` flag on `score_all`). `dashboard/build.py` ships per-scan × per-sector `{data_score, sentiment_score}` arrays as `RESCORE_DATA`. A pure JS module `dashboard/assets/rescore.js` recomputes composite, rank, deltas, trajectory, and emerging at a chosen weight `W`; inline template JS wires a toggle/weight control to it and re-renders the leaderboard.

**Tech Stack:** Python 3.11, pandas, scipy, PyYAML, pytrends, Jinja2, Plotly, vanilla JS (no framework), Node (for parity test only), pytest.

## Global Constraints

- Never commit directly to `main`; work on branch `feature/sentiment-ranking-toggle` (already created).
- Conventional commits, subject line < 72 chars.
- Secrets only in `.env` (gitignored); never hardcode `DATABASE_URL` or API tokens.
- Run Python via the project venv: `.venv/bin/python`, `.venv/bin/pytest`.
- The dashboard is static HTML on GitHub Pages — no server at runtime; all ranking interactivity is client-side JS.
- Canonical stored composite/rank MUST remain pure-data (data only). Sentiment is stored but never folded into the canonical composite.
- Toggle default OFF (W=0); default weight when ON = 30 (percent). Persist `sentimentEnabled` and `sentimentWeight` in `localStorage`; first-time visitor = OFF / 30.
- The toggle affects the leaderboard tab only. Other tabs (RRG, Movers, History, Data↔Sentiment) stay server-built and carry a one-line note.

---

### Task 1: Add `blend_sentiment` flag to `score_all`

Keeps the canonical composite pure-data while still emitting a populated `sentiment_score` column.

**Files:**
- Modify: `src/scoring.py:134-186` (`score_all`)
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: existing `compute_composite(data_score, sentiment_score=None, data_weight, sentiment_weight)` and `rank_sectors(composite)`.
- Produces: `score_all(signals_df, weights_path="config/weights.yaml", sentiment_score=None, blend_sentiment=True) -> pd.DataFrame` with columns `level_score, change_score, data_score, sentiment_score, composite, rank`. When `blend_sentiment=False`, the `sentiment_score` column is populated from the passed Series but `composite == data_score` (pure data, no sentiment blend) and `rank` is the pure-data rank.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py`:

```python
def test_score_all_blend_sentiment_false_keeps_composite_pure_data():
    import numpy as np
    import pandas as pd
    from src.scoring import score_all, zscore_cross_section, compute_level_score, \
        compute_change_score, compute_data_score

    # Two sectors, distinct signal values so data_score differs
    idx = ["US|Technology", "US|Energy"]
    signals = pd.DataFrame(
        {
            "rs_ratio": [1.0, -1.0], "return_3m": [1.0, -1.0], "return_6m": [1.0, -1.0],
            "above_50dma": [1.0, -1.0], "above_200dma": [1.0, -1.0],
            "rs_momentum": [1.0, -1.0], "acceleration": [1.0, -1.0],
            "ma50_slope": [1.0, -1.0], "obv_slope": [1.0, -1.0],
            "return_1m": [0.0, 0.0], "breadth_above_50dma": [0.0, 0.0],
        },
        index=idx,
    )
    sentiment = pd.Series({"US|Technology": -5.0, "US|Energy": 5.0})  # would flip order if blended

    out = score_all(signals, sentiment_score=sentiment, blend_sentiment=False)

    # sentiment_score column is populated (not NaN)
    assert out.loc["US|Technology", "sentiment_score"] == -5.0
    assert out.loc["US|Energy", "sentiment_score"] == 5.0
    # composite equals data_score exactly (pure data, sentiment NOT blended)
    pd.testing.assert_series_equal(
        out["composite"], out["data_score"], check_names=False
    )
    # Technology (higher data) still ranks 1 despite negative sentiment
    assert out.loc["US|Technology", "rank"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scoring.py::test_score_all_blend_sentiment_false_keeps_composite_pure_data -v`
Expected: FAIL — `score_all() got an unexpected keyword argument 'blend_sentiment'`

- [ ] **Step 3: Implement the flag**

In `src/scoring.py`, change the `score_all` signature and composite computation. Replace the signature line and the composite block:

```python
def score_all(
    signals_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
    sentiment_score: pd.Series | None = None,
    blend_sentiment: bool = True,
) -> pd.DataFrame:
```

Then, where `sentiment_score` is reindexed and composite computed (currently lines ~164-173), replace with:

```python
    # Align sentiment_score index to signals_df; fill gaps with 0.0 (neutral)
    if sentiment_score is not None:
        sentiment_score = sentiment_score.reindex(signals_df.index, fill_value=0.0)

    # Canonical composite blends sentiment only when blend_sentiment is True.
    # When False, sentiment is still stored in the output column but the
    # composite/rank stay pure-data.
    composite = compute_composite(
        data,
        sentiment_score=sentiment_score if blend_sentiment else None,
        data_weight=data_weight if blend_sentiment else 1.0,
        sentiment_weight=sentiment_weight if blend_sentiment else 0.0,
    )
    ranks = rank_sectors(composite)
```

The return DataFrame is unchanged (it already emits `sentiment_score` from the reindexed Series).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_scoring.py::test_score_all_blend_sentiment_false_keeps_composite_pure_data -v`
Expected: PASS

- [ ] **Step 5: Run the full scoring suite for regressions**

Run: `.venv/bin/pytest tests/test_scoring.py tests/test_scoring_smoke.py -q`
Expected: all pass (default `blend_sentiment=True` preserves prior behavior)

- [ ] **Step 6: Commit**

```bash
git add src/scoring.py tests/test_scoring.py
git commit -m "feat: add blend_sentiment flag to score_all"
```

---

### Task 2: Wire thin Google Trends sentiment into `scan.py`

Compute and store real `sentiment_score` each scan; keep canonical composite pure-data.

**Files:**
- Modify: `scan.py` (imports near line 300-313; `main()` scoring block near line 366-387)
- Test: `tests/test_scan_smoke.py`

**Interfaces:**
- Consumes: `fetch_trends(keywords: dict[str, list[str]], cache_dir="data/cache") -> dict[str, pd.Series] | None` from `src.data.trends`; `compute_sentiment_score(reddit_data, trends_data, finnhub_data, sector_keys, us_sectors, eu_sectors) -> pd.Series` from `src.signals.sentiment`; `score_all(..., blend_sentiment=False)` from Task 1.
- Produces: a `sentiment_score` Series indexed by `sector_key` passed into `score_all`; stored `sentiment_score` column in the `scores` table is no longer all-NaN.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scan_smoke.py` (a pure unit test of the helper, no network):

```python
def test_compute_sentiment_for_scan_trends_only_returns_series():
    """scan.py's sentiment helper returns a per-sector Series from Trends only."""
    import pandas as pd
    from scan import _compute_sentiment_for_scan

    keywords = {"Technology": ["AI"], "Energy": ["oil"]}
    sector_keys = ["US|Technology", "US|Energy", "EU|Technology", "EU|Energy"]
    us_sectors = {"Technology": "XLK", "Energy": "XLE"}
    eu_sectors = {"Technology": "EXV3.DE", "Energy": "EXV4.DE"}

    # Trends present for Technology, absent for Energy -> Energy sentiment = 0.0
    trends = {
        "Technology": pd.Series([float(i) for i in range(13)]),  # rising slope
        "Energy": pd.Series([5.0] * 13),                          # flat slope
    }

    result = _compute_sentiment_for_scan(
        trends_data=trends,
        sector_keys=sector_keys,
        us_sectors=us_sectors,
        eu_sectors=eu_sectors,
    )

    assert isinstance(result, pd.Series)
    assert set(result.index) == set(sector_keys)
    # No NaNs in the output (all-NaN sector collapses to 0.0 inside compute_sentiment_score)
    assert not result.isna().any()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scan_smoke.py::test_compute_sentiment_for_scan_trends_only_returns_series -v`
Expected: FAIL — `cannot import name '_compute_sentiment_for_scan' from 'scan'`

- [ ] **Step 3: Add the helper to `scan.py`**

Add near the other module-level helpers in `scan.py` (e.g. after `_build_long_signals_df`):

```python
def _compute_sentiment_for_scan(
    trends_data,
    sector_keys: list[str],
    us_sectors: dict[str, str],
    eu_sectors: dict[str, str],
) -> "pd.Series":
    """Trends-only sentiment score per sector_key.

    Reddit and Finnhub are intentionally disabled (passed as None); only Google
    Trends search momentum feeds sentiment. compute_sentiment_score collapses an
    all-NaN sector to 0.0 (neutral).
    """
    from src.signals.sentiment import compute_sentiment_score

    return compute_sentiment_score(
        reddit_data=None,
        trends_data=trends_data,
        finnhub_data=None,
        sector_keys=sector_keys,
        us_sectors=us_sectors,
        eu_sectors=eu_sectors,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_scan_smoke.py::test_compute_sentiment_for_scan_trends_only_returns_series -v`
Expected: PASS

- [ ] **Step 5: Wire the helper into `main()`**

In `scan.py`, update the scoring import (near line 313):

```python
    from src.scoring import score_all, zscore_cross_section
```
becomes
```python
    from src.scoring import score_all, zscore_cross_section
    from src.data.trends import fetch_trends
```

Then replace the scoring block (currently near lines 380-388):

```python
    # ------------------------------------------------------------------
    # Step 8: Score
    # ------------------------------------------------------------------
    logger.info("Scoring sectors …")
    scored = score_all(
        wide_df,
        weights_path="config/weights.yaml",
    )
    logger.info("Scoring complete. %d sectors ranked.", len(scored))
```

with:

```python
    # ------------------------------------------------------------------
    # Step 8: Sentiment (thin Google Trends) + Score
    # ------------------------------------------------------------------
    logger.info("Fetching Google Trends sentiment …")
    with open("config/sentiment_keywords.yaml", "r") as _fh:
        sentiment_keywords = yaml.safe_load(_fh)
    trends_data = fetch_trends(sentiment_keywords)
    sentiment_score = _compute_sentiment_for_scan(
        trends_data=trends_data,
        sector_keys=list(wide_df.index),
        us_sectors=us_sectors,
        eu_sectors=eu_sectors,
    )

    logger.info("Scoring sectors …")
    # Canonical composite stays pure-data; sentiment is stored but not blended.
    scored = score_all(
        wide_df,
        weights_path="config/weights.yaml",
        sentiment_score=sentiment_score,
        blend_sentiment=False,
    )
    logger.info("Scoring complete. %d sectors ranked.", len(scored))
```

(`us_sectors` / `eu_sectors` are already local variables in `main()` at line 337-338, and `yaml` is already imported at line 33.)

- [ ] **Step 6: Smoke-run the scan against the live DB**

Run: `.venv/bin/python scan.py`
Expected: completes, logs "Fetching Google Trends sentiment …" and "Saved scan_id=…". (Trends may rate-limit and log a warning → sentiment neutral; the scan must still succeed.)

- [ ] **Step 7: Verify sentiment is stored**

Run: `.venv/bin/python stats.py 2>&1 | grep -A2 "Signal completeness" ; .venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from src.state import init_db
conn = init_db()
with conn.cursor() as cur:
    cur.execute(\"SELECT COUNT(*) FROM scores WHERE sentiment_score IS NOT NULL\")
    print('non-null sentiment rows:', cur.fetchone()[0])
conn.close()"`
Expected: non-null sentiment rows > 0 for the latest scan.

- [ ] **Step 8: Commit**

```bash
git add scan.py tests/test_scan_smoke.py
git commit -m "feat: compute and store thin Google Trends sentiment in scan"
```

---

### Task 3: Create the pure `rescore.js` module

The client-side scoring engine. Pure functions, no DOM, testable under Node.

**Files:**
- Create: `dashboard/assets/rescore.js`
- (Test added in Task 4)

**Interfaces:**
- Produces a global/UMD object `Rescore` with:
  - `rankAverage(values: number[]) -> number[]` — descending rank (highest value → rank 1), average tie-break (mirrors `scipy.rankdata(-x, method="average")`).
  - `olsSlope(values: number[]) -> number` — least-squares slope over x=0..n-1; returns 0 for n<2.
  - `trajectoryLabel(slope: number) -> {label: string, state: string}` — thresholds matching `dashboard/build.py:_compute_rank_trajectories`.
  - `rescore(data, W: number) -> { [sectorKey: string]: {rank, composite, delta_rank, delta_composite, emerging, trajectory_label, trajectory_state} }` where `data = {scans, sectors, data, sentiment}` (the `RESCORE_DATA` shape from Task 5).

- [ ] **Step 1: Create `dashboard/assets/rescore.js`**

```javascript
// Pure client-side rescoring for the leaderboard sentiment toggle.
// No DOM access. Mirrors src/scoring.py rank semantics and
// dashboard/build.py:_compute_rank_trajectories OLS thresholds.
(function (root) {
  "use strict";

  // Descending rank: highest value -> rank 1. Average tie-break,
  // mirroring scipy.rankdata(-x, method="average").
  function rankAverage(values) {
    var n = values.length;
    var idx = values.map(function (v, i) { return i; });
    // Sort indices by value DESCENDING
    idx.sort(function (a, b) { return values[b] - values[a]; });
    var ranks = new Array(n);
    var i = 0;
    while (i < n) {
      var j = i;
      // Group ties (equal values)
      while (j + 1 < n && values[idx[j + 1]] === values[idx[i]]) { j++; }
      // Positions i..j (0-based) -> 1-based ranks i+1..j+1; average them
      var avg = 0;
      for (var k = i; k <= j; k++) { avg += k + 1; }
      avg = avg / (j - i + 1);
      for (var m = i; m <= j; m++) { ranks[idx[m]] = avg; }
      i = j + 1;
    }
    return ranks;
  }

  // Least-squares slope over x = 0..n-1. Returns 0 for n < 2.
  function olsSlope(values) {
    var n = values.length;
    if (n < 2) { return 0.0; }
    var xMean = (n - 1) / 2.0;
    var yMean = values.reduce(function (a, b) { return a + b; }, 0) / n;
    var num = 0, den = 0;
    for (var i = 0; i < n; i++) {
      num += (i - xMean) * (values[i] - yMean);
      den += (i - xMean) * (i - xMean);
    }
    return den === 0 ? 0.0 : num / den;
  }

  // Trajectory thresholds match _compute_rank_trajectories in build.py.
  // Negative slope = rank improving (climbing toward 1).
  function trajectoryLabel(slope) {
    if (slope <= -1.5) { return { label: "↑↑", state: "strong_up" }; }
    if (slope <= -0.3) { return { label: "↑", state: "up" }; }
    if (slope < 0.3)   { return { label: "→", state: "flat" }; }
    if (slope < 1.5)   { return { label: "↓", state: "down" }; }
    return { label: "↓↓", state: "strong_down" };
  }

  // data = {scans:[{scan_id,run_at}], sectors:[key], data:{key:[..]}, sentiment:{key:[..]}}
  // Returns per-sector result for the LATEST scan.
  function rescore(data, W) {
    var sectors = data.sectors;
    var nScans = data.scans.length;
    var out = {};
    if (nScans === 0) {
      sectors.forEach(function (s) {
        out[s] = { rank: null, composite: 0, delta_rank: 0, delta_composite: 0,
                   emerging: false, trajectory_label: "→", trajectory_state: "flat" };
      });
      return out;
    }

    // composite[scanIdx] = {sector: value}; ranks[scanIdx] = {sector: rank}
    var compositeByScan = [];
    var rankByScan = [];
    for (var s = 0; s < nScans; s++) {
      var vals = sectors.map(function (key) {
        var d = data.data[key][s];
        var sent = data.sentiment[key][s];
        return (1 - W) * d + W * sent;
      });
      var ranks = rankAverage(vals);
      var cMap = {}, rMap = {};
      sectors.forEach(function (key, i) { cMap[key] = vals[i]; rMap[key] = ranks[i]; });
      compositeByScan.push(cMap);
      rankByScan.push(rMap);
    }

    var last = nScans - 1;
    var prev = nScans >= 2 ? last - 1 : null;

    sectors.forEach(function (key) {
      var rankNow = rankByScan[last][key];
      var compNow = compositeByScan[last][key];
      var dRank = 0, dComp = 0;
      if (prev !== null) {
        dRank = rankByScan[prev][key] - rankNow;          // + = climbed
        dComp = compNow - compositeByScan[prev][key];
      }
      // Trajectory: OLS slope over last up-to-5 scans' ranks
      var start = Math.max(0, nScans - 5);
      var rankSeries = [];
      for (var s2 = start; s2 < nScans; s2++) { rankSeries.push(rankByScan[s2][key]); }
      var traj = trajectoryLabel(olsSlope(rankSeries));

      out[key] = {
        rank: rankNow,
        composite: compNow,
        delta_rank: dRank,
        delta_composite: dComp,
        emerging: dRank > 0 && dComp > 0,
        trajectory_label: traj.label,
        trajectory_state: traj.state
      };
    });
    return out;
  }

  var api = { rankAverage: rankAverage, olsSlope: olsSlope,
              trajectoryLabel: trajectoryLabel, rescore: rescore };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.Rescore = api;
})(typeof window !== "undefined" ? window : this);
```

- [ ] **Step 2: Sanity-check it loads under Node**

Run: `node -e "const R=require('./dashboard/assets/rescore.js'); console.log(R.rankAverage([3,1,2]))"`
Expected: `[ 1, 3, 2 ]`

- [ ] **Step 3: Commit**

```bash
git add dashboard/assets/rescore.js
git commit -m "feat: add pure rescore.js client scoring module"
```

---

### Task 4: Node-vs-Python parity test for `rescore.js`

Guards against the JS scoring drifting from the Python reference.

**Files:**
- Create: `tests/test_rescore_parity.py`

**Interfaces:**
- Consumes: `dashboard/assets/rescore.js` (Task 3) via Node subprocess; `scipy.stats.rankdata` for the Python reference.
- Produces: nothing (test only).

- [ ] **Step 1: Write the parity test**

Create `tests/test_rescore_parity.py`:

```python
"""Parity test: rescore.js (run under Node) must match a Python reference
using scipy.rankdata and the same OLS slope as _compute_rank_trajectories."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import rankdata

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _py_reference(data, W):
    sectors = data["sectors"]
    n = len(data["scans"])
    comp_by_scan, rank_by_scan = [], []
    for s in range(n):
        vals = np.array([(1 - W) * data["data"][k][s] + W * data["sentiment"][k][s]
                         for k in sectors])
        ranks = rankdata(-vals, method="average")
        comp_by_scan.append(dict(zip(sectors, vals)))
        rank_by_scan.append(dict(zip(sectors, ranks)))
    last = n - 1
    prev = last - 1 if n >= 2 else None
    out = {}
    for k in sectors:
        rank_now = rank_by_scan[last][k]
        comp_now = comp_by_scan[last][k]
        d_rank = (rank_by_scan[prev][k] - rank_now) if prev is not None else 0.0
        d_comp = (comp_now - comp_by_scan[prev][k]) if prev is not None else 0.0
        start = max(0, n - 5)
        series = [rank_by_scan[s][k] for s in range(start, n)]
        slope = _ols(series)
        out[k] = {
            "rank": rank_now, "composite": comp_now,
            "delta_rank": d_rank, "delta_composite": d_comp,
            "emerging": bool(d_rank > 0 and d_comp > 0),
            "trajectory_label": _traj(slope)[0],
            "trajectory_state": _traj(slope)[1],
        }
    return out


def _ols(values):
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return 0.0 if den == 0 else num / den


def _traj(slope):
    if slope <= -1.5:
        return "↑↑", "strong_up"
    if slope <= -0.3:
        return "↑", "up"
    if slope < 0.3:
        return "→", "flat"
    if slope < 1.5:
        return "↓", "down"
    return "↓↓", "strong_down"


def _run_js(data, W):
    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const data = {json.dumps(data)};
        process.stdout.write(JSON.stringify(R.rescore(data, {W})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def _make_data(n_scans, sectors, seed):
    rng = np.random.default_rng(seed)
    return {
        "scans": [{"scan_id": i + 1, "run_at": f"2026-06-{i+1:02d}T00:00:00"} for i in range(n_scans)],
        "sectors": sectors,
        "data": {k: rng.normal(size=n_scans).round(4).tolist() for k in sectors},
        "sentiment": {k: rng.normal(size=n_scans).round(4).tolist() for k in sectors},
    }


@pytest.mark.parametrize("W", [0.0, 0.30, 1.0])
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_rescore_parity_random(W, seed):
    sectors = [f"US|S{i}" for i in range(6)] + [f"EU|S{i}" for i in range(6)]
    data = _make_data(8, sectors, seed)
    js = _run_js(data, W)
    py = _py_reference(data, W)
    for k in sectors:
        assert js[k]["rank"] == pytest.approx(py[k]["rank"], abs=1e-6)
        assert js[k]["composite"] == pytest.approx(py[k]["composite"], abs=1e-6)
        assert js[k]["delta_rank"] == pytest.approx(py[k]["delta_rank"], abs=1e-6)
        assert js[k]["emerging"] == py[k]["emerging"]
        assert js[k]["trajectory_label"] == py[k]["trajectory_label"]


def test_rescore_parity_ties():
    # All-equal data -> all ranks tie to the average (n+1)/2
    sectors = ["US|A", "US|B", "US|C", "US|D"]
    data = {
        "scans": [{"scan_id": 1, "run_at": "2026-06-01T00:00:00"}],
        "sectors": sectors,
        "data": {k: [1.0] for k in sectors},
        "sentiment": {k: [0.0] for k in sectors},
    }
    js = _run_js(data, 0.30)
    for k in sectors:
        assert js[k]["rank"] == pytest.approx(2.5, abs=1e-6)  # (1+2+3+4)/4


def test_rescore_w0_equals_data_only_order():
    # At W=0 the ranking equals ranking by data_score alone.
    sectors = ["US|A", "US|B", "US|C"]
    data = {
        "scans": [{"scan_id": 1, "run_at": "2026-06-01T00:00:00"}],
        "sectors": sectors,
        "data": {"US|A": [2.0], "US|B": [1.0], "US|C": [3.0]},
        "sentiment": {"US|A": [9.0], "US|B": [9.0], "US|C": [-9.0]},  # ignored at W=0
    }
    js = _run_js(data, 0.0)
    assert js["US|C"]["rank"] == 1.0  # highest data
    assert js["US|A"]["rank"] == 2.0
    assert js["US|B"]["rank"] == 3.0
```

- [ ] **Step 2: Run the parity test**

Run: `.venv/bin/pytest tests/test_rescore_parity.py -v`
Expected: all parametrized cases PASS (or SKIP if node missing).

- [ ] **Step 3: Commit**

```bash
git add tests/test_rescore_parity.py
git commit -m "test: node-vs-python parity for rescore.js"
```

---

### Task 5: Build and embed `RESCORE_DATA`; ship `rescore.js`

Server builds the per-scan × per-sector arrays and copies the JS module into `docs/assets/`.

**Files:**
- Modify: `dashboard/build.py` (add builder fn near `_build_drilldown_data` ~line 625; call + context in `main()` ~lines 964-1034; asset copy ~line 1007-1014)
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `history_df` (columns include `scan_id, run_at, region, gics_sector, data_score, sentiment_score`).
- Produces: `_build_rescore_data(history_df) -> dict` returning `{"scans": [{"scan_id", "run_at"}], "sectors": [key], "data": {key: [floats]}, "sentiment": {key: [floats]}}`, sorted by `scan_id` ascending, every per-sector array length == `len(scans)`, missing/NaN → `0.0`. Rendered into the template as `var RESCORE_DATA = {{ rescore_data_json | safe }};`. The file `dashboard/assets/rescore.js` is copied to `docs/assets/rescore.js`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dashboard_js.py`:

```python
def test_build_rescore_data_shape():
    import pandas as pd
    from dashboard.build import _build_rescore_data

    rows = []
    for scan_id, run_at in [(1, "2026-06-22T00:00:00"), (2, "2026-06-23T00:00:00")]:
        for region, sector, dscore, sscore in [
            ("US", "Technology", 0.6, 0.2),
            ("EU", "Energy", -0.3, float("nan")),  # NaN sentiment -> 0.0
        ]:
            rows.append({
                "scan_id": scan_id, "run_at": run_at, "region": region,
                "gics_sector": sector, "data_score": dscore, "sentiment_score": sscore,
            })
    df = pd.DataFrame(rows)

    out = _build_rescore_data(df)

    assert [s["scan_id"] for s in out["scans"]] == [1, 2]
    assert set(out["sectors"]) == {"US|Technology", "EU|Energy"}
    # arrays aligned to scans length
    for key in out["sectors"]:
        assert len(out["data"][key]) == 2
        assert len(out["sentiment"][key]) == 2
    # NaN sentiment coerced to 0.0
    assert out["sentiment"]["EU|Energy"] == [0.0, 0.0]
    assert out["data"]["US|Technology"] == [0.6, 0.6]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard_js.py::test_build_rescore_data_shape -v`
Expected: FAIL — `cannot import name '_build_rescore_data'`

- [ ] **Step 3: Implement `_build_rescore_data` in `dashboard/build.py`**

Add this function just above `_build_drilldown_data` (line ~625):

```python
def _build_rescore_data(history_df) -> dict:
    """Per-scan × per-sector data_score and sentiment_score arrays for the
    client-side leaderboard rescoring. Arrays are aligned to the ascending
    scan list; missing / NaN values become 0.0."""
    if history_df.empty:
        return {"scans": [], "sectors": [], "data": {}, "sentiment": {}}

    df = history_df.copy()
    df["sector_key"] = df["region"] + "|" + df["gics_sector"]

    scan_ids = sorted(df["scan_id"].unique().tolist())
    scans_meta = []
    for sid in scan_ids:
        run_at = df[df["scan_id"] == sid]["run_at"].iloc[0]
        scans_meta.append({"scan_id": int(sid), "run_at": str(run_at)})

    sectors = sorted(df["sector_key"].unique().tolist())

    def _series(col: str) -> dict:
        result = {}
        for key in sectors:
            sk = df[df["sector_key"] == key].set_index("scan_id")
            vals = []
            for sid in scan_ids:
                v = sk[col].get(sid) if sid in sk.index else None
                fv = _safe_float(v)
                vals.append(fv if fv is not None else 0.0)
            result[key] = vals
        return result

    return {
        "scans": scans_meta,
        "sectors": sectors,
        "data": _series("data_score"),
        "sentiment": _series("sentiment_score"),
    }
```

(`_safe_float` already exists in `build.py` at line ~137.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dashboard_js.py::test_build_rescore_data_shape -v`
Expected: PASS

- [ ] **Step 5: Call the builder and add to render context in `main()`**

In `dashboard/build.py main()`, after the sentiment scatter line (~973):

```python
    logger.info("Building sentiment scatter …")
    sentiment_scatter_json = _build_sentiment_scatter_figure(history_df)
```
add:
```python
    logger.info("Building rescore data …")
    rescore_data_json = json.dumps(_build_rescore_data(history_df))
```

Then in the `context=dict(...)` block (~line 1031, after `sentiment_scatter_json=...`):

```python
            sentiment_scatter_json=sentiment_scatter_json,
            rescore_data_json=rescore_data_json,
```

- [ ] **Step 6: Copy `rescore.js` into `docs/assets/`**

In `main()`, find the plotly copy block (~line 1007-1014):

```python
    import shutil
    docs_assets = out_dir / "assets"
    docs_assets.mkdir(exist_ok=True)
    plotly_src = _ASSETS_DIR / "plotly.min.js"
    if plotly_src.exists():
        shutil.copy2(plotly_src, docs_assets / "plotly.min.js")
    plotly_bundle_rel = "assets/plotly.min.js"
```
add after the plotly copy, before `plotly_bundle_rel = ...`:
```python
    rescore_src = _ASSETS_DIR / "rescore.js"
    if rescore_src.exists():
        shutil.copy2(rescore_src, docs_assets / "rescore.js")
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/build.py tests/test_dashboard_js.py
git commit -m "feat: build RESCORE_DATA and ship rescore.js to docs"
```

---

### Task 6: Template — control, data var, script include, tab note

Wire `RESCORE_DATA` and the toggle control into the template (no JS behavior yet — that is Task 7). This task makes the page render with the new var present and valid.

**Files:**
- Modify: `dashboard/templates/index.html.j2`
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `rescore_data_json` context var (Task 5); `plotly_bundle` rel path pattern already in template.
- Produces: `var RESCORE_DATA = {{ rescore_data_json | safe }};` in the inline script; `<script src="assets/rescore.js"></script>` include; a `#sentiment-control` bar with `#sentiment-toggle` checkbox and `#sentiment-weight` number input above the leaderboard table; a `.tab-note` line on the non-leaderboard tabs.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dashboard_js.py` (reuses the existing `_render` + mock pattern at the bottom of the file — extend the mock context with `rescore_data_json`):

```python
def test_rendered_template_includes_rescore_data_and_control(tmp_path):
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-06-23",
            leaderboard_rows=[],
            rrg_data_json=_make_mock_plotly_json(),
            drilldown_data=json.dumps({}),
            sector_keys=[],
            movers_json=_make_mock_plotly_json(),
            history_json=_make_mock_plotly_json(),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            signals_list=[],
            plotly_bundle="assets/plotly.min.js",
        ),
    )
    html = out.read_text()
    assert "var RESCORE_DATA =" in html
    assert 'assets/rescore.js' in html
    assert 'id="sentiment-toggle"' in html
    assert 'id="sentiment-weight"' in html
    # no empty JS var assignments
    assert not re.compile(r"var\s+\w+\s*=\s*;").findall(html)
```

Also add `rescore_data_json` to the existing `test_rendered_template_has_no_empty_js_vars` context dict so it keeps passing.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard_js.py::test_rendered_template_includes_rescore_data_and_control -v`
Expected: FAIL — assertions on missing `RESCORE_DATA` / control markup.

- [ ] **Step 3: Add the control bar above the leaderboard table**

In `dashboard/templates/index.html.j2`, immediately before the leaderboard `<table>` (the one whose rows use `leaderboard-row`), insert:

```html
<div id="sentiment-control" class="sentiment-control">
  <label>
    <input type="checkbox" id="sentiment-toggle"> Include sentiment in ranking
  </label>
  <span class="sw-weight">
    Weight: <input type="number" id="sentiment-weight" min="0" max="100" step="1" value="30" disabled>%
  </span>
</div>
```

- [ ] **Step 4: Add the data var and script include**

In the inline `<script>` block, next to the other `var X = {{ ... | safe }};` declarations (e.g. after `var SENTIMENT_DATA = {{ sentiment_scatter_json | safe }};`), add:

```html
var RESCORE_DATA = {{ rescore_data_json | safe }};
```

Where Plotly is included via `<script src="{{ plotly_bundle }}"></script>`, add right after it:

```html
<script src="assets/rescore.js"></script>
```

- [ ] **Step 5: Add a note to the non-leaderboard tab panels**

In each of the RRG, Movers, History, and Data↔Sentiment tab panels (`#tab-rrg`, `#tab-movers`, `#tab-history`, `#tab-sentiment`), add near the top of the panel:

```html
<p class="tab-note">Sentiment weighting affects the leaderboard ranking only.</p>
```

- [ ] **Step 6: Add minimal CSS for the control and note**

In the `<style>` block, add:

```css
.sentiment-control { display:flex; align-items:center; gap:16px; margin:8px 0 12px; font-size:0.9em; color:#3E392B; }
.sentiment-control input[type=number] { width:52px; }
.sentiment-control .sw-weight[data-disabled="true"] { opacity:0.45; }
.tab-note { font-size:0.8em; color:#8C8370; margin:0 0 8px; }
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_dashboard_js.py -v`
Expected: PASS (new test + existing guards).

- [ ] **Step 8: Commit**

```bash
git add dashboard/templates/index.html.j2 tests/test_dashboard_js.py
git commit -m "feat: add sentiment toggle control and RESCORE_DATA to template"
```

---

### Task 7: Template JS — wire toggle to `rescore` and re-render leaderboard

The behavior: read localStorage, compute W, call `Rescore.rescore`, repaint the leaderboard and breakdown score-tree.

**Files:**
- Modify: `dashboard/templates/index.html.j2` (inline `<script>` + add data attributes to leaderboard row cells)
- Verified manually via the preview workflow + a build-time smoke check.

**Interfaces:**
- Consumes: global `Rescore` (Task 3), `RESCORE_DATA` (Task 5), the control inputs `#sentiment-toggle` / `#sentiment-weight` (Task 6).
- Produces: leaderboard DOM updates. Requires each leaderboard row and breakdown to expose stable hooks: `data-sector-key` on the row `<tr class="leaderboard-row">`, and class names on the cells it updates (`.rank-cell`, `.composite-cell`, `.delta-cell`, `.traj-badge`, `.emerging-badge`), plus the breakdown score-tree composite/data/sentiment value+weight spans (`.st-composite-val`, `.st-data-wt`, `.st-data-val`, `.st-sent-wt`, `.st-sent-val`).

- [ ] **Step 1: Add stable data hooks to the leaderboard row markup**

In `index.html.j2`, on the leaderboard row `<tr>`, ensure it carries `data-sector-key="{{ row.key }}"` and that the cells the JS updates have the classes above. For the rank cell:

```html
<tr class="leaderboard-row" data-sector-key="{{ row.key }}" onclick="toggleBreakdown('{{ row.sector_id }}')">
  <td class="rank-cell">{{ row.rank }} <span class="chevron" id="chev-{{ row.sector_id }}">▶</span></td>
  <td>{{ row.sector }}{% if row.emerging %}<span class="emerging-badge">⬆ Emerging</span>{% endif %}</td>
  ...
  <td class="composite-cell">{{ row.composite }}</td>
  ...
  <td class="delta-cell"><span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {{ row.delta_rank }}</td>
  <td><span class="traj-badge traj-{{ row.trajectory_state }}">{{ row.trajectory_label }}</span></td>
</tr>
```

(Match the existing column order — only add the class names and `data-sector-key`; do not reorder columns. Inspect the current row markup and add classes to the matching `<td>`s.)

- [ ] **Step 2: Add score-tree hooks in the breakdown HTML (build.py)**

The score-tree is server-rendered in `dashboard/build.py:_build_breakdown_html`. Add a Sentiment row and class hooks so JS can update values. Replace the score-tree string (lines ~297-328) so the composite value, the Data weight/value, and a new Sentiment weight/value carry spans:

```python
    tree = (
        f'<div class="score-tree" data-sector-key="{_html.escape(sector_key)}">'
        f'<div class="st-row st-top">'
        f'<span class="st-label">Composite</span>'
        f'<span class="st-val st-composite-val">{composite}</span>'
        f'</div>'
        f'<div class="st-row">'
        f'<span class="st-conn">├─</span>'
        f'<span class="st-label">Data Score</span>'
        f'<span class="st-wt st-data-wt">(100%)</span>'
        f'<span class="st-val st-data-val">{data_score}</span>'
        f'</div>'
        f'<div class="st-row st-sub">'
        f'<span class="st-conn">│ ├─</span>'
        f'<span class="st-label">Level</span>'
        f'<span class="st-wt">({level_weight*100:.0f}%)</span>'
        f'<span class="st-val">{level_score}</span>'
        f'<span class="st-meta">5 signals</span>'
        f'</div>'
        f'<div class="st-row st-sub">'
        f'<span class="st-conn">│ └─</span>'
        f'<span class="st-label">Change</span>'
        f'<span class="st-wt">({chg_weight*100:.0f}%)</span>'
        f'<span class="st-val">{change_score}</span>'
        f'<span class="st-meta">4 signals</span>'
        f'</div>'
        f'<div class="st-row">'
        f'<span class="st-conn">└─</span>'
        f'<span class="st-label">Sentiment</span>'
        f'<span class="st-wt st-sent-wt">(0%)</span>'
        f'<span class="st-val st-sent-val">{fv(score_row.get("sentiment_score"))}</span>'
        f'</div>'
        f'</div>'
        f'<div class="bd-footer">'
        f'ETF: {_html.escape(str(ticker))} &middot; '
        f'Benchmark: {_html.escape(str(benchmark))}'
        f'</div>'
    )
```

(The default markup shows Data 100% / Sentiment 0% — consistent with the toggle-off default. JS overwrites these when W changes.)

- [ ] **Step 3: Add the wiring script**

In the inline `<script>` (after `RESCORE_DATA` is defined and after `rescore.js` is included), add:

```javascript
(function () {
  var LS_ENABLED = "sentimentEnabled", LS_WEIGHT = "sentimentWeight";
  var toggle = document.getElementById("sentiment-toggle");
  var weightInput = document.getElementById("sentiment-weight");
  var weightWrap = weightInput ? weightInput.closest(".sw-weight") : null;
  if (!toggle || !weightInput || typeof Rescore === "undefined") { return; }

  function readState() {
    var enabled = false, weight = 30;
    try {
      enabled = localStorage.getItem(LS_ENABLED) === "true";
      var w = parseInt(localStorage.getItem(LS_WEIGHT), 10);
      if (!isNaN(w)) { weight = Math.min(100, Math.max(0, w)); }
    } catch (e) {}
    return { enabled: enabled, weight: weight };
  }

  function fmt(n, d) { return (n === null || n === undefined) ? "—" : Number(n).toFixed(d); }

  function applyRanking() {
    var enabled = toggle.checked;
    var weight = Math.min(100, Math.max(0, parseInt(weightInput.value, 10) || 0));
    var W = enabled ? weight / 100 : 0;
    try {
      localStorage.setItem(LS_ENABLED, enabled ? "true" : "false");
      localStorage.setItem(LS_WEIGHT, String(weight));
    } catch (e) {}
    weightInput.disabled = !enabled;
    if (weightWrap) { weightWrap.setAttribute("data-disabled", String(!enabled)); }

    var scored = Rescore.rescore(RESCORE_DATA, W);

    var tbody = document.querySelector("table tbody"); // leaderboard tbody
    var rows = Array.prototype.slice.call(document.querySelectorAll("tr.leaderboard-row"));

    // Update each row's cells from scored[key]
    rows.forEach(function (tr) {
      var key = tr.getAttribute("data-sector-key");
      var r = scored[key];
      if (!r) { return; }
      var rankCell = tr.querySelector(".rank-cell");
      if (rankCell) {
        var chev = rankCell.querySelector(".chevron");
        rankCell.childNodes[0].nodeValue = (r.rank % 1 === 0 ? r.rank : r.rank.toFixed(1)) + " ";
        if (chev) { rankCell.appendChild(chev); }
      }
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
        var badge = sectorCell.querySelector(".emerging-badge");
        if (r.emerging && !badge) {
          var s = document.createElement("span");
          s.className = "emerging-badge"; s.textContent = "⬆ Emerging";
          sectorCell.appendChild(s);
        } else if (!r.emerging && badge) {
          badge.remove();
        }
      }
      // Update breakdown score-tree for this sector, if present
      var tree = document.querySelector('.score-tree[data-sector-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
      if (tree) {
        var cv = tree.querySelector(".st-composite-val");
        if (cv) { cv.textContent = fmt(r.composite, 3); }
        var dataPct = Math.round((1 - W) * 100), sentPct = Math.round(W * 100);
        var dwt = tree.querySelector(".st-data-wt"); if (dwt) { dwt.textContent = "(" + dataPct + "%)"; }
        var swt = tree.querySelector(".st-sent-wt"); if (swt) { swt.textContent = "(" + sentPct + "%)"; }
      }
    });

    // Re-sort rows by rank ascending and re-attach
    rows.sort(function (a, b) {
      var ra = scored[a.getAttribute("data-sector-key")];
      var rb = scored[b.getAttribute("data-sector-key")];
      return (ra ? ra.rank : 1e9) - (rb ? rb.rank : 1e9);
    });
    if (tbody) {
      rows.forEach(function (tr) {
        tbody.appendChild(tr);
        // keep any sibling breakdown row immediately after its leaderboard row
        var sid = tr.getAttribute("data-sector-key");
        // breakdown row id is bd-<sector_id>; find by matching chevron id base
        var chev = tr.querySelector(".chevron");
        if (chev) {
          var bdId = "bd-" + chev.id.replace("chev-", "");
          var bdRow = document.getElementById(bdId);
          if (bdRow) { tbody.appendChild(bdRow); }
        }
      });
    }
  }

  var init = readState();
  toggle.checked = init.enabled;
  weightInput.value = init.weight;
  toggle.addEventListener("change", applyRanking);
  weightInput.addEventListener("input", applyRanking);
  applyRanking();
})();
```

- [ ] **Step 4: Rebuild the dashboard against the live DB**

Run: `.venv/bin/python dashboard/build.py`
Expected: "Dashboard written to …/docs/index.html". Then verify no broken vars and the data is present:

Run: `grep -c "var RESCORE_DATA = {" docs/index.html ; grep -c 'assets/rescore.js' docs/index.html ; grep -c "var [A-Z_]* = ;" docs/index.html`
Expected: `1`, `1`, `0`.

- [ ] **Step 5: Verify interactivity in the browser preview**

Start the preview server on the repo root, open `docs/index.html`, and:
1. Confirm the leaderboard renders and rows expand on click (no JS errors in console).
2. Check the "Include sentiment in ranking" box → the weight field enables; row order / composite / Δ update.
3. Change the weight to e.g. 60 → ranking updates again; reload page → setting persisted (still checked, 60).
4. Uncheck → returns to pure-data order; reload → stays unchecked.

Use `preview_console_logs` to confirm no errors and `preview_screenshot` to capture the toggled state.

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/index.html.j2 dashboard/build.py
git commit -m "feat: wire sentiment toggle to live leaderboard rescoring"
```

---

### Task 8: Update BACKLOG.md and finalize

Move the completed item and run a final verification.

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Move the Sentiment toggle item to Done**

In `BACKLOG.md`, remove the `## Sentiment toggle` section and add to the `## Done` list:

```markdown
- ~~Sentiment toggle~~ — dashboard toggle + weight field blends Google Trends
  sentiment into the leaderboard ranking client-side; canonical composite stays
  pure-data. Thin Trends wired into the scan; rich Trends tab still pending. *(2026-06-24)*
```

(Leave "Sentiment methodology explanation" and "Sentiment module — Google Trends only, as a dedicated tab" — those are separate, still-pending items.)

- [ ] **Step 2: Final full-suite run**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark sentiment ranking toggle done in backlog"
```

- [ ] **Step 4: Run code review**

Per `CLAUDE.md`, run `/code-review` on the branch, address findings, then push:

```bash
git push -u origin feature/sentiment-ranking-toggle
```

Stop there — Jonas reviews and merges manually.
