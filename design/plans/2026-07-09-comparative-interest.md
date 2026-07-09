# Comparative Cross-Sector Interest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a comparative Google Trends fetch pass that puts one representative term per sector into overlapping chained batches, producing a single-scale `attention_level` signal per region|sector, displayed on the sentiment dashboard page.

**Architecture:** A new `fetch_comparative_interest()` function in `src/data/trends_symbols.py` builds overlapping 5-term batches (anchor-chaining), fetches via the existing `_fetch_geo` machinery, rescales all batches onto a common scale, and returns one float per sector. The result is stored as `signal_name="attention_level"` rows in the existing `sentiment_signals` table (no schema change). The sentiment dashboard page gains a new "Attention" column.

**Tech Stack:** Python 3, pytrends, pandas, numpy, Jinja2, pytest

## Global Constraints

- No schema changes — `attention_level` rows go into the existing `sentiment_signals` table.
- No composite/ranking impact — attention is info-only, like acceleration/spike/volatility.
- Per-region isolation — US and EU are on independent scales; no cross-region comparison.
- Day-cache participation — comparative batches use the same `batch_key()` keying as existing fetches.
- 5-term Google Trends payload limit per API call.
- `DERIVED_SIGNAL_NAMES` tuple in `trends_symbols.py` is not changed — `attention_level` is not a per-series derived signal; it's a cross-sector comparative signal with its own fetch pass.

---

### Task 1: `_build_chained_batches` helper + tests

**Files:**
- Modify: `src/data/trends_symbols.py` (append after `_rekey_by_ticker`, around line 336)
- Create: `tests/test_comparative_interest.py`

**Interfaces:**
- Consumes: nothing (pure helper)
- Produces: `_build_chained_batches(terms: list[str], batch_size: int = 5) -> list[list[str]]`
  Returns a list of overlapping batches where the last term of batch N is the first term of batch N+1 (the bridge).

- [ ] **Step 1: Write the failing tests**

In `tests/test_comparative_interest.py`:

```python
import math
import pytest
from src.data.trends_symbols import _build_chained_batches


def test_chained_batches_11_terms():
    terms = [f"S{i}" for i in range(11)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [
        ["S0", "S1", "S2", "S3", "S4"],
        ["S4", "S5", "S6", "S7", "S8"],
        ["S8", "S9", "S10"],
    ]


def test_chained_batches_5_terms_single_batch():
    terms = [f"S{i}" for i in range(5)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [["S0", "S1", "S2", "S3", "S4"]]


def test_chained_batches_3_terms():
    terms = ["A", "B", "C"]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [["A", "B", "C"]]


def test_chained_batches_6_terms():
    terms = [f"S{i}" for i in range(6)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [
        ["S0", "S1", "S2", "S3", "S4"],
        ["S4", "S5"],
    ]


def test_chained_batches_1_term():
    batches = _build_chained_batches(["only"], batch_size=5)
    assert batches == [["only"]]


def test_chained_batches_empty():
    batches = _build_chained_batches([], batch_size=5)
    assert batches == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_comparative_interest.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_chained_batches'`

- [ ] **Step 3: Implement `_build_chained_batches`**

In `src/data/trends_symbols.py`, after the `_rekey_by_ticker` function (around line 336), add:

```python
def _build_chained_batches(terms: list[str], batch_size: int = 5) -> list[list[str]]:
    """Split terms into overlapping batches for anchor-chaining.

    The last term of batch N becomes the first term of batch N+1 (the bridge).
    With batch_size=5 and 11 terms: [[S0..S4], [S4..S8], [S8..S10]].
    """
    if not terms:
        return []
    if len(terms) <= batch_size:
        return [list(terms)]
    batches: list[list[str]] = []
    stride = batch_size - 1
    i = 0
    while i < len(terms):
        batch = terms[i : i + batch_size]
        batches.append(batch)
        i += stride
        if i >= len(terms):
            break
    return batches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_comparative_interest.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_comparative_interest.py
git commit -m "feat: add _build_chained_batches helper for comparative interest"
```

---

### Task 2: `_rescale_chain` helper + tests

