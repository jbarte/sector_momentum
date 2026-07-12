# Sentiment Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Google Trends sentiment pipeline with a seasonal baseline signal (12-month window) and rising/breakout query discovery per sector.

**Architecture:** Change the existing `fetch_symbol_trends` call from 3-month to 12-month timeframe. Existing derived signals operate on the trailing 13 weeks of the longer series. A new `seasonal_ratio` signal uses the full 52-week series. A separate `fetch_rising_queries` function calls `related_queries()` per sector, stored as JSON in a new `text_value` column on `sentiment_signals`. Both surface on the sentiment dashboard page.

**Tech Stack:** Python 3, pytrends, pandas, numpy, psycopg2, Jinja2, pytest

## Global Constraints

- Branch: `feature/sentiment-enrichment`
- Both features are info-only / toggle-only — neither affects the momentum composite score
- All new API calls are fail-open (log + continue on failure)
- Day-cache integration follows the existing `cache.setdefault(namespace, {})[key]` pattern
- i18n: every user-facing string needs EN + SV
- Do not commit `docs/` from the feature branch (CI rebuilds it after merge)
- Update `BACKLOG.md` Done section in the same branch

---

### Task 1: Seasonal ratio signal + tests

**Files:**
- Modify: `src/data/trends_symbols.py` (lines 122-221 — derived signal functions + `DERIVED_SIGNAL_NAMES`)
- Modify: `tests/test_trends_symbols_transforms.py` (line 75 — existing `test_derived_signals_keys_and_momentum_matches_slope`)
- Test: `tests/test_trends_symbols_transforms.py`

**Interfaces:**
- Consumes: existing `_slope()`, `_acceleration()`, `_range_position()`, `_spike_z()`, `_volatility()` helpers
- Produces: `_seasonal_ratio(series: list[float]) -> float`, updated `DERIVED_SIGNAL_NAMES` tuple (adds `"seasonal_ratio"`), updated `derived_signals(series) -> dict[str, float]` (existing signals use `series[-13:]`, seasonal_ratio uses full series)

- [ ] **Step 1: Write failing tests for `_seasonal_ratio`**

Add to `tests/test_trends_symbols_transforms.py`:

```python
from src.data.trends_symbols import _seasonal_ratio

def test_seasonal_ratio_normal():
    # 39 weeks trailing at value 10, 13 weeks recent at value 20 → ratio 2.0
    series = [10.0] * 39 + [20.0] * 13
    assert _seasonal_ratio(series) == pytest.approx(2.0)

def test_seasonal_ratio_below_baseline():
    series = [20.0] * 39 + [10.0] * 13
    assert _seasonal_ratio(series) == pytest.approx(0.5)

def test_seasonal_ratio_zero_trailing():
    # Trailing portion all zeros → NaN (can't divide by zero baseline)
    series = [0.0] * 39 + [10.0] * 13
    assert math.isnan(_seasonal_ratio(series))

def test_seasonal_ratio_short_series():
    # Series shorter than 14 points → not enough trailing data → NaN
    series = [10.0] * 13
    assert math.isnan(_seasonal_ratio(series))

def test_seasonal_ratio_exact_boundary():
    # Exactly 14 points: 1 trailing + 13 recent
    series = [5.0] + [10.0] * 13
    assert _seasonal_ratio(series) == pytest.approx(10.0 / 5.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trends_symbols_transforms.py -k "seasonal_ratio" -v`
Expected: FAIL — `_seasonal_ratio` not importable

- [ ] **Step 3: Implement `_seasonal_ratio` and update `derived_signals`**

In `src/data/trends_symbols.py`, add after `_volatility` (after line 193):

```python
def _seasonal_ratio(series: list[float]) -> float:
    """Recent 13-week mean / trailing baseline mean.

    >1.0 = current interest above historical norm. Returns NaN if the
    trailing portion (everything before the last 13 points) averages zero
    or if the series is too short to split.
    """
    vals = [float(v) for v in series]
    if len(vals) < 14:
        return float("nan")
    recent = vals[-13:]
    trailing = vals[:-13]
    trailing_mean = sum(trailing) / len(trailing)
    if trailing_mean == 0.0:
        return float("nan")
    return sum(recent) / len(recent) / trailing_mean
```

