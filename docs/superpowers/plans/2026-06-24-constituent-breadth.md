# Constituent Breadth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the misleading single-ETF breadth *proxy* with true constituent breadth for US sectors — the % of each sector's S&P 500 constituents trading above their own 50-DMA — as an info-only signal.

**Architecture:** A new `src/data/constituents.py` scrapes + caches the S&P 500 GICS table; a new `src/signals/breadth.py` computes equal-weight % above 50-DMA per sector; `scan.py` runs both as a non-fatal step after the ETF price fetch and injects the result into US sector rows (EU rows → NaN). Composite/weights are untouched (breadth stays info-only).

**Tech Stack:** Python 3.11+, pandas (`read_html` via lxml), pytest. Reuses `src/data/prices.py:fetch_prices`.

## Global Constraints

- Breadth is **info-only** — do NOT add it to `config/weights.yaml` signal lists or change the composite/ranking.
- US-only true breadth; **EU sectors store `NaN`** for `breadth_above_50dma` (render "—").
- Every breadth step is **non-fatal** — any failure logs a warning, leaves breadth `NaN`, and the scan still completes.
- Equal-weight breadth = `count(above 50-DMA) / count(valid constituents)`, value in `[0, 1]`.
- Run Python via the project venv: `.venv/bin/python`, `.venv/bin/pytest`.
- Branch `feature/constituent-breadth` (already created). Conventional commits, subject < 72 chars. No secrets in tracked files.
- **Do not run the full `pytest` suite with only `DATABASE_URL` set unless the test-wipe fix (skip state tests unless `TEST_DATABASE_URL`) is present on this branch** — branch from current `main`, which has it (PR #17 merged). Verify `tests/test_state_smoke.py` skips before running the whole suite.

---

### Task 1: `src/data/constituents.py` — fetch + cache S&P 500 constituents

**Files:**
- Create: `src/data/constituents.py`
- Modify: `requirements.txt` (pin `lxml` for `pandas.read_html`)
- Test: `tests/test_constituents.py`

**Interfaces:**
- Produces: `fetch_sp500_constituents(cache_dir: str = "data/cache", ttl_days: int = 7) -> dict[str, list[str]] | None` — maps our sector key (e.g. `"Technology"`) → list of yfinance-style tickers (e.g. `["AAPL", "MSFT", "BRK-B"]`). Returns `None` on any failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_constituents.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.data.constituents import fetch_sp500_constituents, _GICS_TO_SECTOR


def _fake_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": ["AAPL", "MSFT", "BRK.B", "JPM", "XOM"],
            "GICS Sector": [
                "Information Technology",
                "Information Technology",
                "Financials",
                "Financials",
                "Energy",
            ],
        }
    )


def test_maps_gics_sector_and_normalizes_tickers(tmp_path):
    with patch("src.data.constituents.pd.read_html", return_value=[_fake_table()]):
        result = fetch_sp500_constituents(cache_dir=str(tmp_path))
    assert result is not None
    # "Information Technology" → our "Technology"
    assert set(result["Technology"]) == {"AAPL", "MSFT"}
    assert set(result["Financials"]) == {"BRK-B", "JPM"}   # BRK.B → BRK-B
    assert result["Energy"] == ["XOM"]


def test_writes_then_reads_cache_without_rescrape(tmp_path):
    with patch("src.data.constituents.pd.read_html", return_value=[_fake_table()]) as m:
        fetch_sp500_constituents(cache_dir=str(tmp_path))
        assert m.call_count == 1
    # Second call within TTL must NOT scrape again.
    with patch("src.data.constituents.pd.read_html", side_effect=AssertionError("should not scrape")) as m2:
        cached = fetch_sp500_constituents(cache_dir=str(tmp_path))
        m2.assert_not_called()
    assert cached["Technology"]


def test_scrape_failure_returns_none(tmp_path):
    with patch("src.data.constituents.pd.read_html", side_effect=Exception("network down")):
        assert fetch_sp500_constituents(cache_dir=str(tmp_path)) is None


