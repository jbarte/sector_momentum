# EU Sector Composites (Phase 1: Financials & Materials) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute EU Financials and Materials momentum on an equal-weight *composite* of their STOXX supersector ETFs (Financials = Banks + Financial Services + Insurance; Materials = Basic Resources + Chemicals), leaving every other sector's signals byte-identical.

**Architecture:** `universe.yaml` `eu_sectors` values become **lists** of component tickers. A new `build_composite_series` blends a sector's components into one rebased-mean `Close` + summed `Volume`; the existing `compute_signals_for_sector` runs on that. Single-component sectors keep the exact current code path (no composite), guaranteeing identical results. All other consumers of `eu_sectors` are made list-safe *before* the config flips to lists, so there is no broken intermediate state.

**Tech Stack:** Python 3, pandas, numpy, PyYAML, pytest.

**Spec:** `design/specs/2026-06-29-eu-sector-composites-design.md`

## Global Constraints

- **Only EU Financials & Materials change.** All US sectors and the other 9 EU sectors must produce **byte-identical** signals — guaranteed by routing single-component sectors through the unchanged `prices[sector_ticker]` path (composite is built only when a sector has >1 component).
- **Equal-weight** components (no weights in config).
- **Composite Close** = mean of each component's Close rebased to 100 at the common (inner-join) start date. **Composite Volume** = element-wise sum of component volumes on the common index.
- **Benchmark unchanged** (`EXSA.DE`); **US path unchanged**.
- **Components (verbatim):** Financials = `EXV1.DE` (Banks), `EXH2.DE` (Financial Services, DE000A0H08G5), `EXH5.DE` (Insurance, DE000A0H08K7); Materials = `EXV6.DE` (Basic Resources), `EXV7.DE` (Chemicals, DE000A0H08E0). All new components TER `"0.46%"`, issuer iShares, url `https://www.justetf.com/en/etf-profile.html?isin=<ISIN>`.
- **`config/universe.yaml`** schema: `eu_sectors` value = `list[str]`; `us_sectors` stays `str`.
- **Do NOT commit `docs/`** (CI-owned). Conventional commits; subject < 72 chars; end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Branch:** `feature/eu-sector-composites` (already created).

## File structure

- `src/pipeline.py` — add `build_composite_series`; add optional `sector_df` to `compute_signals_for_sector`; route EU sectors in `build_signals_rows` (single vs composite).
- `scan.py` — flatten list-valued `eu_sectors` when collecting tickers to fetch.
- `src/data/trends_symbols.py` — `build_symbol_map` normalizes list-valued `eu_sectors` primaries.
- `dashboard/build.py` — breakdown footer ETF display joins a list-valued ticker.
- `src/backtest/rotations.py` — use the first (primary) component for a list-valued sector.
- `config/universe.yaml` — `eu_sectors` → lists (2 multi, 9 single).
- `config/sector_etfs.yaml` — Financials & Materials list their component ETFs.
- Tests under `tests/`.

---

### Task 1: Composite series + signal routing in the pipeline

**Files:**
- Modify: `src/pipeline.py`
- Test: `tests/test_pipeline_composite.py`

**Interfaces:**
- Produces:
  - `build_composite_series(tickers: list[str], prices: dict[str, pd.DataFrame]) -> pd.DataFrame | None` — equal-weight composite: `Close` = mean of components' Closes each rebased to 100 at the common inner-join start; `Volume` = summed component volumes on the common index. `None` if no component has usable Close data.
  - `compute_signals_for_sector(..., prices, sector_df: pd.DataFrame | None = None)` — when `sector_df` is provided, signals are computed from it instead of `prices[sector_ticker]`; when `None`, behaviour is unchanged.
  - `build_signals_rows` — EU sectors whose `eu_sectors` value is a single-element list use the unchanged single-ticker path; multi-element lists build a composite and pass it as `sector_df`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_composite.py
