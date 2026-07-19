# EU Sub-Sector Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two untradeable EU equal-weight composite sectors with their five underlying STOXX sub-sector ETFs as first-class sectors (EU: 11 → 14 sectors), with parent-GICS mapping for sentiment and Swedish-ticker matching.

**Architecture:** Config-driven split — `config/universe.yaml` lists 14 single-ticker EU entries; composite-building code in `src/pipeline.py` is deleted as dead code; a new tiny `src/sector_map.py` module makes the existing (currently unconsumed) `stoxx_to_gics` map in `config/sector_map.yaml` live config, consumed by the FinBERT sentiment application in `scan.py` and Swedish-ticker matching in `src/report.py`.

**Tech Stack:** Python 3, pandas, PyYAML, pytest. No new dependencies.

**Spec:** `design/specs/2026-07-18-eu-subsector-split-design.md`

## Global Constraints

- The five new EU sector names are exactly: `Banks`, `Financial Services`, `Insurance`, `Basic Resources`, `Chemicals` (must match `config/sector_map.yaml` `stoxx_to_gics` keys verbatim).
- New EU tickers: Banks=`EXV1.DE`, Financial Services=`EXH2.DE`, Insurance=`EXH5.DE`, Basic Resources=`EXV6.DE`, Chemicals=`EXV7.DE`. US universe unchanged.
- `eu_sectors` values become plain scalars — list support is removed everywhere (pipeline, scan ticker collection, tests).
- Parent-map lookups never raise; unmapped names resolve to themselves (identity fallback). A missing/malformed `sector_map.yaml` raises at load time.
- No schema changes. No `git add docs/` on this branch (CI owns `docs/`).
- Conventional commits, subject < 72 chars.
- Run only the test files named in each step, not the whole suite — CI runs the full suite on push.
- BACKLOG.md hygiene ships in this branch (delete Queued section, Done entry at top of Done) — Task 4.

---

## File Structure

- **Create** `src/sector_map.py` — parent-map loader + lookup (one responsibility, no I/O beyond the YAML read).
- **Create** `tests/test_sector_map.py` — loader/lookup tests.
- **Modify** `config/universe.yaml` — 14 scalar EU entries.
- **Modify** `config/sector_etfs.yaml` — EU drill-down re-keyed to the new sector names.
- **Modify** `src/pipeline.py` — delete `build_composite_series` + composite branch.
- **Modify** `scan.py` — drop `_flatten`; apply FinBERT via parent map; emit news signal rows per universe sector name.
- **Modify** `src/data/news_sentiment.py` — two new pure helpers (`apply_polarity_to_keys`, `build_news_signal_rows`) so the scan-step logic is unit-testable.
- **Modify** `src/report.py` — Swedish-ticker matching through parent map.
- **Modify/Delete tests** — `tests/test_pipeline_composite.py` (delete), `tests/test_pipeline.py` (drop composite tests), `tests/test_news_sentiment.py` + `tests/test_report_markdown.py` (new cases).
- **Modify** `BACKLOG.md`, `CLAUDE.md` — backlog hygiene + project-overview line.

---

### Task 1: Parent-sector map module

**Files:**
- Create: `src/sector_map.py`
- Test: `tests/test_sector_map.py`

**Interfaces:**
- Consumes: `config/sector_map.yaml` (existing file; `stoxx_to_gics` mapping already contains all five new names).
- Produces: `load_parent_map(path: str = "config/sector_map.yaml") -> dict[str, str]` and `parent_sector(name: str, parent_map: dict[str, str]) -> str`. Tasks 3 and 4 import both from `src.sector_map`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sector_map.py`:

```python
# tests/test_sector_map.py
import pytest

from src.sector_map import load_parent_map, parent_sector


def test_load_parent_map_reads_repo_config():
    pmap = load_parent_map()
    assert pmap["Banks"] == "Financials"
    assert pmap["Financial Services"] == "Financials"
    assert pmap["Insurance"] == "Financials"
    assert pmap["Basic Resources"] == "Materials"
    assert pmap["Chemicals"] == "Materials"


def test_parent_sector_identity_fallback():
    pmap = {"Banks": "Financials"}
    assert parent_sector("Banks", pmap) == "Financials"
    assert parent_sector("Technology", pmap) == "Technology"
    assert parent_sector("Utilities", {}) == "Utilities"