def test_information_technology_is_the_only_nonidentity_mapping():
    # Guard: if Wikipedia renames a GICS sector, this fails loudly.
    for gics, ours in _GICS_TO_SECTOR.items():
        if gics != "Information Technology":
            assert gics == ours
    assert _GICS_TO_SECTOR["Information Technology"] == "Technology"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_constituents.py -q`
Expected: FAIL — `No module named 'src.data.constituents'`.

- [ ] **Step 3: Implement `src/data/constituents.py`**

```python
"""
S&P 500 constituent loader (per GICS sector), for true sector breadth.

Source: the Wikipedia "List of S&P 500 companies" table (free, no API key).
Cached to data/cache/sp500_constituents.json with a multi-day TTL — the list
changes only a few times a year. Returns None on any failure (callers degrade
gracefully; breadth is info-only).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Wikipedia "GICS Sector" → our sector keys (config/universe.yaml us_sectors).
# Only "Information Technology" differs; the other ten are identical.
_GICS_TO_SECTOR = {
    "Information Technology": "Technology",
    "Financials": "Financials",
    "Energy": "Energy",
    "Health Care": "Health Care",
    "Industrials": "Industrials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Utilities": "Utilities",
    "Materials": "Materials",
    "Real Estate": "Real Estate",
    "Communication Services": "Communication Services",
}

_CACHE_NAME = "sp500_constituents.json"


def _cache_file(cache_dir: str) -> str:
    return os.path.join(cache_dir, _CACHE_NAME)


def _cache_fresh(path: str, ttl_days: int) -> bool:
    if not os.path.exists(path):
        return False
    age_days = (time.time() - os.path.getmtime(path)) / 86400.0
    return age_days < ttl_days


def fetch_sp500_constituents(
    cache_dir: str = "data/cache",
    ttl_days: int = 7,
) -> dict[str, list[str]] | None:
    """Return {our_sector: [yf_ticker, ...]} for the S&P 500, or None on failure."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_file(cache_dir)

    if _cache_fresh(cache_file, ttl_days):
        logger.info("Constituents: cache hit %s", cache_file)
        try:
            return {s: list(v) for s, v in json.loads(Path(cache_file).read_text()).items()}
        except (json.JSONDecodeError, OSError):
            pass  # fall through to fresh scrape

    try:
        tables = pd.read_html(_WIKI_URL)
        df = tables[0]
        if "Symbol" not in df.columns or "GICS Sector" not in df.columns:
            logger.warning("Constituents: unexpected table columns %s", list(df.columns))
            return None

        result: dict[str, list[str]] = {}
        for _, r in df.iterrows():
            gics = str(r["GICS Sector"]).strip()
            sector = _GICS_TO_SECTOR.get(gics)
            if sector is None:
                logger.warning("Constituents: unmapped GICS sector %r — skipping", gics)
                continue
            ticker = str(r["Symbol"]).strip().replace(".", "-")  # BRK.B → BRK-B
            result.setdefault(sector, []).append(ticker)

        if not result:
            logger.warning("Constituents: no rows mapped — returning None")
            return None

        tmp = cache_file + ".tmp"
        Path(tmp).write_text(json.dumps(result))
        os.replace(tmp, cache_file)
        logger.info("Constituents: scraped %d sectors → %s",
                    len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("Constituents: fetch failed (%s) — breadth unavailable", exc)
        return None
```

- [ ] **Step 4: Pin `lxml` in requirements.txt**

Add a line to `requirements.txt` (read it first, append after the last dependency):

```
lxml>=5.0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_constituents.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/data/constituents.py tests/test_constituents.py requirements.txt
git commit -m "feat: add S&P 500 constituent loader for sector breadth"
```

---

### Task 2: `src/signals/breadth.py` — compute constituent breadth

**Files:**
- Create: `src/signals/breadth.py`
- Test: `tests/test_breadth.py`

**Interfaces:**
- Consumes: `prices: dict[str, pd.DataFrame]` (ticker → frame with a `"Close"` column, from `fetch_prices`); `constituents: dict[str, list[str]]` (our_sector → tickers, from Task 1).
- Produces: `compute_constituent_breadth(prices, constituents, min_coverage: float = 0.60) -> dict[str, float]` — maps `"US|<sector>"` → breadth in `[0, 1]`, or `float("nan")` when coverage `< min_coverage` or no valid constituents.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_breadth.py`:

```python
import math

import numpy as np
import pandas as pd

from src.signals.breadth import compute_constituent_breadth


def _frame(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def _above() -> pd.DataFrame:
    # 60 days flat at 100 then jump to 200 → last close well above 50-DMA
    return _frame([100.0] * 60 + [200.0])


def _below() -> pd.DataFrame:
    # 60 days flat at 100 then drop to 50 → last close below 50-DMA
    return _frame([100.0] * 60 + [50.0])


def test_breadth_fraction_is_count_above_over_valid():
    constituents = {"Technology": ["A", "B", "C", "D"]}
    prices = {"A": _above(), "B": _above(), "C": _above(), "D": _below()}  # 3/4
    out = compute_constituent_breadth(prices, constituents)
    assert math.isclose(out["US|Technology"], 0.75, abs_tol=1e-9)


def test_under_coverage_returns_nan():
    # Only 1 of 4 constituents has data → 25% < 60% → NaN
    constituents = {"Energy": ["A", "B", "C", "D"]}
    prices = {"A": _above()}
    out = compute_constituent_breadth(prices, constituents)
    assert math.isnan(out["US|Energy"])


def test_short_history_excluded_from_denominator():
    # B has < 50 closes → not "valid"; A counts. 1 valid of 2 listed = 50% < 60% → NaN
    constituents = {"Materials": ["A", "B"]}
    prices = {"A": _above(), "B": _frame([100.0] * 10)}
    out = compute_constituent_breadth(prices, constituents)
    assert math.isnan(out["US|Materials"])


def test_empty_sector_is_nan():
    out = compute_constituent_breadth({}, {"Utilities": []})
    assert math.isnan(out["US|Utilities"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_breadth.py -q`
Expected: FAIL — `No module named 'src.signals.breadth'`.

- [ ] **Step 3: Implement `src/signals/breadth.py`**

```python
"""
True constituent breadth: the share of a sector's constituents trading above
their own 50-day moving average. Equal-weight, info-only.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_MA_WINDOW = 50


def _is_above_50dma(close: pd.Series) -> bool | None:
    """True/False if computable (>=50 valid closes), else None."""
    clean = close.dropna()
    if len(clean) < _MA_WINDOW:
        return None
    ma50 = float(clean.rolling(_MA_WINDOW).mean().iloc[-1])
    return float(clean.iloc[-1]) > ma50


def compute_constituent_breadth(
    prices: dict[str, "pd.DataFrame"],
    constituents: dict[str, list[str]],
    min_coverage: float = 0.60,
) -> dict[str, float]:
    """Return {"US|<sector>": pct_above_50dma in [0,1]} or NaN when under-covered."""
    out: dict[str, float] = {}
    for sector, tickers in constituents.items():
        n_listed = len(tickers)
        above = 0
        valid = 0
        for t in tickers:
            df = prices.get(t)
            if df is None or "Close" not in df.columns:
                continue
            verdict = _is_above_50dma(df["Close"])
            if verdict is None:
                continue
            valid += 1
            if verdict:
                above += 1
        coverage = (valid / n_listed) if n_listed else 0.0
        if valid == 0 or coverage < min_coverage:
            out[f"US|{sector}"] = float("nan")
        else:
            out[f"US|{sector}"] = above / valid
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_breadth.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/signals/breadth.py tests/test_breadth.py
git commit -m "feat: compute equal-weight constituent breadth per sector"
```

---

### Task 3: Wire breadth into `scan.py` (and retire the proxy)

**Files:**
- Modify: `scan.py` (imports; `_compute_signals_for_sector` proxy removal; `main()` injection)
- Modify: `src/signals/technical.py` (remove `compute_breadth_proxy`)
- Modify: `tests/test_signals.py`, `tests/test_signals_smoke.py` (remove proxy tests/imports)
- Test: `tests/test_scan_smoke.py` (non-fatal breadth contract)

**Interfaces:**
- Consumes: `fetch_sp500_constituents` (Task 1), `compute_constituent_breadth` (Task 2), existing `fetch_prices`, and `start_date`/`end_date`/`us_sectors`/`rows` already in `main()` scope.
- Produces: each row's `breadth_above_50dma` is true breadth for US (or NaN), NaN for EU.

- [ ] **Step 1: Write the failing test (non-fatal contract)**

Add to `tests/test_scan_smoke.py`:

```python
def test_breadth_injection_is_non_fatal_and_eu_is_nan():
    """If constituent fetch returns None, breadth stays NaN and rows still build;
    a helper injects true breadth for US and NaN for EU."""
    import math
    from unittest.mock import patch
    from scan import _inject_constituent_breadth

    rows = [
        {"region": "US", "gics_sector": "Technology", "sector_key": "US|Technology",
         "breadth_above_50dma": 1.0},
        {"region": "EU", "gics_sector": "Technology", "sector_key": "EU|Technology",
         "breadth_above_50dma": 1.0},
    ]
    # Constituent fetch fails → all breadth NaN, no exception raised.
    with patch("scan.fetch_sp500_constituents", return_value=None):
        _inject_constituent_breadth(rows, start="2026-01-01", end="2026-06-01")
    assert math.isnan(rows[0]["breadth_above_50dma"])
    assert math.isnan(rows[1]["breadth_above_50dma"])


def test_breadth_injection_sets_us_value_and_eu_nan():
    import math
    from unittest.mock import patch
    from scan import _inject_constituent_breadth

    rows = [
        {"region": "US", "gics_sector": "Technology", "sector_key": "US|Technology",
         "breadth_above_50dma": float("nan")},
        {"region": "EU", "gics_sector": "Technology", "sector_key": "EU|Technology",
         "breadth_above_50dma": float("nan")},
    ]
    with patch("scan.fetch_sp500_constituents", return_value={"Technology": ["A", "B"]}), \
         patch("scan.fetch_prices", return_value={}), \
         patch("scan.compute_constituent_breadth", return_value={"US|Technology": 0.66}):
        _inject_constituent_breadth(rows, start="2026-01-01", end="2026-06-01")
    assert rows[0]["breadth_above_50dma"] == 0.66      # US injected
    assert math.isnan(rows[1]["breadth_above_50dma"])  # EU forced NaN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scan_smoke.py::test_breadth_injection_is_non_fatal_and_eu_is_nan tests/test_scan_smoke.py::test_breadth_injection_sets_us_value_and_eu_nan -q`
Expected: FAIL — `cannot import name '_inject_constituent_breadth' from 'scan'`.

- [ ] **Step 3: Add the injection helper + imports in `scan.py`**

Near the top-of-file imports of `scan.py`, the helper uses `fetch_sp500_constituents`, `fetch_prices`, `compute_constituent_breadth` — import them at module level so the tests can patch `scan.<name>`:

```python
from src.data.prices import fetch_prices
from src.data.constituents import fetch_sp500_constituents
from src.signals.breadth import compute_constituent_breadth
```
(If `fetch_prices` is currently imported only inside `main()`, also add it at module level as above; leave the in-`main` import or switch `main` to the module-level one — do not import it twice inside `main`.)

Add this module-level helper (e.g. after `_build_signals_rows`):

```python
def _inject_constituent_breadth(rows: list[dict], start: str, end: str) -> None:
    """Mutate rows in place: set breadth_above_50dma to true constituent breadth
    for US sectors (NaN if unavailable/under-covered), and NaN for EU sectors.
    Fully non-fatal — any failure leaves all breadth values NaN."""
    nan = float("nan")
    breadth: dict[str, float] = {}
    try:
        constituents = fetch_sp500_constituents()
        if constituents:
            all_tickers = sorted({t for ts in constituents.values() for t in ts})
            logger.info("Fetching prices for %d S&P 500 constituents …", len(all_tickers))
            cons_prices = fetch_prices(tickers=all_tickers, start=start, end=end)
            breadth = compute_constituent_breadth(cons_prices, constituents)
        else:
            logger.warning("Constituent breadth unavailable — leaving NaN")
    except Exception as exc:
        logger.warning("Constituent breadth step failed (%s) — leaving NaN", exc)

    for row in rows:
        if row.get("region") == "US":
            row["breadth_above_50dma"] = breadth.get(f"US|{row['gics_sector']}", nan)
        else:
            row["breadth_above_50dma"] = nan
```

- [ ] **Step 4: Call the helper in `main()`**

In `scan.py main()`, immediately after the `if not rows: ... return 1` guard and before `wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]`, insert:

```python
    logger.info("Computing true constituent breadth …")
    _inject_constituent_breadth(rows, start=str(start_date), end=str(end_date))
```

- [ ] **Step 5: Remove the proxy usage in `_compute_signals_for_sector`**

In `scan.py`, delete the breadth proxy block (the `try`/`except` that calls `compute_breadth_proxy` and sets `signals["breadth_above_50dma"]`, around lines 166–171) — `signals` already initializes `breadth_above_50dma` to NaN, and `main()` now sets the real value. Also remove `compute_breadth_proxy` from the `from src.signals.technical import ...` line in that function.

- [ ] **Step 6: Remove `compute_breadth_proxy` from `src/signals/technical.py`**

Delete the entire `compute_breadth_proxy` function (the `def compute_breadth_proxy(...)` through its `return result`). Update the module docstring line "Technical signals: moving averages, breadth proxy, on-balance volume." → "Technical signals: moving averages and on-balance volume."

- [ ] **Step 7: Remove the proxy's tests**

- In `tests/test_signals.py`: remove `compute_breadth_proxy` from the `from src.signals.technical import ...` line, and delete `test_compute_breadth_proxy_keys`.
- In `tests/test_signals_smoke.py`: remove `compute_breadth_proxy` from its import line and delete the two lines exercising it (`bp = compute_breadth_proxy(sector)` and the `assert 'breadth_above_50dma' in bp`).

- [ ] **Step 8: Run the affected tests**

Run: `.venv/bin/pytest tests/test_scan_smoke.py tests/test_signals.py tests/test_signals_smoke.py -q`
Expected: all pass (new breadth-injection tests pass; proxy tests gone).

- [ ] **Step 9: Confirm the state tests still skip, then run the full suite**

Run: `.venv/bin/pytest tests/test_state_smoke.py -q`
Expected: `5 skipped` (no `TEST_DATABASE_URL`). Then:
Run: `.venv/bin/pytest -q`
Expected: all pass / skipped; no DB wipe.

- [ ] **Step 10: Commit**

```bash
git add scan.py src/signals/technical.py tests/
git commit -m "feat: wire true constituent breadth into scan, retire proxy"
```

---

### Task 4: End-to-end verify, backlog, finish

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Run a real scan and rebuild the dashboard**

Run: `.venv/bin/python scan.py`
Expected: logs "Fetching prices for ~500 S&P 500 constituents …" then "Saved scan_id=…"; completes even if some constituents fail. (If Supabase is empty, this also repopulates it.)

- [ ] **Step 2: Verify breadth renders correctly**

Run: `grep -o '<td class="sig-raw">[0-9]\{1,3\}%</td>' docs/index.html | head` (informal) — or open the dashboard, expand a US sector row, and confirm "Breadth >50-DMA" shows a realistic percentage (e.g. 55%), while an EU sector shows "—". Use the preview workflow (`preview_start` "dashboard") and `preview_snapshot` to confirm.

- [ ] **Step 3: Move the item to Done in `BACKLOG.md`**

Under `## Phase 3 features`, change the line:
```
- **Constituent breadth** — true breadth from sector constituents (vs the current
  proxy)
```
to remove it from the list, and add under `## Done`:
```
- ~~Constituent breadth (Phase 3.1)~~ — true breadth for US sectors: % of each
  sector's S&P 500 constituents (Wikipedia GICS list) above their own 50-DMA,
  info-only; EU shows "—"; retired the single-ETF proxy. *(2026-06-24)*
```

- [ ] **Step 4: Commit**

```bash
git add BACKLOG.md docs/index.html
git commit -m "docs: mark constituent breadth (Phase 3.1) done"
```

- [ ] **Step 5: Code review + push**

Per `CLAUDE.md`: run `/code-review`, address findings, then push and open a PR:
```bash
git push -u origin feature/constituent-breadth
```
Stop there — Jonas reviews and merges manually.