Update `DERIVED_SIGNAL_NAMES` (line ~198):

```python
DERIVED_SIGNAL_NAMES = (
    "momentum",
    "acceleration",
    "range_position",
    "spike",
    "volatility",
    "seasonal_ratio",
)
```

Update `derived_signals` function (line ~207) — existing signals use `series[-13:]`, new one uses full series:

```python
def derived_signals(series) -> dict[str, float]:
    vals = list(series)
    recent = vals[-13:] if len(vals) >= 13 else vals
    return {
        "momentum": _slope(recent),
        "acceleration": _acceleration(recent),
        "range_position": _range_position(recent),
        "spike": _spike_z(recent),
        "volatility": _volatility(recent),
        "seasonal_ratio": _seasonal_ratio(vals),
    }
```

- [ ] **Step 4: Update existing derived_signals test for new key**

In `tests/test_trends_symbols_transforms.py`, update `test_derived_signals_keys_and_momentum_matches_slope`:

```python
def test_derived_signals_keys_and_momentum_matches_slope():
    series = [1, 2, 1, 3, 2, 4, 3, 5]
    out = derived_signals(series)
    assert set(out) == set(DERIVED_SIGNAL_NAMES)
    assert out["momentum"] == _slope(series)
    # Short series → seasonal_ratio is NaN, all others are real
    assert math.isnan(out["seasonal_ratio"])
    assert not any(math.isnan(v) for k, v in out.items() if k != "seasonal_ratio")
```

Add a 52-week test:

```python
def test_derived_signals_52w_includes_seasonal():
    series = [10.0] * 39 + [15.0] * 13
    out = derived_signals(series)
    assert set(out) == set(DERIVED_SIGNAL_NAMES)
    assert out["seasonal_ratio"] == pytest.approx(1.5)
    # momentum uses only trailing 13 weeks (flat) → ~0
    assert abs(out["momentum"]) < 0.01
```

- [ ] **Step 5: Run all tests and verify they pass**

Run: `pytest tests/test_trends_symbols_transforms.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_transforms.py
git commit -m "feat: add seasonal_ratio derived signal (12-month baseline)"
```

---

### Task 2: Schema change + state.py + backup.py

**Files:**
- Modify: `src/state.py` (lines 59-65 — DDL, lines 96-108 — init_db, lines 215-230 — save_scan INSERT, lines 314-329 — get_sentiment_signals query)
- Modify: `src/backup.py` (line 29 — `_COLUMNS["sentiment_signals"]`)
- Test: `tests/test_state_smoke.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `sentiment_signals` table gains `text_value TEXT` column; `save_scan` accepts DataFrames with optional `text_value` column; `get_sentiment_signals_for_latest_scan` returns `text_value` in results

- [ ] **Step 1: Write failing test for schema**

Add to `tests/test_state_smoke.py`:

```python
def test_sentiment_signals_ddl_includes_text_value():
    from src.state import _DDL_STATEMENTS
    ddl = " ".join(_DDL_STATEMENTS)
    assert "text_value" in ddl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_smoke.py::test_sentiment_signals_ddl_includes_text_value -v`
Expected: FAIL

- [ ] **Step 3: Update DDL in `src/state.py`**

Change the `sentiment_signals` DDL (line 59):

```python
    """
    CREATE TABLE IF NOT EXISTS sentiment_signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        region      TEXT NOT NULL,
        gics_sector TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        value       REAL,
        text_value  TEXT
    )
    """,
```

- [ ] **Step 4: Add ALTER TABLE migration in `init_db`**

After the DDL loop in `init_db()` (after line 107), add:

```python
            cur.execute(
                "ALTER TABLE sentiment_signals "
                "ADD COLUMN IF NOT EXISTS text_value TEXT"
            )
