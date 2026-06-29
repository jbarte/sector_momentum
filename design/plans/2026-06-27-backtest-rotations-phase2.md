# Backtest Phase 2 — Rotation Event-Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For a curated set of historical sector rotations, visualize whether the scanner's rank for the sector climbed *before* the price ran — rank-over-time overlaid on the sector ETF's indexed price.

**Architecture:** A config-driven event-study that reuses the Phase-1 point-in-time `replay.score_as_of`. A new `src/backtest/rotations.py` builds per-rotation rank/price series; `results.py` persists them in `summary.json`; `backtest.py` computes them; the dashboard renders dual-axis small-multiples in the existing Backtest tab. No scoring/composite change.

**Tech Stack:** Python 3, pandas, PyYAML, Plotly (existing), pytest.

## Global Constraints

- **Reuse, don't re-derive:** use `replay.score_as_of(universe, prices, as_of, region)` (returns a frame indexed by `region|sector` with `composite` and `rank`) and `replay.month_end_dates(index)`.
- **Price-pillars only; no composite change** — this is read-only analysis (`score_as_of` already calls `score_all(..., blend_sentiment=False)`).
- **Visual-only** — no quantified lead/lag metric.
- **Graceful skips:** a rotation with an unknown sector, missing ETF price, or < 2 valid month-ends is skipped (logged), never fatal. Missing `config/rotations.yaml` → `load_rotations` returns `[]`.
- **`docs/` is CI-owned** — do not commit `docs/` from this branch.
- **Commit style:** conventional commits, subject < 72 chars; end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Sector names in `config/rotations.yaml` must match `config/universe.yaml` exactly.

## File structure

- `config/rotations.yaml` (new) — curated rotations.
- `src/backtest/rotations.py` (new) — `load_rotations`, `event_study`.
- `src/backtest/results.py` (modify) — `write_results` gains `rotations=None`.
- `backtest.py` (modify) — compute + persist the event-study; `--no-rotations` flag.
- `dashboard/build.py` (modify) — `_build_rotation_figures` + context keys.
- `dashboard/templates/index.html.j2` (modify) — small-multiples + JS render.
- Tests under `tests/`.

---

### Task 1: Rotations config + loader

**Files:**
- Create: `config/rotations.yaml`
- Create: `src/backtest/rotations.py`
- Test: `tests/test_rotations_load.py`

**Interfaces:**
- Produces: `load_rotations(path: str = "config/rotations.yaml") -> list[dict]` — parses the YAML list; returns `[]` if the file is absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rotations_load.py
from src.backtest.rotations import load_rotations


def test_load_rotations_reads_seeded_file():
    rots = load_rotations("config/rotations.yaml")
    assert isinstance(rots, list) and len(rots) >= 1
    r = rots[0]
    assert {"name", "region", "gics_sector", "start", "end"} <= set(r)


def test_load_rotations_missing_file_returns_empty():
    assert load_rotations("config/does_not_exist.yaml") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rotations_load.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.backtest.rotations'`.

- [ ] **Step 3: Create the seeded config**

```yaml
# config/rotations.yaml
# Curated historical sector rotations for the backtest event-study.
# gics_sector must match config/universe.yaml exactly. Dates: YYYY-MM-DD.
- name: "Energy 2021–22"
  region: US
  gics_sector: Energy
  start: "2021-01-01"
  end: "2022-06-30"
- name: "Technology / AI 2023"
  region: US
  gics_sector: Technology
  start: "2022-10-01"
  end: "2023-12-31"
- name: "Utilities defensive 2022"
  region: US
  gics_sector: Utilities
  start: "2022-01-01"
  end: "2022-09-30"
```

- [ ] **Step 4: Implement `load_rotations`**