import numpy as np
import pandas as pd
import pytest
from src.pipeline import build_composite_series, build_signals_rows


def _frame(closes, vols=None):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    data = {"Close": closes}
    if vols is not None:
        data["Volume"] = vols
    return pd.DataFrame(data, index=idx)


def test_composite_equal_weight_rebased_mean():
    # A doubles (100->200 rebased), B flat (100->100). Mean ends at 150.
    prices = {
        "A": _frame([10.0, 20.0], [100, 100]),
        "B": _frame([50.0, 50.0], [200, 200]),
    }
    out = build_composite_series(["A", "B"], prices)
    assert list(out["Close"]) == pytest.approx([100.0, 150.0])
    assert list(out["Volume"]) == [300, 300]          # summed volumes


def test_composite_drops_missing_component_and_blends_rest():
    prices = {"A": _frame([10.0, 11.0])}              # B absent
    out = build_composite_series(["A", "B"], prices)
    assert list(out["Close"]) == pytest.approx([100.0, 110.0])  # just A, rebased
    assert "Volume" not in out.columns                # no component had Volume


def test_composite_all_missing_returns_none():
    assert build_composite_series(["X", "Y"], {}) is None


def test_build_signals_rows_single_element_list_matches_string():
    # Single-element list must behave exactly like the bare-string path.
    idx = pd.date_range("2026-01-01", periods=300, freq="D")
    close = pd.Series(np.linspace(100, 130, 300), index=idx)
    bench = pd.Series(np.linspace(100, 120, 300), index=idx)
    prices = {
        "EXV3.DE": pd.DataFrame({"Close": close, "Volume": 1000}),
        "EXSA.DE": pd.DataFrame({"Close": bench, "Volume": 1000}),
    }
    u_str = {"eu_sectors": {"Technology": "EXV3.DE"}, "us_sectors": {},
             "us_benchmark": "EXSA.DE", "eu_benchmark": "EXSA.DE"}
    u_list = {"eu_sectors": {"Technology": ["EXV3.DE"]}, "us_sectors": {},
              "us_benchmark": "EXSA.DE", "eu_benchmark": "EXSA.DE"}
    r_str = build_signals_rows(u_str, prices)
    r_list = build_signals_rows(u_list, prices)
    assert r_str == r_list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pipeline_composite.py -v`
Expected: FAIL — `build_composite_series` not defined.

- [ ] **Step 3: Implement `build_composite_series` (add to `src/pipeline.py`, above `compute_signals_for_sector`)**

```python
def build_composite_series(
    tickers: list[str],
    prices: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    """Equal-weight composite of component ETFs.

    Close = mean of each component's Close rebased to 100 at the common
    (inner-join) start date; Volume = summed component volumes on that index.
    Returns None if no component has usable Close data.
    """
    closes, vols = [], []
    for t in tickers:
        df = prices.get(t)
        if df is None or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        if c.empty:
            continue
        closes.append(c.rename(t))
        if "Volume" in df.columns:
            vols.append(df["Volume"].rename(t))
    if not closes:
        return None
    close_df = pd.concat(closes, axis=1, join="inner").dropna()
    if close_df.empty:
        return None
    rebased = close_df / close_df.iloc[0] * 100.0
    out = pd.DataFrame({"Close": rebased.mean(axis=1)})
    if vols:
        vol_df = pd.concat(vols, axis=1, join="inner").reindex(out.index)
        out["Volume"] = vol_df.sum(axis=1)
    return out
```

- [ ] **Step 4: Add the `sector_df` parameter to `compute_signals_for_sector`**

Change the signature and the lookup guard only; leave all signal blocks unchanged.

```python
def compute_signals_for_sector(
    sector_key: str,
    region: str,
    gics_sector: str,
    sector_ticker: str,
    benchmark_ticker: str,
    prices: dict[str, pd.DataFrame],
    sector_df: pd.DataFrame | None = None,
) -> dict | None:
    # ... docstring + imports unchanged ...
    if sector_df is None:
        if sector_ticker not in prices:
            logger.warning("Skipping %s (%s) — ticker %s not in price data", gics_sector, region, sector_ticker)
            return None
        sector_df = prices[sector_ticker]
    if benchmark_ticker not in prices:
        logger.warning("Skipping %s (%s) — benchmark ticker %s not in price data", gics_sector, region, benchmark_ticker)
        return None

    bench_df = prices[benchmark_ticker]
    # ... rest of the function unchanged (uses sector_df) ...
```

- [ ] **Step 5: Route EU sectors in `build_signals_rows`**

Replace the EU loop so a list value is normalized; single-element → unchanged path, multi-element → composite.

```python
    # EU sectors
    for gics_sector, value in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        tickers = value if isinstance(value, list) else [value]
        if len(tickers) == 1:
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker=tickers[0], benchmark_ticker=eu_benchmark, prices=prices,
            )
        else:
            comp = build_composite_series(tickers, prices)
            if comp is None:
                logger.warning("Skipping EU %s — no composite data for %s", gics_sector, tickers)
                continue
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker="+".join(tickers), benchmark_ticker=eu_benchmark,
                prices=prices, sector_df=comp,
            )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pipeline_composite.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_composite.py