**Files:**
- Modify: `src/data/trends_symbols.py` (append after `_build_chained_batches`)
- Modify: `tests/test_comparative_interest.py`

**Interfaces:**
- Consumes: output of `_build_chained_batches` (to know bridge terms)
- Produces: `_rescale_chain(batch_results: list[dict[str, float]], batches: list[list[str]]) -> dict[str, float]`
  Takes per-batch `{term: mean_interest}` dicts and the batch lists (to identify bridges), returns all terms on a single common scale. Zero-bridge → `NaN` for downstream terms.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comparative_interest.py`:

```python
from src.data.trends_symbols import _rescale_chain


def test_rescale_chain_two_batches():
    batches = [["A", "B", "C"], ["C", "D", "E"]]
    batch_results = [
        {"A": 50.0, "B": 30.0, "C": 100.0},
        {"C": 50.0, "D": 25.0, "E": 75.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert merged["B"] == 30.0
    assert merged["C"] == 100.0
    # batch1 scale factor = 100/50 = 2.0
    assert merged["D"] == 50.0
    assert merged["E"] == 150.0


def test_rescale_chain_three_batches():
    batches = [["A", "B", "C"], ["C", "D", "E"], ["E", "F"]]
    batch_results = [
        {"A": 10.0, "B": 20.0, "C": 40.0},
        {"C": 20.0, "D": 10.0, "E": 30.0},
        {"E": 15.0, "F": 45.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    # batch0: as-is. batch1 factor = 40/20 = 2.0
    assert merged["A"] == 10.0
    assert merged["D"] == 20.0   # 10 * 2
    assert merged["E"] == 60.0   # 30 * 2
    # batch2 factor = 60/15 = 4.0
    assert merged["F"] == 180.0  # 45 * 4


def test_rescale_chain_single_batch():
    batches = [["X", "Y"]]
    batch_results = [{"X": 80.0, "Y": 20.0}]
    merged = _rescale_chain(batch_results, batches)
    assert merged == {"X": 80.0, "Y": 20.0}


def test_rescale_chain_zero_bridge():
    batches = [["A", "B"], ["B", "C"]]
    batch_results = [
        {"A": 50.0, "B": 0.0},
        {"B": 0.0, "C": 30.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert merged["B"] == 0.0
    assert math.isnan(merged["C"])


def test_rescale_chain_zero_bridge_cascades():
    batches = [["A", "B"], ["B", "C"], ["C", "D"]]
    batch_results = [
        {"A": 50.0, "B": 0.0},
        {"B": 0.0, "C": 30.0},
        {"C": 10.0, "D": 20.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert math.isnan(merged["C"])
    assert math.isnan(merged["D"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_comparative_interest.py::test_rescale_chain_two_batches -v`
Expected: FAIL with `ImportError: cannot import name '_rescale_chain'`

- [ ] **Step 3: Implement `_rescale_chain`**

In `src/data/trends_symbols.py`, after `_build_chained_batches`:

```python
def _rescale_chain(
    batch_results: list[dict[str, float]],
    batches: list[list[str]],
) -> dict[str, float]:
    """Rescale per-batch mean-interest dicts onto batch 0's scale via bridge terms.

    batch_results[i] maps each term in batches[i] to its Google-reported mean.
    The bridge between batch i and i+1 is the last term of batch i (= first of
    batch i+1). If the bridge is zero in either batch, downstream terms get NaN.
    """
    if not batch_results:
        return {}
    merged: dict[str, float] = dict(batch_results[0])
    scale = 1.0
    broken = False
    for i in range(1, len(batch_results)):
        bridge = batches[i][0]
        bridge_prev = merged.get(bridge, 0.0)
        bridge_cur = batch_results[i].get(bridge, 0.0)
        if broken or bridge_prev == 0.0 or bridge_cur == 0.0:
            broken = True
            for term, val in batch_results[i].items():
                if term not in merged:
                    merged[term] = float("nan")
            continue
        scale = bridge_prev / bridge_cur
        for term, val in batch_results[i].items():
            if term not in merged:
                merged[term] = val * scale
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_comparative_interest.py -v`
Expected: all 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_comparative_interest.py
git commit -m "feat: add _rescale_chain helper for anchor-chained normalization"
```

---

### Task 3: `fetch_comparative_interest` main function + tests

**Files:**
- Modify: `src/data/trends_symbols.py` (append after `_rescale_chain`)
- Modify: `tests/test_comparative_interest.py`

**Interfaces:**
- Consumes: `_build_chained_batches`, `_rescale_chain`, `_fetch_geo`, `_average_geo_series`, `load_entities`, `build_symbol_map`, `_resolve_query_terms`
- Produces: `fetch_comparative_interest(symbol_map: dict[str, list[str]], client=None, timeframe: str = "today 3-m", window: int = 13, sleep_s: float = 20.0, max_retries: int = 3, entities: dict[str, str] | None = None, region_geos: dict[str, list[str]] | None = None, cache: dict | None = None) -> dict[str, float]`
  Returns `{"US|Technology": 85.3, "US|Energy": 42.1, ...}` — one attention_level per region|sector key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comparative_interest.py`:

```python
import pandas as pd
from src.data.trends_symbols import fetch_comparative_interest


class FakeComparativeClient:
    """Returns deterministic interest for comparative batches (no anchor term)."""
    def __init__(self):
        self._terms = []
        self._geo = ""

    def build_payload(self, kw_list, timeframe=None, geo=None):
        self._terms = list(kw_list)
        self._geo = geo

    def interest_over_time(self):
        data = {}
        for i, t in enumerate(self._terms):
            data[t] = [float((i + 1) * 10)] * 13
        return pd.DataFrame(data)


def test_fetch_comparative_interest_basic():
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE"],
        "US|Financials": ["XLF"],
    }
    result = fetch_comparative_interest(
        smap,
        client=FakeComparativeClient(),
        sleep_s=0.0,
        region_geos={"US": ["US"]},
    )
    assert "US|Technology" in result
    assert "US|Energy" in result
    assert "US|Financials" in result
    assert all(isinstance(v, float) for v in result.values())


def test_fetch_comparative_interest_uses_first_symbol():
    """Representative term is symbols[0] for each sector."""
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE", "IYE"],
    }

    class CapturingClient:
        def __init__(self):
            self.payloads = []
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self.payloads.append(list(kw_list))
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    client = CapturingClient()
    fetch_comparative_interest(
        smap, client=client, sleep_s=0.0, region_geos={"US": ["US"]},
    )
    all_terms = [t for p in client.payloads for t in p]
    assert "XLK" in all_terms
    assert "XLE" in all_terms
    assert "VGT" not in all_terms
    assert "IYE" not in all_terms


def test_fetch_comparative_interest_entity_resolution():
    """If an entity mid exists for symbols[0], use the mid instead."""
    smap = {"US|Technology": ["XLK"]}
    entities = {"XLK": "/m/tech_entity"}

    class CapturingClient:
        def __init__(self):
            self.payloads = []
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self.payloads.append(list(kw_list))
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    client = CapturingClient()
    fetch_comparative_interest(
        smap, client=client, sleep_s=0.0, entities=entities,
        region_geos={"US": ["US"]},
    )
    all_terms = [t for p in client.payloads for t in p]
    assert "/m/tech_entity" in all_terms
    assert "XLK" not in all_terms


def test_fetch_comparative_interest_empty_map():
    result = fetch_comparative_interest({}, sleep_s=0.0)
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_comparative_interest.py::test_fetch_comparative_interest_basic -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_comparative_interest'`

- [ ] **Step 3: Implement `fetch_comparative_interest`**

In `src/data/trends_symbols.py`, after `_rescale_chain`:

```python
def fetch_comparative_interest(
    symbol_map: dict[str, list[str]],
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, float]:
    """Comparative cross-sector interest via anchor-chained Google Trends batches.

    Puts one representative term per sector into overlapping 5-term payloads so
    Google's 0-100 normalization gives a true head-to-head ranking. Returns
    {region|sector: attention_level} on a single common scale per region.
    """
    if not symbol_map:
        return {}
    if client is None:
        try:
            client = _new_client()
        except Exception as exc:
            logger.warning("Trends client init failed (%s) — comparative interest skipped", exc)
            return {}

    entities = entities or {}
    region_geos = region_geos if region_geos is not None else DEFAULT_REGION_GEOS

    sectors_by_region: dict[str, list[str]] = {}
    rep_term: dict[str, dict[str, str]] = {}
    for key, symbols in sorted(symbol_map.items()):
        region, _, sector = key.partition("|")
        sectors_by_region.setdefault(region, []).append(sector)
        ticker = symbols[0]
        term = entities.get(ticker, ticker)
        rep_term.setdefault(region, {})[sector] = term

    result: dict[str, float] = {}
    for region, sectors in sectors_by_region.items():
        terms = [rep_term[region][s] for s in sectors]
        term_to_sector = {rep_term[region][s]: s for s in sectors}
        batches = _build_chained_batches(terms, batch_size=5)
        geos = region_geos.get(region, [""])

        per_geo_merged: list[dict[str, float]] = []
        for geo in geos:
            batch_results: list[dict[str, float]] = []
            for bi, batch in enumerate(batches):
                if cache is not None:
                    key = batch_key(batch)
                    cached = cache.get(f"cmp_{geo}", {}).get(key)
                    if isinstance(cached, dict):
                        batch_results.append(cached)
                        continue

                df = None
                for attempt in range(max_retries):
                    try:
                        client.build_payload(batch, timeframe=timeframe, geo=geo)
                        df = client.interest_over_time()
                        break
                    except Exception as exc:
                        if attempt < max_retries - 1:
                            time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                        else:
                            logger.warning(
                                "Comparative batch %d (geo=%s) failed (%s)",
                                bi + 1, geo or "world", exc,
                            )

                means: dict[str, float] = {}
                if df is not None and not df.empty:
                    for t in batch:
                        if t in df.columns:
                            series = df[t].tolist()[-window:]
                            means[t] = sum(series) / len(series) if series else 0.0
                else:
                    means = {t: 0.0 for t in batch}

                batch_results.append(means)
                if cache is not None:
                    cache.setdefault(f"cmp_{geo}", {})[batch_key(batch)] = means

                if bi < len(batches) - 1 and sleep_s:
                    time.sleep(sleep_s)

            merged = _rescale_chain(batch_results, batches)
            per_geo_merged.append(merged)

        if len(per_geo_merged) == 1:
            final = per_geo_merged[0]
        else:
            all_terms = list({t for m in per_geo_merged for t in m})
            final = {}
            for t in all_terms:
                vals = [m[t] for m in per_geo_merged if t in m and not math.isnan(m[t])]
                final[t] = sum(vals) / len(vals) if vals else float("nan")

        for term, val in final.items():
            sector = term_to_sector.get(term)
            if sector:
                result[f"{region}|{sector}"] = float(val)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_comparative_interest.py -v`
Expected: all 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_comparative_interest.py
git commit -m "feat: add fetch_comparative_interest with anchor-chained batches"
```

---

### Task 4: Wire `fetch_comparative_interest` into `scan.py`

**Files:**
- Modify: `scan.py:283-328` (after the existing sentiment block)

**Interfaces:**
- Consumes: `fetch_comparative_interest` from Task 3, existing `_symbol_map`, `_entities`, `_region_geos`, `_cache`, `sentiment_signals_df`
- Produces: `sentiment_signals_df` with additional `attention_level` rows appended

- [ ] **Step 1: Write the failing test**

Create `tests/test_comparative_scan_integration.py`:

```python
import pandas as pd
import pytest


def test_attention_rows_appended_to_sentiment_signals():
    """Verify that fetch_comparative_interest output is shaped correctly
    for the sentiment_signals_df format used by save_scan."""
    from src.data.trends_symbols import fetch_comparative_interest

    class FakeClient:
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    smap = {"US|Technology": ["XLK"], "US|Energy": ["XLE"]}
    attn = fetch_comparative_interest(
        smap, client=FakeClient(), sleep_s=0.0, region_geos={"US": ["US"]},
    )
    rows = []
    for key, val in attn.items():
        region, _, sector = key.partition("|")
        rows.append({
            "region": region,
            "gics_sector": sector,
            "signal_name": "attention_level",
            "value": val,
        })
    df = pd.DataFrame(rows)
    assert set(df.columns) == {"region", "gics_sector", "signal_name", "value"}
    assert (df["signal_name"] == "attention_level").all()
    assert len(df) == 2
```

- [ ] **Step 2: Run test to verify it passes** (this test validates the shape, not the wiring — it should pass already)

Run: `pytest tests/test_comparative_scan_integration.py -v`
Expected: PASS

- [ ] **Step 3: Wire into `scan.py`**

In `scan.py`, after the existing derived-signals block (after line 328 `sentiment_signals_df = pd.DataFrame(_sent_signal_rows)`), add:

```python
    # ------------------------------------------------------------------
    # Step 8b: Comparative cross-sector interest (attention_level)
    # ------------------------------------------------------------------
    logger.info("Fetching comparative cross-sector interest …")
    from src.data.trends_symbols import fetch_comparative_interest
    try:
        _attention = fetch_comparative_interest(
            _symbol_map, client=client, sleep_s=20.0, max_retries=3,
            entities=_entities, region_geos=_region_geos, cache=_cache,
        )
        if _attention:
            _attn_rows = []
            for _key, _val in _attention.items():
                _region, _, _sector = _key.partition("|")
                _attn_rows.append({
                    "region": _region, "gics_sector": _sector,
                    "signal_name": "attention_level", "value": _val,
                })
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_attn_rows)],
                ignore_index=True,
            )
            logger.info("Comparative interest: %d sectors scored", len(_attention))
        else:
            logger.info("Comparative interest: no results (skipped or failed)")
    except Exception as exc:
        logger.warning("Comparative interest failed (%s) — continuing without", exc)
```

Note: the `client` variable doesn't exist at this scope — the existing code creates a client inside `fetch_symbol_trends`. We need to create a fresh client here. Replace `client=client` with `client=None` (let `fetch_comparative_interest` create its own client):

```python
        _attention = fetch_comparative_interest(
            _symbol_map, sleep_s=20.0, max_retries=3,
            entities=_entities, region_geos=_region_geos, cache=_cache,
        )
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: all existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add scan.py tests/test_comparative_scan_integration.py
git commit -m "feat: wire comparative interest into scan pipeline"
```

---

### Task 5: Dashboard — add "Attention" column to sentiment page

**Files:**
- Modify: `dashboard/build.py:630-659` (`_build_sentiment_signal_rows`)
- Modify: `dashboard/templates/sentiment.html.j2:82-112` (table)
- Modify: `dashboard/templates/_i18n.html.j2` (SV translation)

**Interfaces:**
- Consumes: `sentiment_signals_df` from DB (now includes `attention_level` rows)
- Produces: updated sentiment page with an "Attention" column

- [ ] **Step 1: Write the failing test**

Create `tests/test_sentiment_attention_column.py`:

```python
import math
import pandas as pd
from dashboard.build import _build_sentiment_signal_rows


def test_attention_level_included_in_rows():
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Technology", "signal_name": "momentum", "value": 0.5},
        {"region": "US", "gics_sector": "Technology", "signal_name": "acceleration", "value": 0.1},
        {"region": "US", "gics_sector": "Technology", "signal_name": "range_position", "value": 0.7},
        {"region": "US", "gics_sector": "Technology", "signal_name": "spike", "value": 1.2},
        {"region": "US", "gics_sector": "Technology", "signal_name": "volatility", "value": 0.3},
        {"region": "US", "gics_sector": "Technology", "signal_name": "attention_level", "value": 85.3},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["attention"] == "85.3"


def test_attention_level_missing_shows_dash():
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Energy", "signal_name": "momentum", "value": -0.2},
        {"region": "US", "gics_sector": "Energy", "signal_name": "acceleration", "value": 0.0},
        {"region": "US", "gics_sector": "Energy", "signal_name": "range_position", "value": 0.5},
        {"region": "US", "gics_sector": "Energy", "signal_name": "spike", "value": 0.0},
        {"region": "US", "gics_sector": "Energy", "signal_name": "volatility", "value": 0.1},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["attention"] == "—"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sentiment_attention_column.py -v`
Expected: FAIL with `KeyError: 'attention'`

- [ ] **Step 3: Update `_build_sentiment_signal_rows` in `dashboard/build.py`**

In the `_build_sentiment_signal_rows` function (around line 630), update the row dict construction. Inside the `for (region, sector), grp in ...` loop, after `vals = dict(zip(grp["signal_name"], grp["value"]))`, the `rows.append(...)` block currently has five signal keys. Add `attention_level`:

```python
    def _fmt_attn(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.1f}"
```

And in the row dict, add:
```python
            "attention": _fmt_attn(vals.get("attention_level")),
```

The full updated function body for the row append:

```python
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
        })
```

- [ ] **Step 4: Update the sentiment template**

In `dashboard/templates/sentiment.html.j2`, add the column header after the Volatility `<th>`:

```html
          <th data-i18n="sent_col_attention">Attention</th>
```

And in the `<tbody>` row, after `<td>{{ r.volatility }}</td>`:

```html
          <td>{{ r.attention }}</td>
```

- [ ] **Step 5: Update the guide text**

In `dashboard/templates/sentiment.html.j2`, inside the `<ul>` block under "Derived signals" (around line 56), add a new `<li>` after the Volatility bullet:

```html
        <li><strong>Attention</strong> — relative search interest compared
            head-to-head against all other sectors in the same region.
            Higher = more attention.</li>
```

- [ ] **Step 6: Add i18n translation**

In `dashboard/templates/_i18n.html.j2`, add to the `SV` dict (after `sent_col_volatility`):

```javascript
    sent_col_attention: "Uppmärksamhet",
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_sentiment_attention_column.py tests/test_comparative_interest.py -v`
Expected: all PASS

- [ ] **Step 8: Rebuild dashboard locally to verify**

Run: `python3 dashboard/build.py`
Expected: builds without errors; `docs/sentiment.html` contains the new "Attention" column

- [ ] **Step 9: Commit**

```bash
git add dashboard/build.py dashboard/templates/sentiment.html.j2 dashboard/templates/_i18n.html.j2 tests/test_sentiment_attention_column.py
git commit -m "feat: add Attention column to sentiment dashboard page"
```

---

### Task 6: Final integration verification + backlog update

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: all prior tasks
- Produces: updated backlog, clean test suite

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: all tests PASS with no regressions

- [ ] **Step 2: Rebuild dashboard and visually verify**

Run: `python3 dashboard/build.py`
Open `docs/sentiment.html` in a browser. Verify:
- The "Attention" column appears in the derived signals table
- Values show as numbers (e.g. "85.3") or "—" for missing data
- The guide text includes the Attention bullet
- Swedish translation works for the column header

- [ ] **Step 3: Update BACKLOG.md**

Move the "Comparative cross-sector interest" item from Queued to Done with today's date (2026-07-09).

- [ ] **Step 4: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark comparative cross-sector interest done in backlog"
```

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin feature/comparative-interest
gh pr create --title "feat: comparative cross-sector interest (attention_level)" --body "$(cat <<'EOF'
## Summary
- Adds anchor-chained Google Trends comparative fetch: one representative term per sector in overlapping 5-term payloads, rescaled onto a common scale
- Stores `attention_level` as a new derived signal in `sentiment_signals` (no schema change)
- Adds "Attention" column to the sentiment dashboard page with i18n support
- Info-only signal — does not affect composite scoring or sector ranking

## Test plan
- [ ] `_build_chained_batches` — correct overlap, bridge terms, edge cases
- [ ] `_rescale_chain` — known inputs → expected output, zero-bridge → NaN
- [ ] `fetch_comparative_interest` — mock client, entity resolution, empty map
- [ ] Integration shape test — attention rows match sentiment_signals format
- [ ] Dashboard — Attention column renders, guide text updated, SV translation works
- [ ] Full test suite passes with no regressions

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