```python
# src/backtest/rotations.py
"""Rotation event-study: did the scanner's rank lead the price move?

Reuses the point-in-time replay engine to recover a sector's rank-over-time
across a curated historical window, alongside the sector ETF's indexed price.
"""
from __future__ import annotations

import logging
import os

import pandas as pd
import yaml

from src.backtest.replay import month_end_dates, score_as_of

logger = logging.getLogger(__name__)


def load_rotations(path: str = "config/rotations.yaml") -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return data or []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_rotations_load.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add config/rotations.yaml src/backtest/rotations.py tests/test_rotations_load.py
git commit -m "feat: rotations config + loader for event-study

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `event_study`

**Files:**
- Modify: `src/backtest/rotations.py`
- Test: `tests/test_rotations_event_study.py`

**Interfaces:**
- Consumes: `replay.score_as_of`, `replay.month_end_dates`; `universe["us_sectors"]`/`["eu_sectors"]` ({sector: ticker}); `prices` ({ticker: DataFrame with `Close`}).
- Produces: `event_study(universe: dict, prices: dict[str, pd.DataFrame], rotations: list[dict]) -> list[dict]`. Each output entry: `{"name", "region", "sector", "ticker", "dates": [str], "rank": [float], "composite": [float], "price_indexed": [float]}`. `price_indexed` starts at 100.0. Rotations with unknown sector/ticker, missing price, or < 2 valid month-ends are skipped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rotations_event_study.py
import numpy as np
import pandas as pd
from src.backtest.rotations import event_study


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE", "Health Care": "XLV"},
        "eu_sectors": {}, "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def _prices():
    n = 500
    return {"XLK": _ramp(n, 100, 0.9), "XLE": _ramp(n, 100, 0.2),
            "XLV": _ramp(n, 100, 0.5), "RSP": _ramp(n, 100, 0.4)}


def test_event_study_produces_rank_and_indexed_price():
    rots = [{"name": "Tech run", "region": "US", "gics_sector": "Technology",
             "start": "2019-01-01", "end": "2019-09-30"}]
    out = event_study(_universe(), _prices(), rots)
    assert len(out) == 1
    e = out[0]
    assert e["sector"] == "Technology" and e["ticker"] == "XLK"
    assert len(e["dates"]) >= 2
    assert e["price_indexed"][0] == 100.0
    assert len(e["rank"]) == len(e["dates"])


def test_event_study_skips_unknown_sector():
    rots = [{"name": "Bogus", "region": "US", "gics_sector": "Nonexistent",
             "start": "2019-01-01", "end": "2019-09-30"}]
    assert event_study(_universe(), _prices(), rots) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rotations_event_study.py -v`
Expected: FAIL — `event_study` not defined.

- [ ] **Step 3: Implement `event_study` (append to `src/backtest/rotations.py`)**

```python
def event_study(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    rotations: list[dict],
) -> list[dict]:
    out: list[dict] = []
    for rot in rotations:
        region = rot["region"]
        sector = rot["gics_sector"]
        key = f"{region}|{sector}"
        sector_map = universe.get("us_sectors" if region == "US" else "eu_sectors", {})
        ticker = sector_map.get(sector)
        if not ticker or ticker not in prices:
            logger.warning("Rotation '%s' skipped — no price for %s (%s)", rot.get("name"), sector, ticker)
            continue

        start, end = pd.Timestamp(rot["start"]), pd.Timestamp(rot["end"])
        price_df = prices[ticker]
        calendar = [d for d in month_end_dates(price_df.index) if start <= d <= end]

        dates: list[str] = []
        ranks: list[float] = []
        comps: list[float] = []
        for d in calendar:
            scored = score_as_of(universe, prices, d, region)
            if scored is None or key not in scored.index:
                continue
            dates.append(d.strftime("%Y-%m-%d"))
            ranks.append(float(scored.loc[key, "rank"]))
            comps.append(float(scored.loc[key, "composite"]))

        if len(dates) < 2:
            logger.warning("Rotation '%s' skipped — < 2 valid month-ends in window", rot.get("name"))
            continue

        closes = [float(price_df["Close"][price_df.index <= pd.Timestamp(d)].iloc[-1]) for d in dates]
        base = closes[0]
        price_indexed = [c / base * 100.0 for c in closes] if base else [0.0] * len(closes)

        out.append({
            "name": rot["name"], "region": region, "sector": sector, "ticker": ticker,
            "dates": dates, "rank": ranks, "composite": comps, "price_indexed": price_indexed,
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rotations_event_study.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/rotations.py tests/test_rotations_event_study.py
git commit -m "feat: rotation event-study (rank-over-time vs indexed price)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Persist rotations in results

**Files:**
- Modify: `src/backtest/results.py`
- Test: `tests/test_results_rotations.py`

**Interfaces:**
- Produces: `write_results(tracks, out_dir="backtests", generated_at="", top_n=5, rotations=None) -> str` — adds `"rotations"` (defaults `[]`) to `summary.json`. Existing positional/keyword callers unaffected; existing readers tolerate the new key; old summaries without it still load via `load_summary`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_results_rotations.py
from src.backtest import results


def test_rotations_round_trip(tmp_path):
    out = str(tmp_path / "bt")
    rot = [{"name": "Tech run", "region": "US", "sector": "Technology",
            "ticker": "XLK", "dates": ["2019-01-31", "2019-02-28"],
            "rank": [3.0, 1.0], "composite": [0.1, 0.9], "price_indexed": [100.0, 110.0]}]
    results.write_results({"US": None, "EU": None}, out_dir=out,
                          generated_at="2026-06-27T00:00:00Z", top_n=5, rotations=rot)
    loaded = results.load_summary(out)
    assert loaded["rotations"][0]["sector"] == "Technology"
    assert loaded["rotations"][0]["price_indexed"][0] == 100.0


def test_write_results_without_rotations_defaults_empty(tmp_path):
    out = str(tmp_path / "bt")
    results.write_results({"US": None, "EU": None}, out_dir=out)
    assert results.load_summary(out)["rotations"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_results_rotations.py -v`