git commit -m "feat: composite price series + sector_df routing in pipeline" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Make remaining `eu_sectors` consumers list-safe

**Files:**
- Modify: `scan.py`, `src/data/trends_symbols.py`, `dashboard/build.py`, `src/backtest/rotations.py`
- Test: `tests/test_trends_symbols_map.py` (extend)

**Interfaces:**
- Consumes: `eu_sectors` values that may be `str` or `list[str]`.
- Produces: every consumer tolerates both forms (no behaviour change while values are still strings).

- [ ] **Step 1: Write the failing test (build_symbol_map with a list value)**

Add to `tests/test_trends_symbols_map.py`:

```python
def test_build_symbol_map_handles_list_valued_eu_sector():
    universe = {
        "us_sectors": {}, "eu_sectors": {"Financials": ["EXV1.DE", "EXH2.DE", "EXH5.DE"]},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }
    sector_etfs = {"EU": {"Financials": []}}
    m = build_symbol_map(universe, sector_etfs, blocklist=set())
    assert m["EU|Financials"] == ["EXV1.DE", "EXH2.DE", "EXH5.DE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_trends_symbols_map.py::test_build_symbol_map_handles_list_valued_eu_sector -v`
Expected: FAIL — current code does `[primary] + ...` with `primary` a list, so the key is absent or malformed.

- [ ] **Step 3: Normalize the primary in `build_symbol_map` (`src/data/trends_symbols.py`)**

```python
    for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for sector, primary in universe.get(key, {}).items():
            symbols: list[str] = []
            prims = primary if isinstance(primary, list) else [primary]
            candidates = prims + [
                e.get("ticker")
                for e in sector_etfs.get(region, {}).get(sector, [])
                if e.get("ticker")
            ]
            # ... unchanged dedup/blocklist loop ...
```

- [ ] **Step 4: Flatten list values in `scan.py` ticker collection**

Replace the `all_tickers` construction (`scan.py` ~line 224):

```python
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

- [ ] **Step 5: Join list-valued ticker in the dashboard footer (`dashboard/build.py` ~line 350)**

```python
        ticker = universe.get("eu_sectors", {}).get(sector_name, "—")
        if isinstance(ticker, list):
            ticker = " + ".join(ticker)
```

(Leave the `us_sectors` line above it unchanged — US values stay strings.)

- [ ] **Step 6: Use the primary component in `rotations.py` (`src/backtest/rotations.py` ~line 38)**

```python
        ticker = sector_map.get(sector)
        if isinstance(ticker, list):
            ticker = ticker[0] if ticker else None