```

- [ ] **Step 5: Update `save_scan` INSERT statement**

In `save_scan` (lines 215-230), change the sentiment_signals insert to include `text_value`:

```python
            if sentiment_signals_df is not None and not sentiment_signals_df.empty:
                sent_rows = [
                    (
                        scan_id,
                        row["region"],
                        row["gics_sector"],
                        row["signal_name"],
                        _to_float_or_none(row.get("value")),
                        row.get("text_value") or None,
                    )
                    for _, row in sentiment_signals_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO sentiment_signals "
                    "(scan_id, region, gics_sector, signal_name, value, text_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    sent_rows,
                )
```

- [ ] **Step 6: Update `get_sentiment_signals_for_latest_scan` query**

In `get_sentiment_signals_for_latest_scan` (line 322), add `text_value` to SELECT:

```python
    return pd.read_sql_query(
        """
        SELECT ss.region, ss.gics_sector, ss.signal_name, ss.value, ss.text_value
        FROM sentiment_signals ss
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON ss.scan_id = m.max_id
        """,
        conn,
    )
```

- [ ] **Step 7: Update backup.py columns**

In `src/backup.py` line 29, update:

```python
    "sentiment_signals": ("scan_id", "region", "gics_sector", "signal_name", "value", "text_value"),
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_state_smoke.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/state.py src/backup.py tests/test_state_smoke.py
git commit -m "feat: add text_value column to sentiment_signals schema"
```

---

### Task 3: `fetch_rising_queries` + tests

**Files:**
- Modify: `src/data/trends_symbols.py` (add new function after `fetch_comparative_interest`, around line 506)
- Create: `tests/test_rising_queries.py`

**Interfaces:**
- Consumes: `_new_client()`, `_symbols_by_region()`, `DEFAULT_REGION_GEOS`, `batch_key` from `trends_cache`
- Produces: `fetch_rising_queries(symbol_map, client, timeframe, sleep_s, max_retries, entities, region_geos, cache) -> dict[str, list[dict]]` where each value is `[{"query": str, "growth": str}, ...]`

- [ ] **Step 1: Write failing tests**

Create `tests/test_rising_queries.py`:

```python
"""Tests for fetch_rising_queries."""
import math
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.trends_symbols import fetch_rising_queries


def _mock_client(rising_df=None):
    """Return a mock pytrends client with configurable related_queries output."""
    client = MagicMock()
    if rising_df is None:
        rising_df = pd.DataFrame({
            "query": ["nvidia stock", "ai etf", "semiconductor etf", "tech stocks", "apple stock price"],
            "value": [2400, 1800, 900, 500, "Breakout"],
        })
    client.related_queries.return_value = {
        # related_queries returns {term: {"top": df, "rising": df}}
        # The term key varies per call — we use a catch-all via side_effect below
    }
    def _rq():
        # build_payload was called with [term]; extract term for the key
        term = client.build_payload.call_args[0][0][0]
        return {term: {"top": pd.DataFrame(), "rising": rising_df}}
    client.related_queries = _rq
    return client


def test_fetch_rising_queries_basic():
    symbol_map = {"US|Technology": ["XLK", "VGT"], "US|Energy": ["XLE"]}
    client = _mock_client()
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    assert "US|Technology" in result
    assert "US|Energy" in result
    assert len(result["US|Technology"]) <= 5
    assert result["US|Technology"][0]["query"] == "nvidia stock"
    assert result["US|Technology"][-1]["growth"] == "Breakout"


def test_fetch_rising_queries_empty_results():
    symbol_map = {"US|Technology": ["XLK"]}
    client = _mock_client(rising_df=pd.DataFrame(columns=["query", "value"]))
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    assert result.get("US|Technology", []) == []


def test_fetch_rising_queries_fail_open():
    symbol_map = {"US|Technology": ["XLK"]}
    client = MagicMock()
    client.build_payload.side_effect = Exception("429 Too Many Requests")
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    # Should not raise; returns empty or missing key
    assert result.get("US|Technology", []) == []