def test_load_parent_map_missing_key_raises(tmp_path):
    bad = tmp_path / "sector_map.yaml"
    bad.write_text("gics_sectors: [Technology]\n")
    with pytest.raises(KeyError):
        load_parent_map(str(bad))


def test_load_parent_map_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_parent_map(str(tmp_path / "nope.yaml"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sector_map.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.sector_map'`

- [ ] **Step 3: Write the module**

Create `src/sector_map.py`:

```python
# src/sector_map.py
"""Sub-sector → GICS parent mapping.

Makes ``stoxx_to_gics`` in config/sector_map.yaml live config: EU STOXX
sub-sectors (Banks, Chemicals, …) resolve to their GICS-11 parent for
consumers that only know GICS names (FinBERT news sentiment, Swedish
ticker matching). Names without a mapping resolve to themselves, so US
and unchanged EU sectors pass through untouched.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_parent_map(path: str = "config/sector_map.yaml") -> dict[str, str]:
    """Load the sub-sector → GICS parent map. Raises on missing/malformed file."""
    with Path(path).open() as fh:
        cfg = yaml.safe_load(fh)
    return dict(cfg["stoxx_to_gics"])


def parent_sector(name: str, parent_map: dict[str, str]) -> str:
    """Resolve a sector name to its GICS parent; unmapped names map to themselves."""
    return parent_map.get(name, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sector_map.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/sector_map.py tests/test_sector_map.py
git commit -m "feat: add sector parent-map loader (stoxx_to_gics becomes live config)"
```

---

### Task 2: Split the universe, delete composite code

**Files:**
- Modify: `config/universe.yaml` (eu_sectors block, ~lines 19-36)
- Modify: `config/sector_etfs.yaml` (EU section comment + Financials + Materials blocks, ~lines 100-140 and ~185-200)
- Modify: `src/pipeline.py:31-63` (delete `build_composite_series`), `src/pipeline.py:189-213` (EU loop)
- Modify: `scan.py:314-328` (`_flatten` removal)
- Delete: `tests/test_pipeline_composite.py`
- Modify: `tests/test_pipeline.py` (drop composite import + 2 tests)

**Interfaces:**
- Consumes: nothing from Task 1 (independent).
- Produces: `universe["eu_sectors"]` is `dict[str, str]` (scalar tickers, 14 entries). `build_signals_rows` signature unchanged. Tasks 3+ can assume scalar-only eu_sectors.

- [ ] **Step 1: Update `config/universe.yaml`**

Replace the whole EU block (comment lines + `eu_sectors:` mapping) with:

```yaml
# European sector proxies (STOXX Europe 600 sector UCITS ETFs via iShares/Amundi)
# Using Yahoo Finance tickers for iShares STOXX Europe 600 sector ETFs.
# Banks / Financial Services / Insurance and Basic Resources / Chemicals are
# standalone STOXX sub-sectors (former GICS composites, split 2026-07-18);
# config/sector_map.yaml stoxx_to_gics maps them to GICS parents where a
# GICS-level view is needed (news sentiment, Swedish ticker matching).
eu_sectors:
  Technology: EXV3.DE
  Banks: EXV1.DE
  Financial Services: EXH2.DE
  Insurance: EXH5.DE
  Energy: EXH1.DE
  Health Care: EXV4.DE
  Industrials: EXH4.DE
  Consumer Discretionary: EXH7.DE
  Consumer Staples: EXH3.DE
  Utilities: EXH9.DE
  Basic Resources: EXV6.DE
  Chemicals: EXV7.DE
  Real Estate: IPRP.L
  Communication Services: EXV2.DE
```

- [ ] **Step 2: Re-key `config/sector_etfs.yaml` EU section**

In the EU section header comment, delete these two lines:

```yaml
  # Composites: Financials (Banks + Financial Services + Insurance) and Materials
  # (Basic Resources + Chemicals) are equal-weight blends of the listed components.
```

Replace the `Financials:` block (three entries under one key) with three keys:

```yaml
  Banks:
    - ticker: EXV1.DE
      name: iShares STOXX Europe 600 Banks
      isin: DE000A0F5UJ7
      ter: "0.47%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0F5UJ7

  Financial Services:
    - ticker: EXH2.DE
      name: iShares STOXX Europe 600 Financial Services
      isin: DE000A0H08G5
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08G5

  Insurance:
    - ticker: EXH5.DE
      name: iShares STOXX Europe 600 Insurance
      isin: DE000A0H08K7
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08K7
```

Replace the `Materials:` block (two entries under one key) with two keys:

```yaml
  Basic Resources:
    - ticker: EXV6.DE
      name: iShares STOXX Europe 600 Basic Resources
      isin: DE000A0F5UK5
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0F5UK5

  Chemicals:
    - ticker: EXV7.DE
      name: iShares STOXX Europe 600 Chemicals
      isin: DE000A0H08E0
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08E0
```

- [ ] **Step 3: Delete composite code in `src/pipeline.py`**

Delete the entire `build_composite_series` function (lines 31-63, including its docstring). Then replace the EU loop inside `build_signals_rows` — currently:

```python
    # EU sectors
    for gics_sector, value in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        tickers = value if isinstance(value, list) else [value]
        if len(tickers) == 1:
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker=tickers[0], benchmark_ticker=eu_benchmark, prices=prices,
                rs_momentum_fast=rs_fast,
            )
        else:
            comp = build_composite_series(tickers, prices)
            if comp is None:
                logger.warning("Skipping EU %s — no composite data for %s", gics_sector, tickers)
                continue
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker="+".join(tickers), benchmark_ticker=eu_benchmark,
                prices=prices, sector_df=comp, rs_momentum_fast=rs_fast,
            )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)
```

with (same shape as the US loop above it):

```python
    # EU sectors
    for gics_sector, ticker in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        sig = compute_signals_for_sector(
            sector_key=sector_key,
            region="EU",
            gics_sector=gics_sector,
            sector_ticker=ticker,
            benchmark_ticker=eu_benchmark,
            prices=prices,
            rs_momentum_fast=rs_fast,
        )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)
```

Also check whether `sector_df` is still a used parameter of `compute_signals_for_sector` after this change (`grep -n "sector_df" src/ tests/ scan.py -r`). If the composite path was its only caller, remove the parameter and its handling too.

- [ ] **Step 4: Simplify ticker collection in `scan.py`**

Replace (currently at ~lines 313-328):

```python
    us_sectors: dict[str, str] = universe.get("us_sectors", {})
    eu_sectors: dict[str, str | list[str]] = universe.get("eu_sectors", {})
    us_benchmark: str = universe["us_benchmark"]
    eu_benchmark: str = universe["eu_benchmark"]

    def _flatten(values) -> list[str]:
        out: list[str] = []
        for v in values:
            out.extend(v if isinstance(v, list) else [v])
        return out

    all_tickers: list[str] = (
        _flatten(us_sectors.values())
        + _flatten(eu_sectors.values())
        + [us_benchmark, eu_benchmark]
    )
```

with:

```python
    us_sectors: dict[str, str] = universe.get("us_sectors", {})
    eu_sectors: dict[str, str] = universe.get("eu_sectors", {})
    us_benchmark: str = universe["us_benchmark"]
    eu_benchmark: str = universe["eu_benchmark"]

    all_tickers: list[str] = (
        list(us_sectors.values())
        + list(eu_sectors.values())
        + [us_benchmark, eu_benchmark]
    )
```

- [ ] **Step 5: Remove composite tests**

- Delete the file `tests/test_pipeline_composite.py` (`git rm tests/test_pipeline_composite.py`). Every test in it exercises composite blending or str/list equivalence, both now removed behaviours.
- In `tests/test_pipeline.py`: remove `build_composite_series` from the import block (line 11), and delete the two tests `test_build_composite_series_averages_rebased_close` and `test_build_composite_series_returns_none_for_empty` (~lines 219-241).

- [ ] **Step 6: Run the touched test files**

Run: `pytest tests/test_pipeline.py tests/test_scan_smoke.py tests/test_validation.py tests/test_backtest_engine.py -v`
Expected: all pass (scan smoke fixtures already use scalar eu_sectors; validation accepts scalars).

- [ ] **Step 7: Commit**

```bash
git add config/universe.yaml config/sector_etfs.yaml src/pipeline.py scan.py tests/test_pipeline.py
git rm tests/test_pipeline_composite.py
git commit -m "feat: split EU composite sectors into 5 standalone STOXX sub-sectors"
```

---

### Task 3: FinBERT sentiment through the parent map

**Files:**
- Modify: `src/data/news_sentiment.py` (add two pure helpers at module level, after `zscore_polarity`)
- Modify: `scan.py` step 8d (~lines 495-540)
- Test: `tests/test_news_sentiment.py` (append new tests)

**Interfaces:**
- Consumes: `load_parent_map()`, `parent_sector(name, parent_map)` from `src.sector_map` (Task 1); scalar `us_sectors`/`eu_sectors` dicts in `scan.py` scope (Task 2).
- Produces:
  - `apply_polarity_to_keys(sentiment_score: pd.Series, finbert_z: dict[str, float], parent_map: dict[str, str]) -> pd.Series` — returns a copy of `sentiment_score` where each `REGION|Sector` key whose parent sector has a non-NaN z-score is overwritten with that score.
  - `build_news_signal_rows(finbert_scores: dict[str, dict], universe: dict, parent_map: dict[str, str]) -> list[dict]` — rows `{"region", "gics_sector", "signal_name", "value"}` for every sector name actually in the universe (US + EU), with values looked up via the parent map. Sub-sectors inherit their parent's numbers; names whose parent wasn't scored produce no rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_news_sentiment.py`:

```python
import math

import pandas as pd

from src.data.news_sentiment import apply_polarity_to_keys, build_news_signal_rows

_PMAP = {"Banks": "Financials", "Insurance": "Financials", "Chemicals": "Materials"}


def test_apply_polarity_maps_subsector_to_parent_score():
    idx = ["US|Financials", "EU|Banks", "EU|Insurance", "EU|Technology"]
    base = pd.Series(0.0, index=idx)
    z = {"Financials": 1.5, "Technology": -0.5}
    out = apply_polarity_to_keys(base, z, _PMAP)
    assert out["US|Financials"] == 1.5
    assert out["EU|Banks"] == 1.5          # inherited from parent Financials
    assert out["EU|Insurance"] == 1.5
    assert out["EU|Technology"] == -0.5    # identity fallback
    assert base["EU|Banks"] == 0.0         # input not mutated


def test_apply_polarity_skips_nan_and_unscored():
    base = pd.Series(0.0, index=["EU|Banks", "EU|Chemicals"])
    z = {"Financials": float("nan")}       # Materials absent entirely
    out = apply_polarity_to_keys(base, z, _PMAP)
    assert out["EU|Banks"] == 0.0
    assert out["EU|Chemicals"] == 0.0


def test_build_news_signal_rows_emits_universe_names():
    universe = {
        "us_sectors": {"Financials": "XLF"},
        "eu_sectors": {"Banks": "EXV1.DE", "Technology": "EXV3.DE"},
    }
    scores = {
        "Financials": {"mean_polarity": 0.2, "count": 10,
                       "positive_pct": 60.0, "negative_pct": 20.0},
    }
    rows = build_news_signal_rows(scores, universe, _PMAP)
    keys = {(r["region"], r["gics_sector"]) for r in rows}
    # Financials scored: US|Financials (identity) and EU|Banks (via parent) emit;
    # EU|Technology's parent (Technology) wasn't scored -> no rows.
    assert keys == {("US", "Financials"), ("EU", "Banks")}
    banks = {r["signal_name"]: r["value"] for r in rows if r["gics_sector"] == "Banks"}
    assert banks == {"news_polarity": 0.2, "news_count": 10.0,
                     "news_positive_pct": 60.0, "news_negative_pct": 20.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_sentiment.py -v -k "apply_polarity or signal_rows"`
Expected: FAIL with `ImportError: cannot import name 'apply_polarity_to_keys'`

- [ ] **Step 3: Implement the helpers**

Add to `src/data/news_sentiment.py` (after `zscore_polarity`; module already imports `math`):

```python
from src.sector_map import parent_sector


def apply_polarity_to_keys(
    sentiment_score: "pd.Series",
    finbert_z: dict[str, float],
    parent_map: dict[str, str],
) -> "pd.Series":
    """Overwrite per-key sentiment with FinBERT z-scores, resolving sub-sectors
    to their GICS parent (identity fallback). Returns a copy; NaN/unscored
    parents leave the existing value untouched."""
    out = sentiment_score.copy()
    for key in out.index:
        _, _, sector = key.partition("|")
        parent = parent_sector(sector, parent_map)
        z = finbert_z.get(parent)
        if z is not None and not math.isnan(z):
            out[key] = z
    return out


def build_news_signal_rows(
    finbert_scores: dict[str, dict],
    universe: dict,
    parent_map: dict[str, str],
) -> list[dict]:
    """Info-only news signal rows keyed by the universe's actual sector names.
    Sub-sectors inherit their GICS parent's numbers; sectors whose parent has
    no headline scores emit nothing."""
    rows: list[dict] = []
    for region, cfg_key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for name in universe.get(cfg_key, {}):
            sc = finbert_scores.get(parent_sector(name, parent_map))
            if sc is None:
                continue
            rows.extend([
                {"region": region, "gics_sector": name,
                 "signal_name": "news_polarity", "value": sc["mean_polarity"]},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_count", "value": float(sc["count"])},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_positive_pct", "value": sc["positive_pct"]},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_negative_pct", "value": sc["negative_pct"]},
            ])
    return rows
```

Note: `pd` may only be imported under `TYPE_CHECKING` in this module — check the top of the file; the string annotations avoid a hard import either way. If `pandas` is already imported normally, drop the quotes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_sentiment.py -v`
Expected: all pass (old + 3 new)

- [ ] **Step 5: Wire into `scan.py` step 8d**

In the step-8d block, replace the manual loop:

```python
            if _live_finbert >= 2:
                for key in sentiment_score.index:
                    _region, _, _sector = key.partition("|")
                    if _sector in _finbert_z and not math.isnan(_finbert_z[_sector]):
                        sentiment_score[key] = _finbert_z[_sector]
                logger.info("sentiment_score overwritten with FinBERT polarity z-scores")
```

with:

```python
            if _live_finbert >= 2:
                sentiment_score = apply_polarity_to_keys(
                    sentiment_score, _finbert_z, _parent_map,
                )
                logger.info("sentiment_score overwritten with FinBERT polarity z-scores")
```

and replace the `_finbert_signal_rows` double loop (the `for _sector, _sc in _finbert_scores.items(): for _region in ("US", "EU"): …` block) with:

```python
            _finbert_signal_rows = build_news_signal_rows(
                _finbert_scores, universe, _parent_map,
            )
```

Update the import inside the `try:` to include the new names:

```python
            from src.data.news_sentiment import (
                fetch_news_headlines, score_headlines, zscore_polarity,
                apply_polarity_to_keys, build_news_signal_rows,
            )
```

Load the parent map once near the top of `main()` right after `universe` is loaded (Step 4 area, where `us_sectors`/`eu_sectors` are read):

```python
    from src.sector_map import load_parent_map
    _parent_map = load_parent_map()
```

(Follow the file's existing import style — if top-level imports are the norm in `scan.py`, put `from src.sector_map import load_parent_map` with the other top-level imports instead.)

- [ ] **Step 6: Run the scan smoke tests**

Run: `pytest tests/test_scan_smoke.py tests/test_news_sentiment.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/data/news_sentiment.py scan.py tests/test_news_sentiment.py
git commit -m "feat: apply FinBERT sentiment to EU sub-sectors via GICS parent map"
```

---

### Task 4: Swedish-ticker matching, backlog, docs

**Files:**
- Modify: `src/report.py` (`build_swedish_overlay`, ~lines 103-145)
- Modify: `BACKLOG.md`, `CLAUDE.md`
- Test: `tests/test_report_markdown.py` (append one test)

**Interfaces:**
- Consumes: `load_parent_map()`, `parent_sector()` from `src.sector_map` (Task 1).
- Produces: nothing consumed downstream; final task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report_markdown.py` (reuse the file's existing fixture style for `scores_with_deltas` — a DataFrame with `region`, `gics_sector`, `composite`, `rank` columns; check the top of the file and mirror it):

```python
def test_swedish_overlay_matches_subsector_via_parent(tmp_path):
    csv = tmp_path / "swedish_tickers.csv"
    csv.write_text(
        "ticker,name,gics_sector,market_cap_bn_sek,exchange\n"
        "SEB-A.ST,SEB A,Financials,300,Nasdaq Stockholm\n"
    )
    scores = pd.DataFrame([
        {"region": "EU", "gics_sector": "Banks", "composite": 2.0, "rank": 1},
    ])
    md = build_swedish_overlay(scores, swedish_tickers_path=str(csv), top_n=1)
    assert "Banks (EU)" in md        # displayed under the sub-sector's own name
    assert "SEB-A.ST" in md          # matched via parent Financials
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report_markdown.py -v -k subsector`
Expected: FAIL — the overlay skips Banks (no direct `gics_sector == "Banks"` rows in the CSV), so `"SEB-A.ST" in md` asserts False.

- [ ] **Step 3: Implement parent-mapped matching in `src/report.py`**

Add the import at the top of the file:

```python
from src.sector_map import load_parent_map, parent_sector
```

In `build_swedish_overlay`, load the map once after reading the CSV:

```python
    tickers_df = pd.read_csv(tickers_path)
    parent_map = load_parent_map()
```

and change the matching line inside the loop from:

```python
        matching = tickers_df[tickers_df["gics_sector"] == sector].copy()
```

to:

```python
        matching = tickers_df[
            tickers_df["gics_sector"] == parent_sector(sector, parent_map)
        ].copy()
```

The displayed heading keeps using `sector` (the sub-sector's own name) — only the CSV lookup goes through the parent.

- [ ] **Step 4: Run the report tests**

Run: `pytest tests/test_report_markdown.py tests/test_report_smoke.py -v`
Expected: all pass

- [ ] **Step 5: Backlog + docs hygiene**

In `BACKLOG.md`: delete the entire Queued section `## Split EU composite sectors into standalone sectors (research)` (heading through its final paragraph, including the trailing `---`-adjacent blank lines, leaving Queued's other sections intact). Then insert at the **top of Done** (directly under the `# Done` heading, above the auth entry):

```markdown
- **Split EU composite sectors into standalone sectors** — the two untradeable
  equal-weight EU composites replaced by their STOXX sub-sector ETFs as
  first-class sectors: Financials → Banks (EXV1.DE) + Financial Services
  (EXH2.DE) + Insurance (EXH5.DE); Materials → Basic Resources (EXV6.DE) +
  Chemicals (EXV7.DE). EU universe 11 → 14 sectors; composite-building code
  removed from the pipeline. `config/sector_map.yaml` `stoxx_to_gics` became
  live config (`src/sector_map.py`): FinBERT news sentiment and Swedish-ticker
  matching resolve sub-sectors to their GICS parent (identity fallback).
  Research basis (3y daily): Basic Resources↔Chemicals correlation 0.50 with
  37% 6m-momentum sign disagreement; Financials components ~0.70 with ~15pp
  median best-vs-worst momentum spread — the blends were averaging away the
  signal the scanner exists to find. *(2026-07-18)*
```

In `CLAUDE.md`, update the Project overview line from:

```
Sector momentum scanner: US SPDR + STOXX Europe 600 sectors → GICS 11 → data-pillar signals → composite score → Supabase/Postgres snapshots → static dashboard (GitHub Pages).
```

to:

```
Sector momentum scanner: US SPDR (GICS 11) + STOXX Europe 600 sectors (14, incl. standalone sub-sectors) → data-pillar signals → composite score → Supabase/Postgres snapshots → static dashboard (GitHub Pages).
```

- [ ] **Step 6: Commit**

```bash
git add src/report.py tests/test_report_markdown.py BACKLOG.md CLAUDE.md
git commit -m "feat: match Swedish tickers for sub-sectors via GICS parent"
```

---

## Verification after all tasks

- `pytest` (full suite) — CI runs it on push; run locally only if CI is unavailable.
- Optional local sanity build: `python3 dashboard/build.py` renders without errors against the existing DB (old composite keys still in history — leaderboard must render the latest scan's keys only). Do **not** commit `docs/`.
- Post-merge (manual, next scan): first scan produces 25 sector rows (11 US + 14 EU); check the dashboard shows Banks / Financial Services / Insurance / Basic Resources / Chemicals as separate EU rows with sentiment populated.