```

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_trends_symbols_map.py -q`
Expected: PASS (existing map tests + the new one).

- [ ] **Step 8: Commit**

```bash
git add scan.py src/data/trends_symbols.py dashboard/build.py src/backtest/rotations.py tests/test_trends_symbols_map.py
git commit -m "refactor: make eu_sectors consumers accept list values" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Flip config to composites + reference + backlog, verify end-to-end

**Files:**
- Modify: `config/universe.yaml`, `config/sector_etfs.yaml`, `BACKLOG.md`

**Interfaces:**
- Consumes: list-safe consumers (Tasks 1–2).
- Produces: the live scan computes Financials/Materials from composites; the other 20 sector-keys are unchanged.

- [ ] **Step 1: Convert `eu_sectors` to lists in `config/universe.yaml`**

Every EU sector becomes a list; only Financials and Materials are multi-component:

```yaml
eu_sectors:
  Technology: [EXV3.DE]
  Financials: [EXV1.DE, EXH2.DE, EXH5.DE]   # Banks + Financial Services + Insurance
  Energy: [EXH1.DE]
  Health Care: [EXV4.DE]
  Industrials: [EXH4.DE]
  Consumer Discretionary: [EXH7.DE]
  Consumer Staples: [EXH3.DE]
  Utilities: [EXH9.DE]
  Materials: [EXV6.DE, EXV7.DE]             # Basic Resources + Chemicals
  Real Estate: [IPRP.L]
  Communication Services: [EXV2.DE]
```

Keep `us_sectors`, both benchmarks, and all comments otherwise intact.

- [ ] **Step 2: Add the component ETFs to the two composite sectors in `config/sector_etfs.yaml`**

Replace the single `Financials` entry with the three components, and add Chemicals to `Materials` (keep Basic Resources). Use the existing schema. Financials:

```yaml
  Financials:
    - ticker: EXV1.DE
      name: iShares STOXX Europe 600 Banks
      isin: DE000A0F5UJ7
      ter: "0.47%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0F5UJ7
    - ticker: EXH2.DE
      name: iShares STOXX Europe 600 Financial Services
      isin: DE000A0H08G5
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08G5
    - ticker: EXH5.DE
      name: iShares STOXX Europe 600 Insurance
      isin: DE000A0H08K7
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08K7
```

Materials:

```yaml
  Materials:
    - ticker: EXV6.DE
      name: iShares STOXX Europe 600 Basic Resources
      isin: DE000A0F5UK5
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0F5UK5
    - ticker: EXV7.DE
      name: iShares STOXX Europe 600 Chemicals
      isin: DE000A0H08E0
      ter: "0.46%"
      issuer: iShares
      url: https://www.justetf.com/en/etf-profile.html?isin=DE000A0H08E0
```

Update the EU header comment to note Financials & Materials are composites (their signal is the equal-weight blend of the listed components).

- [ ] **Step 3: Validate config shape + the reference==components invariant**

```bash
python3 - <<'PY'
import yaml
u = yaml.safe_load(open("config/universe.yaml"))
e = yaml.safe_load(open("config/sector_etfs.yaml"))
assert all(isinstance(v, list) for v in u["eu_sectors"].values()), "eu_sectors must all be lists"
assert u["eu_sectors"]["Financials"] == ["EXV1.DE","EXH2.DE","EXH5.DE"]
assert u["eu_sectors"]["Materials"] == ["EXV6.DE","EXV7.DE"]
# EU reference lists exactly the scanned components, in order
for sec, comps in u["eu_sectors"].items():
    refs = [x["ticker"] for x in e["EU"][sec]]
    assert refs == comps, (sec, comps, refs)
for region in ("US","EU"):
    for sec,lst in e[region].items():
        for x in lst:
            assert x["isin"] in x["url"], (region, sec)