def test_fetch_rising_queries_cache_hit():
    symbol_map = {"US|Technology": ["XLK"]}
    cached_data = [{"query": "cached query", "growth": "100%"}]
    cache = {"rising_US": {"XLK": cached_data}}
    client = MagicMock()
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]}, cache=cache,
    )
    assert result["US|Technology"] == cached_data
    # Client should not have been called (cache hit)
    client.build_payload.assert_not_called()


def test_fetch_rising_queries_uses_entity_mid():
    symbol_map = {"US|Technology": ["XLK"]}
    entities = {"XLK": "/m/0xyz_tech"}
    client = _mock_client()
    fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        entities=entities, region_geos={"US": ["US"]},
    )
    # build_payload should have been called with the entity mid, not ticker
    call_args = client.build_payload.call_args[0][0]
    assert call_args == ["/m/0xyz_tech"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rising_queries.py -v`
Expected: FAIL — `fetch_rising_queries` not importable

- [ ] **Step 3: Implement `fetch_rising_queries`**

Add to `src/data/trends_symbols.py` after `fetch_comparative_interest` (after line 506):

```python
def fetch_rising_queries(
    symbol_map: dict[str, list[str]],
    client=None,
    timeframe: str = "today 12-m",
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, list[dict]]:
    """Fetch rising/breakout queries per sector via pytrends related_queries.

    One call per sector (primary representative term) per geo. Returns
    {region|sector: [{"query": str, "growth": str}, ...]} with up to 5
    entries per sector. Entirely fail-open.
    """
    if not symbol_map:
        return {}
    if client is None:
        try:
            client = _new_client()
        except Exception as exc:
            logger.warning("Trends client init failed (%s) — rising queries skipped", exc)
            return {}

    entities = entities or {}
    region_geos = region_geos if region_geos is not None else DEFAULT_REGION_GEOS

    sectors_by_region: dict[str, list[str]] = {}
    rep_term: dict[str, dict[str, str]] = {}
    for key, symbols in sorted(symbol_map.items()):
        region, _, sector = key.partition("|")
        sectors_by_region.setdefault(region, []).append(sector)
        ticker = symbols[0]
        rep_term.setdefault(region, {})[sector] = entities.get(ticker, ticker)

    result: dict[str, list[dict]] = {}
    for region, sectors in sectors_by_region.items():
        geos = region_geos.get(region, [""])
        for sector in sectors:
            term = rep_term[region][sector]
            sector_key = f"{region}|{sector}"
            cache_ns = f"rising_{region}"

            if cache is not None:
                cached = cache.get(cache_ns, {}).get(term)
                if isinstance(cached, list):
                    result[sector_key] = cached
                    continue

            per_geo_rising: list[dict] = []
            for geo in geos:
                for attempt in range(max_retries):
                    try:
                        client.build_payload([term], timeframe=timeframe, geo=geo)
                        rq = client.related_queries()
                        rising_df = rq.get(term, {}).get("rising")
                        if rising_df is not None and not rising_df.empty:
                            for _, row in rising_df.head(5).iterrows():
                                q = str(row.get("query", ""))
                                g = str(row.get("value", ""))
                                if g != "Breakout":
                                    g = f"{g}%"
                                per_geo_rising.append({"query": q, "growth": g})
                        break
                    except Exception as exc:
                        if attempt < max_retries - 1:
                            time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                        else:
                            logger.warning(
                                "Rising queries for %s (geo=%s) failed (%s)",
                                sector_key, geo or "world", exc,
                            )

            # Deduplicate across geos: keep unique queries, highest growth first
            seen: set[str] = set()
            deduped: list[dict] = []
            for entry in per_geo_rising:
                if entry["query"] not in seen:
                    seen.add(entry["query"])
                    deduped.append(entry)
            deduped = deduped[:5]

            if deduped:
                result[sector_key] = deduped
                if cache is not None:
                    cache.setdefault(cache_ns, {})[term] = deduped

            if sleep_s:
                time.sleep(sleep_s)

    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_rising_queries.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_rising_queries.py
git commit -m "feat: add fetch_rising_queries for emerging search terms"
```

---

### Task 4: Wire into scan.py (timeframe + rising queries)

**Files:**
- Modify: `scan.py` (lines 315-317 — fetch_symbol_trends call, lines 362-365 — after comparative interest block)

**Interfaces:**
- Consumes: `fetch_rising_queries` from Task 3, `derived_signals` with 52-week support from Task 1, `text_value` column from Task 2
- Produces: `sentiment_signals_df` with `seasonal_ratio` rows + `rising_queries` text_value rows passed to `save_scan`

- [ ] **Step 1: Update `fetch_symbol_trends` call to 12-month window**

In `scan.py` line 315, change:

```python
    _trends_by_key = fetch_symbol_trends(
        _symbol_map, anchor=_anchor, entities=_entities, region_geos=_region_geos,
        cache=_cache, timeframe="today 12-m", window=52,
    )
```

- [ ] **Step 2: Add Step 8c — rising queries**

After the comparative interest block (after line 362, before `if _use_cache:`), add:

```python
    # ------------------------------------------------------------------
    # Step 8c: Rising / breakout queries per sector
    # ------------------------------------------------------------------
    logger.info("Fetching rising queries …")
    from src.data.trends_symbols import fetch_rising_queries
    try:
        _rising = fetch_rising_queries(
            _symbol_map, sleep_s=20.0, max_retries=3,
            entities=_entities, region_geos=_region_geos, cache=_cache,
        )
        if _rising:
            import json as _json
            _rising_rows = []
            for _key, _queries in _rising.items():
                _region, _, _sector = _key.partition("|")
                _rising_rows.append({
                    "region": _region, "gics_sector": _sector,
                    "signal_name": "rising_queries", "value": None,
                    "text_value": _json.dumps(_queries),
                })
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_rising_rows)],
                ignore_index=True,
            )
            logger.info("Rising queries: %d sectors with results", len(_rising))
        else:
            logger.info("Rising queries: no results (skipped or failed)")
    except Exception as exc:
        logger.warning("Rising queries failed (%s) — continuing without", exc)