Expected: FAIL — `write_results() got an unexpected keyword argument 'rotations'`.

- [ ] **Step 3: Modify `write_results`**

Change the signature and the `summary` dict in `src/backtest/results.py`:

```python
def write_results(tracks: dict, out_dir: str = "backtests",
                  generated_at: str = "", top_n: int = 5,
                  rotations: list | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    summary = {"generated_at": generated_at, "top_n": top_n,
               "tracks": tracks, "rotations": rotations or []}
```

(Leave the rest of the function — the CSV writes and `return summary_path` — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_results_rotations.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/results.py tests/test_results_rotations.py
git commit -m "feat: persist rotation event-study in backtest summary

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire into the CLI

**Files:**
- Modify: `backtest.py` (`_parse_args` and `run`)
- Test: none (covered by existing `tests/test_backtest_cli.py`; live run is the acceptance check)

**Interfaces:**
- Consumes: `load_rotations`, `event_study` (Tasks 1–2), `write_results(rotations=…)` (Task 3).

- [ ] **Step 1: Add the `--no-rotations` flag**

In `backtest.py` `_parse_args`, add alongside the existing arguments:

```python
    p.add_argument("--no-rotations", action="store_true",
                   help="Skip the rotation event-study.")
```

- [ ] **Step 2: Compute + persist the event-study in `run`**

In `backtest.py` `run`, add imports and the event-study between `run_all` and `write_results`:

```python
    from src.backtest.rotations import load_rotations, event_study
```
After `tracks = run_all(universe, prices, top_n=args.top_n)` and before `write_results`:
```python
    rotations_data = []
    if not args.no_rotations:
        rots = load_rotations("config/rotations.yaml")
        rotations_data = event_study(universe, prices, rots)
        logger.info("Rotation event-study: %d/%d rotations produced", len(rotations_data), len(rots))
```
Then pass it to `write_results`:
```python
    path = write_results(tracks, out_dir=args.out,
                         generated_at=datetime.utcnow().isoformat() + "Z",
                         top_n=args.top_n, rotations=rotations_data)
```

- [ ] **Step 3: Verify the CLI still imports/parses**

Run: `pytest tests/test_backtest_cli.py -v`
Expected: PASS (build_ticker_list test unaffected).
Run: `.venv/bin/python backtest.py --help`
Expected: shows `--no-rotations` in the options.

- [ ] **Step 4: (Optional, network) Live run to populate rotations**

Run: `.venv/bin/python backtest.py --start 2015-01-01`
Expected: logs `Rotation event-study: N/3 rotations produced` and `backtests/summary.json` now has a non-empty `rotations` list. Inspect that each entry has `dates`, `rank`, `price_indexed` (starting 100). If offline/rate-limited, skip — the dashboard handles an empty list.

- [ ] **Step 5: Commit (code; include `backtests/` only if the live run ran)**

```bash
git add backtest.py
# git add backtests/   # only if step 4 produced updated results
git commit -m "feat: backtest CLI computes rotation event-study

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Dashboard rotation small-multiples

**Files:**
- Modify: `dashboard/build.py` (`_build_rotation_figures`; extend `_build_backtest_context`; add context key in `main`/`_render`)
- Modify: `dashboard/templates/index.html.j2` (Backtest panel + JS)
- Test: `tests/test_dashboard_rotations.py`

**Interfaces:**
- Consumes: `summary["rotations"]`; module-level `go`, `pio`, `_WARM_PALETTE`.
- Produces: `_build_rotation_figures(summary) -> list[dict]` — per rotation `{"title": str, "fig_json": str}`, dual-axis (rank inverted left, indexed price right). `[]` when no rotations.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_rotations.py
import json
from dashboard.build import _build_rotation_figures


def _summary():
    return {"rotations": [{
        "name": "Energy 2021–22", "region": "US", "sector": "Energy", "ticker": "XLE",
        "dates": ["2021-01-31", "2021-02-28"], "rank": [9.0, 4.0],
        "composite": [-0.5, 0.6], "price_indexed": [100.0, 118.0],
    }]}


def test_build_rotation_figures_dual_axis():
    figs = _build_rotation_figures(_summary())
    assert len(figs) == 1
    parsed = json.loads(figs[0]["fig_json"])
    assert len(parsed["data"]) == 2          # rank + price traces
    assert parsed["layout"]["yaxis"]["autorange"] == "reversed"  # rank inverted


def test_build_rotation_figures_empty_when_none():
    assert _build_rotation_figures({"rotations": []}) == []
    assert _build_rotation_figures(None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_rotations.py -v`
Expected: FAIL — `cannot import name '_build_rotation_figures'`.

- [ ] **Step 3: Add `_build_rotation_figures` to `dashboard/build.py`**

Add after `_build_backtest_figures`:

```python
def _build_rotation_figures(summary) -> list:
    """Per-rotation dual-axis charts: scanner rank (inverted) vs indexed price."""
    if not summary or not summary.get("rotations"):
        return []
    out = []
    for rot in summary["rotations"]:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=rot["dates"], y=rot["rank"], mode="lines+markers", name="Scanner rank",
            yaxis="y", line=dict(color=_WARM_PALETTE[0])))
        fig.add_trace(go.Scatter(
            x=rot["dates"], y=rot["price_indexed"], mode="lines", name="Price (indexed=100)",
            yaxis="y2", line=dict(color=_WARM_PALETTE[3], dash="dash")))
        fig.update_layout(
            title=dict(text=f"{rot['name']} — {rot['sector']} ({rot['region']})",
                       font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Rank (1 = best)", autorange="reversed", gridcolor="#DFD5BE"),
            yaxis2=dict(title="Price (indexed)", overlaying="y", side="right", showgrid=False),
            paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
            legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
            margin=dict(l=50, r=50, t=50, b=50), hovermode="x unified",
        )
        out.append({"title": rot["name"], "fig_json": pio.to_json(fig)})
    return out
```

- [ ] **Step 4: Extend `_build_backtest_context` to emit rotation data**

In `_build_backtest_context`, after `figs = _build_backtest_figures(summary)`, add:
```python
    rot_figs = _build_rotation_figures(summary)
```
and add two keys to its returned dict (note the parse-then-dump, matching `backtest_json`, to avoid double-encoding):
```python
        "rotation_json": _json.dumps([{"title": r["title"], "fig": _json.loads(r["fig_json"])}
                                      for r in rot_figs]),
        "has_rotations": bool(rot_figs),
```

- [ ] **Step 5: Pass the new keys through `main`'s `_render` call**

In `dashboard/build.py main()`, in the `_render(... context=dict(...))` call, alongside `backtest_json=backtest_ctx["backtest_json"]`, add:
```python
            rotation_json=backtest_ctx["rotation_json"],
            has_rotations=backtest_ctx["has_rotations"],
```

- [ ] **Step 6: Add the template markup + JS**

In `dashboard/templates/index.html.j2`, inside `#tab-backtest`, after `<div class="chart-container" id="backtest-chart-EU" ...></div>` and before the `{% endif %}`, add:
```html
  {% if has_rotations %}
  <h3 style="margin:22px 0 6px;font-family:var(--font-display);font-size:15px;color:var(--fg1)">Rotation event-study — did rank lead price?</h3>
  <p class="tab-note">Scanner rank (inverted: up = better) vs the sector ETF's indexed price across each curated rotation.</p>
  <div id="rotation-charts"></div>
  {% endif %}
```
Add the JS var alongside `var BACKTEST_DATA = ...`:
```javascript
var ROTATION_DATA = {{ rotation_json | safe }};
```
Extend `renderBacktest()` (after the existing per-region loop, before its closing brace) to render rotation small-multiples once:
```javascript
  var rc = document.getElementById('rotation-charts');
  if (rc && !rc.dataset.rendered && ROTATION_DATA && ROTATION_DATA.length) {
    rc.dataset.rendered = '1';
    ROTATION_DATA.forEach(function (r, i) {
      var d = document.createElement('div');
      d.className = 'chart-container';
      d.style.height = '360px';
      d.style.marginBottom = '16px';
      d.id = 'rotation-chart-' + i;
      rc.appendChild(d);
      Plotly.newPlot(d, r.fig.data, r.fig.layout, {responsive: true, displayModeBar: true});
    });
  }
```

- [ ] **Step 7: Run tests (new + the context-coverage guard)**

Run: `pytest tests/test_dashboard_rotations.py tests/test_dashboard_js.py -v`
Expected: PASS — including `test_render_context_covers_all_template_js_vars` (now that `rotation_json` is in the `_render` context).

- [ ] **Step 8: Rebuild + verify**

Run: `.venv/bin/python dashboard/build.py`
Expected: build succeeds. If `backtests/summary.json` has rotations, `grep -c "rotation-charts\|ROTATION_DATA" docs/index.html` ≥ 2 and `ROTATION_DATA` is a non-empty array of objects (`[{"title":`). With no rotations, the section is absent and the build still succeeds. Do **not** commit `docs/` (CI-owned).

- [ ] **Step 9: Commit**

```bash
git add dashboard/build.py dashboard/templates/index.html.j2 tests/test_dashboard_rotations.py
git commit -m "feat: dashboard rotation event-study small-multiples

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Backlog hygiene + full suite

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Full suite**

Run: `pytest -q`
Expected: green (existing + 3 new test files).

- [ ] **Step 2: Move the backlog item to Done**

In `BACKLOG.md`, under `## Phase 3 features`, the bullet `- **Backtest against past rotations — Phase 2 (rotation event-study)** …` — remove it from the list and add this entry at the top of `## Done`:
`- ~~Backtest against past rotations (Phase 2 — rotation event-study)~~ — curated rotations in \`config/rotations.yaml\` → \`src/backtest/rotations.py\` recovers each sector's point-in-time rank-over-time vs the ETF's indexed price (reusing \`score_as_of\`); persisted in \`backtests/summary.json\` and rendered as dual-axis small-multiples in the Backtest tab. Visual-only. *(2026-06-27)*`

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: backlog — backtest phase 2 (rotation event-study) shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- `config/rotations.yaml` + `load_rotations` → Task 1. ✓
- `event_study` reusing `score_as_of`, rank + indexed price, graceful skips → Task 2. ✓
- `results.py` `rotations` key, backward-compatible → Task 3. ✓
- CLI computes + persists, `--no-rotations` → Task 4. ✓
- Dashboard dual-axis small-multiples (rank inverted vs price), graceful absence, context-var contract → Task 5. ✓
- Backlog hygiene + full suite → Task 6. ✓
- Out of scope (lead metric, auto-detected/EU rotations, composite change) → not in plan. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `event_study` emits `{name,region,sector,ticker,dates,rank,composite,price_indexed}` (Task 2) → persisted verbatim by `write_results(rotations=…)` (Task 3) → consumed by `_build_rotation_figures` reading `rot["dates"|"rank"|"price_indexed"|"name"|"sector"|"region"]` (Task 5). `rotation_json` added to context (Task 5 Steps 4–5) matches the template var (Step 6). Parse-then-dump mirrors the established `backtest_json` pattern (avoids the double-encoding bug fixed in Phase 1).