print("OK: eu_sectors all lists; Financials/Materials composites; EU reference == components")
PY
```
Expected: `OK: eu_sectors all lists; Financials/Materials composites; EU reference == components`

- [ ] **Step 4: Validate Yahoo data exists for the three new components**

```bash
python3 - <<'PY'
from dotenv import load_dotenv; load_dotenv(".env")
import yfinance as yf
for t in ("EXH2.DE","EXH5.DE","EXV7.DE"):
    df = yf.download(t, period="1y", progress=False, auto_adjust=False)
    print(t, "rows:", len(df), "ok" if len(df) > 100 else "TOO FEW — investigate")
PY
```
Expected: each ticker returns > 100 rows. If any is sparse/empty, stop and report (the composite would silently drop it).

- [ ] **Step 5: Full suite + acceptance check (20 sectors unchanged)**

Run: `python3 -m pytest -q`
Expected: PASS (168 passed + new tests; 6 pre-existing skips).

Then rebuild the dashboard from the DB and confirm only EU Financials/Materials moved:
```bash
python3 dashboard/build.py >/tmp/b.log 2>&1 && tail -1 /tmp/b.log
```
Confirm the build succeeds and the EU Financials/Materials breakdown footer shows `ETF: EXV1.DE + EXH2.DE + EXH5.DE` / `ETF: EXV6.DE + EXV7.DE`. Do **not** commit `docs/`: `git checkout -- docs/ 2>/dev/null; git clean -fdq docs/`.

- [ ] **Step 6: Backlog Done entry**

Add to the top of `## Done` in `BACKLOG.md`:
```markdown
- ~~EU sector composites (Phase 1: Financials, Materials)~~ — EU Financials (Banks +
  Financial Services + Insurance) and Materials (Basic Resources + Chemicals) are now
  equal-weight composites of their STOXX supersector ETFs instead of a single sub-sector,
  making them truer GICS proxies. `eu_sectors` values are lists; `build_composite_series`
  blends a rebased-mean Close + summed Volume; single-component sectors and all US sectors
  unchanged. Phase 2 (Consumer Discretionary/Staples/Comm + Media/P&HG crosswalk) pending. *(2026-06-29)*
```

- [ ] **Step 7: Commit**

```bash
git add config/universe.yaml config/sector_etfs.yaml BACKLOG.md
git commit -m "feat: EU Financials & Materials as composite sector proxies" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Composite price series (rebased-mean Close + summed Volume, equal-weight) → Task 1 `build_composite_series`. ✓
- Single-component identity guarantee → Task 1 routes len==1 through the unchanged path; regression test compares list vs string. ✓
- `eu_sectors` → lists → Task 3 Step 1. ✓
- All consumers list-safe before the flip (scan fetch, build_symbol_map, dashboard footer, rotations) → Task 2; flip in Task 3. ✓
- Component sourcing (EXH2/EXH5/EXV7 ISIN/TER) → Global Constraints + Task 3 Step 2; Yahoo validation → Task 3 Step 4. ✓
- Reference == components → Task 3 Steps 2–3. ✓
- Benchmark/US unchanged → Global Constraints; no task edits them. ✓
- Acceptance (only 2 EU sectors change) → Task 3 Step 5. ✓

**Placeholder scan:** none — every step has concrete code/commands and exact ISINs/tickers.

**Type consistency:** `build_composite_series(list[str], dict) -> DataFrame|None` consumed by `build_signals_rows`; `compute_signals_for_sector(..., sector_df=None)` consumed in the same task. `eu_sectors` value type (`list[str]`) consistent across Tasks 1–3 and all four consumers in Task 2. Component tickers/ISINs identical in Global Constraints, Task 3 Step 1, and Task 3 Step 2.

## Note on ordering

Tasks 1 and 2 are backward-compatible (they accept both `str` and `list` values), so the suite stays green while `universe.yaml` still holds strings. Only Task 3 flips the live config — at which point every consumer already tolerates lists. Execute in order.