```

- [ ] **Step 3: Ensure `text_value` column exists in sentiment_signals_df**

The derived-signals loop (line 326-333) creates rows without `text_value`. The concat with rising_queries rows (which have `text_value`) will auto-fill NaN for the missing column. This is handled by pandas automatically — no code change needed, but verify by running the test suite.

- [ ] **Step 4: Update `fetch_symbol_trends` default parameters**

In `src/data/trends_symbols.py`, update the function defaults (line 564-575):

```python
def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = DEFAULT_ANCHOR,
    client=None,
    timeframe: str = "today 12-m",
    window: int = 52,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, pd.Series]:
```

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add scan.py src/data/trends_symbols.py
git commit -m "feat: wire 12-month timeframe and rising queries into scan"
```

---

### Task 5: Dashboard — seasonal column + rising queries panel + i18n

**Files:**
- Modify: `dashboard/sentiment.py` (lines 8-43)
- Modify: `dashboard/templates/sentiment.html.j2` (lines 76-108 — signals table)
- Modify: `dashboard/templates/_i18n.html.j2` (lines 55-63 — SV translations for sent_col_*)
- Modify: `dashboard/templates/sentiment.html.j2` (lines 20-66 — guide)
- Test: `tests/test_dashboard_js.py`

**Interfaces:**
- Consumes: `_build_sentiment_signal_rows(sent_df)` receives DataFrame with `text_value` column and `seasonal_ratio` signal rows
- Produces: each row dict gains `seasonal_ratio` (formatted as `"1.32x"`) and `rising_queries` (list of `{"query": str, "growth": str}` or empty list); template renders seasonal column + expandable panel

- [ ] **Step 1: Write failing test for seasonal column in sentiment rows**

Add to `tests/test_dashboard_js.py`:

```python
def test_sentiment_row_includes_seasonal_ratio():
    import pandas as pd
    from dashboard.sentiment import _build_sentiment_signal_rows
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Technology", "signal_name": "momentum", "value": 0.5, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "acceleration", "value": 0.1, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "range_position", "value": 0.7, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "spike", "value": 1.2, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "volatility", "value": 0.3, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "seasonal_ratio", "value": 1.32, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "attention_level", "value": 42.5, "text_value": None},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["seasonal_ratio"] == "1.32x"


def test_sentiment_row_includes_rising_queries():
    import json
    import pandas as pd
    from dashboard.sentiment import _build_sentiment_signal_rows
    queries = [{"query": "nvidia stock", "growth": "2400%"}]
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Technology", "signal_name": "momentum", "value": 0.5, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "acceleration", "value": 0.1, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "range_position", "value": 0.7, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "spike", "value": 1.2, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "volatility", "value": 0.3, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "seasonal_ratio", "value": 1.32, "text_value": None},
        {"region": "US", "gics_sector": "Technology", "signal_name": "rising_queries", "value": None, "text_value": json.dumps(queries)},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert rows[0]["rising_queries"] == queries
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_js.py -k "seasonal_ratio or rising_queries" -v`
Expected: FAIL

- [ ] **Step 3: Update `_build_sentiment_signal_rows` in `dashboard/sentiment.py`**

```python
"""Sentiment-specific data builders."""

from __future__ import annotations

import json
import math


def _build_sentiment_signal_rows(sent_df) -> list[dict]:
    """Pivot derived sentiment signals into one display row per sector-key.

    Each row: region, sector, and the derived metrics formatted for the
    template. Sorted by momentum descending so the leaders sit on top. Returns
    [] when no sentiment_signals rows exist (older scans / dry runs).
    """
    if sent_df is None or sent_df.empty:
        return []

    def _fmt(v, pct=False):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v * 100:.0f}%" if pct else f"{v:+.2f}"

    def _fmt_attn(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.1f}"

    def _fmt_seasonal(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.2f}x"

    rows = []
    for (region, sector), grp in sent_df.groupby(["region", "gics_sector"]):
        vals = dict(zip(grp["signal_name"], grp["value"]))
        # Parse rising_queries from text_value column
        rising = []
        if "text_value" in grp.columns:
            rq_rows = grp[grp["signal_name"] == "rising_queries"]
            if not rq_rows.empty:
                tv = rq_rows.iloc[0].get("text_value")
                if tv and isinstance(tv, str):
                    try:
                        rising = json.loads(tv)
                    except (json.JSONDecodeError, TypeError):
                        pass

        rows.append({
            "region": region,
            "sector": sector,
            "_momentum": vals.get("momentum") or 0.0,
            "momentum": _fmt(vals.get("momentum")),
            "acceleration": _fmt(vals.get("acceleration")),
            "range_position": _fmt(vals.get("range_position"), pct=True),
            "spike": _fmt(vals.get("spike")),
            "volatility": _fmt(vals.get("volatility"), pct=True),
            "attention": _fmt_attn(vals.get("attention_level")),
            "seasonal_ratio": _fmt_seasonal(vals.get("seasonal_ratio")),
            "rising_queries": rising,
        })
    rows.sort(key=lambda r: r["_momentum"], reverse=True)
    return rows
```

- [ ] **Step 4: Run dashboard tests**

Run: `pytest tests/test_dashboard_js.py -v`
Expected: all PASS

- [ ] **Step 5: Update sentiment template — seasonal column + rising queries panel**

In `dashboard/templates/sentiment.html.j2`, update the signals table (lines 79-107):

Add `<th data-i18n="sent_col_seasonal">Seasonal</th>` after the Attention header and before `</tr>`. Add an empty expand header `<th></th>` at the start of the row for the toggle control.

Update each `<tr>` in the tbody to include the seasonal column plus an expand button and hidden rising-queries panel:

```html
      <thead>
        <tr>
          <th></th>
          <th data-i18n="sent_col_sector">Sector</th>
          <th data-i18n="sent_col_region">Region</th>
          <th data-i18n="sent_col_momentum">Momentum</th>
          <th data-i18n="sent_col_acceleration">Acceleration</th>
          <th data-i18n="sent_col_range">Range&nbsp;pos.</th>
          <th data-i18n="sent_col_spike">Spike</th>
          <th data-i18n="sent_col_volatility">Volatility</th>
          <th data-i18n="sent_col_attention">Attention</th>
          <th data-i18n="sent_col_seasonal">Seasonal</th>
        </tr>
      </thead>
      <tbody>
        {% for r in sentiment_signal_rows %}
        <tr{% if r.rising_queries %} class="has-rising" data-rising-id="rising-{{ loop.index }}" tabindex="0"{% endif %}>
          <td class="rising-toggle">{% if r.rising_queries %}&#x25B6;{% endif %}</td>
          <td>{{ r.sector }}</td>
          <td>{{ r.region }}</td>
          <td>{{ r.momentum }}</td>
          <td>{{ r.acceleration }}</td>
          <td>{{ r.range_position }}</td>
          <td>{{ r.spike }}</td>
          <td>{{ r.volatility }}</td>
          <td>{{ r.attention }}</td>
          <td class="seasonal {% if r.seasonal_ratio != '—' %}{% set sv = r.seasonal_ratio|replace('x','')|float %}{% if sv > 1.0 %}seasonal-hi{% elif sv < 1.0 %}seasonal-lo{% endif %}{% endif %}">{{ r.seasonal_ratio }}</td>
        </tr>
        {% if r.rising_queries %}
        <tr class="rising-panel" id="rising-{{ loop.index }}" style="display:none">
          <td colspan="10">
            <div class="rising-inner">
              <strong data-i18n="rising_heading">Rising Queries</strong>
              <table class="rising-table">
                <thead><tr>
                  <th data-i18n="rising_col_query">Query</th>
                  <th data-i18n="rising_col_growth">Growth</th>
                </tr></thead>
                <tbody>
                  {% for q in r.rising_queries %}
                  <tr><td>{{ q.query }}</td><td>{{ q.growth }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
        {% endif %}
        {% endfor %}
      </tbody>
```

- [ ] **Step 6: Add rising-queries toggle JS + styles**

In the `<script>` block of `sentiment.html.j2`, add a delegated click/keyboard listener:

```javascript
document.addEventListener('click', function(e) {
  var row = e.target.closest('.has-rising');
  if (!row) return;
  var panelId = row.getAttribute('data-rising-id');
  var panel = document.getElementById(panelId);
  if (!panel) return;
  var isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'table-row';
  row.querySelector('.rising-toggle').textContent = isOpen ? '▶' : '▼';
});
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  var row = e.target.closest('.has-rising');
  if (!row) return;
  e.preventDefault();
  row.click();
});
```

Add styles in `_style.html.j2` or inline in the template:

```css
.has-rising { cursor: pointer; }
.rising-toggle { width: 1.2em; text-align: center; color: var(--fg4); }
.rising-inner { padding: 8px 16px; }
.rising-table { margin-top: 4px; font-size: 0.85rem; }
.rising-table th { text-align: left; font-weight: 600; padding: 2px 12px 2px 0; }
.rising-table td { padding: 2px 12px 2px 0; }
.seasonal-hi { color: var(--bull, #8FA77A); }
.seasonal-lo { color: var(--bear, #BF6F50); }
```

- [ ] **Step 7: Add i18n translations**

In `dashboard/templates/_i18n.html.j2`, add SV translations alongside the existing `sent_col_*` keys (around line 63):

```javascript
    sent_col_seasonal: "Säsong",
    rising_heading: "Stigande sökningar",
    rising_col_query: "Sökterm",
    rising_col_growth: "Tillväxt",
```

- [ ] **Step 8: Update guide text**

In the guide body in `sentiment.html.j2` (lines 46-59), add two new `<li>` entries inside the derived-signals `<ul>`:

```html
        <li><strong>Seasonal</strong> — ratio of recent 13-week interest to the
            prior 39-week baseline. Above 1.0× = interest is higher than its own
            trailing norm; below 1.0× = seasonal dip or fading attention.</li>
        <li><strong>Rising queries</strong> — emerging search terms surfaced by
            Google Trends for each sector's representative ETF or entity.
            Click a row to expand. Growth is percent increase or "Breakout"
            (>5,000%).</li>
```

Add corresponding SV translations in the `guide_body_sentiment` SV block in `_i18n.html.j2`.

- [ ] **Step 9: Run full test suite**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 10: Verify dashboard builds locally**

Run: `python3 dashboard/build.py`
Expected: builds successfully (seasonal_ratio column may show "—" if no 52-week data in current DB — that's correct)

- [ ] **Step 11: Commit**

```bash
git add dashboard/sentiment.py dashboard/templates/sentiment.html.j2 dashboard/templates/_i18n.html.j2 tests/test_dashboard_js.py
git commit -m "feat: sentiment dashboard — seasonal column + rising queries panel"
```

---

### Task 6: BACKLOG.md update + final verification + PR

**Files:**
- Modify: `BACKLOG.md` (move sentiment enrichment items to Done)

**Interfaces:**
- Consumes: all prior tasks complete
- Produces: updated backlog, PR

- [ ] **Step 1: Update BACKLOG.md**

In the "Sentiment page — enrichment" section, mark the two shipped items with strikethrough:

```markdown
- ~~**Longer window for a seasonal baseline.**~~ *(done — see Done)* Extended
  Trends fetch to 12 months (52 weeks). New `seasonal_ratio` signal compares
  recent 13-week interest to the prior 39-week baseline.
- ~~**Rising / breakout queries.**~~ *(done — see Done)*
  `fetch_rising_queries()` calls `related_queries()` per sector, surfaces
  top 5 emerging terms in an expandable panel on the sentiment page.
```

Add to the Done section:

```markdown
- ~~Sentiment enrichment — seasonal baseline + rising queries~~ — extended
  Trends fetch from 3-month to 12-month (52-week series); existing derived
  signals still use trailing 13 weeks. New `seasonal_ratio` signal (recent
  13-week mean / prior 39-week baseline). `fetch_rising_queries()` surfaces
  top 5 emerging search terms per sector via `pytrends.related_queries()`.
  `text_value TEXT` column added to `sentiment_signals` for JSON storage.
  Sentiment page gains Seasonal column + expandable rising-queries panel
  per row (EN+SV). Both info-only, no composite impact. *(2026-07-12)*
```

- [ ] **Step 2: Run full test suite one last time**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 3: Commit and push**

```bash
git add BACKLOG.md
git commit -m "docs: mark sentiment enrichment items done in backlog"
git push -u origin feature/sentiment-enrichment
```

- [ ] **Step 4: Create PR**

```bash
gh pr create --title "feat: sentiment enrichment — seasonal baseline + rising queries" --body "$(cat <<'EOF'
## Summary
- Extended Trends fetch from 3-month to 12-month (52-week series)
- Existing derived signals still operate on trailing 13 weeks (no behavior change)
- New `seasonal_ratio` signal: recent 13-week mean / prior 39-week baseline
- New `fetch_rising_queries()`: surfaces top 5 emerging search terms per sector via `pytrends.related_queries()`
- `text_value TEXT` column added to `sentiment_signals` for JSON storage
- Sentiment page: Seasonal column + expandable rising-queries panel (EN+SV)
- Both features info-only — no composite score impact

## Test plan
- [ ] `test_seasonal_ratio` — normal, zero-trailing, short, boundary cases
- [ ] `test_derived_signals_52w` — 52-week input returns all 6 signals
- [ ] `test_fetch_rising_queries` — basic, empty, fail-open, cache, entity-mid
- [ ] `test_sentiment_row_includes_seasonal_ratio` — dashboard formatting
- [ ] `test_sentiment_row_includes_rising_queries` — JSON parse + display
- [ ] `test_sentiment_signals_ddl_includes_text_value` — schema check
- [ ] Full `pytest` green
- [ ] `python3 dashboard/build.py` succeeds

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
