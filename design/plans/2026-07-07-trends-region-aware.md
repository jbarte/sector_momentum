# Region-aware Google Trends pulls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Query US sectors in `geo="US"` and EU sectors averaged across `DE`/`FR`/`GB`, normalized against one stable ubiquitous anchor (`YouTube`) instead of the worldwide `SPY` pull.

**Architecture:** Partition `symbol_map` by region, run the existing per-batch fetch loop once per (region, geo), average a symbol's per-geo series for multi-geo regions, then merge to the same ticker-keyed dict `_aggregate` already consumes. Geo map and anchor come from `config/trends_geo.yaml` (in-code defaults if absent).

**Tech Stack:** Python 3.13, pytrends, pandas, PyYAML, pytest.

## Global Constraints

- Sentiment stays **toggle-only** — this feature does not touch the composite/ranking.
- Downstream functions `_normalize_by_anchor`, `_aggregate`, `score_symbol_sentiment`, `derived_signals` and the entity-mid helpers (`_resolve_query_terms`, `_rekey_by_ticker`, `load_entities`) must remain **unchanged** — they stay ticker-keyed.
- Default anchor becomes `"YouTube"` (was `"SPY"`); default region→geo map is `{"US": ["US"], "EU": ["DE", "FR", "GB"]}`. Define these once as module constants `DEFAULT_ANCHOR` and `DEFAULT_REGION_GEOS` and reuse everywhere (fetch default, config-loader fallback).
- **Not additive:** existing fetch/entity fake-client tests assert the old `geo=""`/`SPY` behavior and are **updated**, not merely added to (the change is intentional).
- Use `python3` to run pytest. Conventional commits, subject < 72 chars, footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do **not** `git add docs/` (generated artifact owned by CI).
- Record the branch test baseline with `python3 -m pytest -q` before Task 1 (6 skips are the psycopg2-less DB modules, or install `psycopg2-binary` to run them). No pre-existing test may regress.
- Spec: `design/specs/2026-07-07-trends-region-aware-design.md`.

## File Structure

- `src/data/trends_symbols.py` — add `_symbols_by_region`, `_average_geo_series`, `_fetch_geo`, `load_geo_config`, module constants; rewrite `fetch_symbol_trends` to be region-aware.
- `config/trends_geo.yaml` — new config (anchor + region→geo map).
- `scan.py` — load the geo config, pass `anchor` + `region_geos`, log geos per region.
- `dashboard/templates/sentiment.html.j2` + `_i18n.html.j2` — explainer copy (EN + SV).
- `tests/test_trends_symbols_region.py` — new test module (region partition, geo averaging, region-aware integration, config loader).
- `tests/test_trends_symbols_entities.py`, `tests/test_trends_symbols_fetch.py` — updated for the new anchor/geo defaults.
- `BACKLOG.md` — move the region-aware bullet to Done.

---

### Task 1: `_symbols_by_region` pure helper

**Files:**
- Modify: `src/data/trends_symbols.py` (add after `_aggregate`, near line 236)
- Test: `tests/test_trends_symbols_region.py` (create)

**Interfaces:**
- Produces: `_symbols_by_region(symbol_map: dict[str, list[str]]) -> dict[str, list[str]]` — groups the unique symbols under each `"REGION|Sector"` key by the region prefix, preserving first-seen order and de-duping within a region.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trends_symbols_region.py
from src.data.trends_symbols import _symbols_by_region


def test_symbols_by_region_groups_and_dedupes():
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE", "XLK"],       # XLK repeats within US
        "EU|Technology": ["EXV3.DE"],
    }
    out = _symbols_by_region(smap)
    assert out["US"] == ["XLK", "VGT", "XLE"]   # first-seen order, deduped
    assert out["EU"] == ["EXV3.DE"]
    assert set(out) == {"US", "EU"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -v`
Expected: FAIL with `ImportError: cannot import name '_symbols_by_region'`

- [ ] **Step 3: Implement**

```python
def _symbols_by_region(symbol_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """Group unique symbols by the region prefix of each 'REGION|Sector' key.

    Preserves first-seen order and de-dupes within each region.
    """
    out: dict[str, list[str]] = {}
    for key, symbols in symbol_map.items():
        region = key.split("|", 1)[0]
        bucket = out.setdefault(region, [])
        for s in symbols:
            if s not in bucket:
                bucket.append(s)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_region.py
git commit -m "feat: group trends symbols by region" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `_average_geo_series` pure helper

**Files:**
- Modify: `src/data/trends_symbols.py` (add after `_symbols_by_region`)
- Test: `tests/test_trends_symbols_region.py` (append)

**Interfaces:**
- Produces: `_average_geo_series(per_geo: list[dict[str, list[float]]], window: int) -> dict[str, list[float]]` — for each ticker across the per-geo maps, average element-wise over the geos where the ticker's series is *live* (has a non-zero value). A ticker live in no geo yields an all-zero series of length `window`. A ticker's series is assumed length `window` in every geo it appears.

- [ ] **Step 1: Write the failing tests**

```python
from src.data.trends_symbols import _average_geo_series


def test_average_geo_series_means_live_geos():
    per_geo = [
        {"EXV3.DE": [100.0, 100.0]},   # DE
        {"EXV3.DE": [200.0, 200.0]},   # FR
        {"EXV3.DE": [300.0, 300.0]},   # GB
    ]
    out = _average_geo_series(per_geo, window=2)
    assert out["EXV3.DE"] == [200.0, 200.0]   # mean of 100/200/300


def test_average_geo_series_skips_dead_geos():
    per_geo = [
        {"X": [10.0, 10.0]},
        {"X": [0.0, 0.0]},             # dead in this geo -> excluded from mean
        {"X": [30.0, 30.0]},
    ]
    out = _average_geo_series(per_geo, window=2)
    assert out["X"] == [20.0, 20.0]    # mean of 10 and 30 only


def test_average_geo_series_all_dead_is_zero():
    per_geo = [{"X": [0.0, 0.0]}, {"X": [0.0, 0.0]}]
    out = _average_geo_series(per_geo, window=2)
    assert out["X"] == [0.0, 0.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k average -v`
Expected: FAIL with `ImportError: cannot import name '_average_geo_series'`

- [ ] **Step 3: Implement**

```python
def _average_geo_series(
    per_geo: list[dict[str, list[float]]],
    window: int,
) -> dict[str, list[float]]:
    """Average each ticker's series across the geos where it is live.

    A series is live if it has any non-zero value. Tickers live in no geo
    yield an all-zero series of length `window`.
    """
    tickers: list[str] = []
    for m in per_geo:
        for t in m:
            if t not in tickers:
                tickers.append(t)
    out: dict[str, list[float]] = {}
    for t in tickers:
        live = [m[t] for m in per_geo if t in m and any(v != 0 for v in m[t])]
        if not live:
            out[t] = [0.0] * window
        else:
            arr = np.array(live, dtype=float)
            out[t] = [float(v) for v in arr.mean(axis=0)]
    return out
```

(`np` is already imported at the top of the module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k average -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_region.py
git commit -m "feat: average trends series across geos" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Extract per-geo fetch loop into `_fetch_geo` (pure refactor)

**Files:**
- Modify: `src/data/trends_symbols.py` — `fetch_symbol_trends` (lines 288-334)
- Test: `tests/test_trends_symbols_region.py` (append)

**Interfaces:**
- Produces: `_fetch_geo(client, symbols: list[str], anchor: str, geo: str, timeframe: str, window: int, batch_size: int, sleep_s: float, max_retries: int, entities: dict[str, str]) -> dict[str, list[float]]` — runs the existing batch loop for one geo and returns the anchor-normalized `{ticker: series}` map. `fetch_symbol_trends` calls it once (still `geo=""`, `anchor="SPY"`) so behavior is unchanged in this task.
- Consumes: `_resolve_query_terms`, `_rekey_by_ticker`, `_normalize_by_anchor` (existing).

- [ ] **Step 1: Write the failing test (records the geo passed through)**

```python
import pandas as pd
from src.data.trends_symbols import _fetch_geo


class _RecordingClient:
    """Records (kw_list, geo) per build_payload; returns a fixed frame."""
    def __init__(self, frame):
        self._frame = frame
        self.calls = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append((list(kw_list), geo))

    def interest_over_time(self):
        return self._frame


def test_fetch_geo_normalizes_and_passes_geo():
    frame = pd.DataFrame({"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]})
    client = _RecordingClient(frame)
    out = _fetch_geo(client, ["XLK"], anchor="SPY", geo="US", timeframe="today 3-m",
                     window=3, batch_size=4, sleep_s=0.0, max_retries=3, entities={})
    assert client.calls == [(["SPY", "XLK"], "US")]   # geo forwarded
    assert out["XLK"] == [50.0, 100.0, 200.0]         # normalized by SPY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k fetch_geo -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_geo'`

- [ ] **Step 3: Extract the helper and rewire `fetch_symbol_trends`**

Add `_fetch_geo` (place it just above `fetch_symbol_trends`):

```python
def _fetch_geo(
    client,
    symbols: list[str],
    anchor: str,
    geo: str,
    timeframe: str,
    window: int,
    batch_size: int,
    sleep_s: float,
    max_retries: int,
    entities: dict[str, str],
) -> dict[str, list[float]]:
    """Fetch + anchor-normalize one geo's symbols. Returns {ticker: series}."""
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    norm_by_symbol: dict[str, list[float]] = {}
    for bi, batch in enumerate(batches):
        query_terms, term_to_ticker = _resolve_query_terms(batch, entities)
        terms = [anchor] + query_terms
        df = None
        for attempt in range(max_retries):
            try:
                client.build_payload(terms, timeframe=timeframe, geo=geo)
                df = client.interest_over_time()
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                else:
                    logger.warning("Trends batch %d (geo=%s) failed (%s) — %d symbols neutral",
                                   bi + 1, geo or "world", exc, len(batch))
        if df is not None and not df.empty:
            raw_by_term = {t: [float(v) for v in df[t].tolist()[-window:]]
                           for t in terms if t in df.columns}
            raw = _rekey_by_ticker(raw_by_term, anchor, term_to_ticker)
            norm_by_symbol.update(_normalize_by_anchor(raw, anchor))
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)
    return norm_by_symbol
```

Replace the body of `fetch_symbol_trends` after the `client is None` block (current lines 306-334) with a single-geo delegation (behavior unchanged for this task):

```python
    entities = entities or {}
    symbols = sorted({s for syms in symbol_map.values() for s in syms})
    norm_by_symbol = _fetch_geo(
        client, symbols, anchor, "", timeframe, window, batch_size,
        sleep_s, max_retries, entities,
    )
    return _aggregate(norm_by_symbol, symbol_map, window=window)
```

- [ ] **Step 4: Run the new test + existing fetch/entity suites (unchanged behavior)**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k fetch_geo tests/test_trends_symbols_fetch.py tests/test_trends_symbols_entities.py -v`
Expected: PASS — `_fetch_geo` test green, and the existing `test_trends_symbols_fetch.py` / `test_trends_symbols_entities.py` still pass because `fetch_symbol_trends` still uses `geo=""` and `anchor="SPY"` here.

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_region.py
git commit -m "refactor: extract per-geo trends fetch loop" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Make `fetch_symbol_trends` region-aware

**Files:**
- Modify: `src/data/trends_symbols.py` — module constants + `fetch_symbol_trends`
- Test: `tests/test_trends_symbols_region.py` (append region-aware integration test)
- Modify: `tests/test_trends_symbols_entities.py` (update anchor/geo expectations)

**Interfaces:**
- Produces: `fetch_symbol_trends(symbol_map, anchor=DEFAULT_ANCHOR, client=None, timeframe="today 3-m", window=13, batch_size=4, sleep_s=20.0, max_retries=3, entities=None, region_geos=None)` — `region_geos` defaults to `DEFAULT_REGION_GEOS`; partitions by region and fetches each region in its geo(s), averaging multi-geo regions. Still returns `{REGION|Sector: pd.Series}`.
- Module constants: `DEFAULT_ANCHOR = "YouTube"`, `DEFAULT_REGION_GEOS = {"US": ["US"], "EU": ["DE", "FR", "GB"]}`.

- [ ] **Step 1: Add module constants**

Near the top of `src/data/trends_symbols.py` (after `logger = logging.getLogger(__name__)`):

```python
DEFAULT_ANCHOR = "YouTube"
DEFAULT_REGION_GEOS = {"US": ["US"], "EU": ["DE", "FR", "GB"]}
```

- [ ] **Step 2: Write the failing region-aware integration test**

Append to `tests/test_trends_symbols_region.py`:

```python
from src.data.trends_symbols import fetch_symbol_trends, DEFAULT_ANCHOR


class _GeoClient:
    """Returns a per-geo frame so multi-geo averaging is observable.

    Anchor 'YouTube' flat at 10. Each non-anchor term is flat at a geo-specific
    level: US=10, DE=10, FR=20, GB=30  -> normalized (÷anchor×100): US=100,
    DE=100, FR=200, GB=300.
    """
    LEVEL = {"US": 10.0, "DE": 10.0, "FR": 20.0, "GB": 30.0, "": 10.0}

    def __init__(self):
        self.calls = []
        self._geo = ""
        self._terms = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append((list(kw_list), geo))
        self._geo = geo
        self._terms = list(kw_list)

    def interest_over_time(self):
        lvl = self.LEVEL[self._geo]
        data = {t: ([10.0] * 3 if t == DEFAULT_ANCHOR else [lvl] * 3) for t in self._terms}
        return pd.DataFrame(data)


def test_fetch_is_region_aware_us_geo_and_eu_average():
    smap = {"US|Technology": ["XLK"], "EU|Technology": ["EXV3.DE"]}
    client = _GeoClient()
    out = fetch_symbol_trends(smap, client=client, window=3, batch_size=4, sleep_s=0.0)

    geos_used = {geo for _, geo in client.calls}
    assert geos_used == {"US", "DE", "FR", "GB"}          # US in US; EU in DE/FR/GB
    assert all(terms[0] == DEFAULT_ANCHOR for terms, _ in client.calls)  # YouTube anchor

    assert list(out["US|Technology"]) == [100.0, 100.0, 100.0]   # US level 10 / anchor 10
    # EU: DE=100, FR=200, GB=300 -> average 200
    assert list(out["EU|Technology"]) == [200.0, 200.0, 200.0]


def test_fetch_region_geos_override():
    smap = {"US|Technology": ["XLK"]}
    client = _GeoClient()
    fetch_symbol_trends(smap, client=client, window=3, batch_size=4, sleep_s=0.0,
                        region_geos={"US": ["DE"]})
    assert {geo for _, geo in client.calls} == {"DE"}    # override respected
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k region_aware -v`
Expected: FAIL — geos used are `{""}` (still single worldwide call) and/or anchor is `SPY`, not the region-aware behavior.

- [ ] **Step 4: Rewrite `fetch_symbol_trends`**

Change the signature default `anchor: str = "SPY"` → `anchor: str = DEFAULT_ANCHOR`, add `region_geos: dict[str, list[str]] | None = None` as the final param, and replace the post-`client is None` body with:

```python
    entities = entities or {}
    region_geos = region_geos if region_geos is not None else DEFAULT_REGION_GEOS
    by_region = _symbols_by_region(symbol_map)

    norm_by_symbol: dict[str, list[float]] = {}
    for region, symbols in by_region.items():
        geos = region_geos.get(region, [""])
        per_geo = [
            _fetch_geo(client, symbols, anchor, geo, timeframe, window,
                       batch_size, sleep_s, max_retries, entities)
            for geo in geos
        ]
        if len(per_geo) == 1:
            norm_by_symbol.update(per_geo[0])
        else:
            norm_by_symbol.update(_average_geo_series(per_geo, window))

    return _aggregate(norm_by_symbol, symbol_map, window=window)
```

- [ ] **Step 5: Update the entity-mid fake-client tests for the new anchor/geo**

In `tests/test_trends_symbols_entities.py`:

(a) Make `_FakeClient` record the geo (line 42-45):

```python
    def __init__(self, frame):
        self._frame = frame
        self.calls = []   # list of (kw_list, geo)

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append((list(kw_list), geo))
```

(b) `test_fetch_substitutes_mid_and_rekeys_to_ticker` — keep `anchor="SPY"` explicit so the frame's `SPY` column stays valid, and assert the geo. Change the call and assertion:

```python
    out = fetch_symbol_trends(
        smap, anchor="SPY", client=fake, window=3, batch_size=4, sleep_s=0.0,
        entities={"XLK": "/m/abc"},
    )
    # the client was queried with the mid, not "XLK", in the US geo
    assert fake.calls == [(["SPY", "/m/abc"], "US")]
```

(c) `test_fetch_without_entities_uses_raw_ticker_terms` — same treatment:

```python
    out = fetch_symbol_trends(
        smap, anchor="SPY", client=fake, window=2, batch_size=4, sleep_s=0.0,
    )
    assert fake.calls == [(["SPY", "XLF"], "US")]
```

(The `out[...]` assertions in both tests are unchanged — normalization math is identical.)

- [ ] **Step 6: Run the region + entity + fetch suites**

Run: `python3 -m pytest tests/test_trends_symbols_region.py tests/test_trends_symbols_entities.py tests/test_trends_symbols_fetch.py -v`
Expected: PASS — region-aware tests green; entity tests green with updated `(kw_list, geo)` assertions; `test_trends_symbols_fetch.py` still green (it passes `anchor="SPY"` explicitly and its fake ignores geo, and US/EU keys both resolve through the region loop).

- [ ] **Step 7: Full suite**

Run: `python3 -m pytest -q`
Expected: branch baseline + new region tests, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_region.py tests/test_trends_symbols_entities.py
git commit -m "feat: region-aware Trends pulls (US geo, EU DE/FR/GB average)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Config file + loader

**Files:**
- Create: `config/trends_geo.yaml`
- Modify: `src/data/trends_symbols.py` — add `load_geo_config`
- Test: `tests/test_trends_symbols_region.py` (append)

**Interfaces:**
- Produces: `load_geo_config(path: str = "config/trends_geo.yaml") -> tuple[str, dict[str, list[str]]]` — returns `(anchor, region_geos)`. Missing file or missing keys fall back to `DEFAULT_ANCHOR` / `DEFAULT_REGION_GEOS`.

- [ ] **Step 1: Write the failing tests**

```python
from src.data.trends_symbols import load_geo_config, DEFAULT_ANCHOR, DEFAULT_REGION_GEOS


def test_load_geo_config_reads_file(tmp_path):
    p = tmp_path / "geo.yaml"
    p.write_text("anchor: Google\nregion_geos:\n  US: [US]\n  EU: [DE, FR]\n")
    anchor, region_geos = load_geo_config(str(p))
    assert anchor == "Google"
    assert region_geos == {"US": ["US"], "EU": ["DE", "FR"]}


def test_load_geo_config_missing_file_uses_defaults():
    anchor, region_geos = load_geo_config("config/does_not_exist_geo.yaml")
    assert anchor == DEFAULT_ANCHOR
    assert region_geos == DEFAULT_REGION_GEOS


def test_load_geo_config_partial_falls_back(tmp_path):
    p = tmp_path / "geo.yaml"
    p.write_text("anchor: Bing\n")   # no region_geos
    anchor, region_geos = load_geo_config(str(p))
    assert anchor == "Bing"
    assert region_geos == DEFAULT_REGION_GEOS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k geo_config -v`
Expected: FAIL with `ImportError: cannot import name 'load_geo_config'`

- [ ] **Step 3: Implement the loader**

```python
def load_geo_config(path: str = "config/trends_geo.yaml") -> tuple[str, dict[str, list[str]]]:
    """Load (anchor, region_geos) from the geo config.

    Missing file or missing keys fall back to DEFAULT_ANCHOR / DEFAULT_REGION_GEOS.
    """
    try:
        with open(path, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return DEFAULT_ANCHOR, DEFAULT_REGION_GEOS
    anchor = cfg.get("anchor") or DEFAULT_ANCHOR
    region_geos = cfg.get("region_geos") or DEFAULT_REGION_GEOS
    return anchor, region_geos
```

(`yaml` is already imported at the top of the module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k geo_config -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Create the config file**

Create `config/trends_geo.yaml`:

```yaml
# Region-aware Google Trends configuration.
# anchor: a stable, ubiquitous term (same spelling in every geo) used to stitch
#   Trends' per-payload 0–100 scaling across batches. NOT a finance term on
#   purpose — it should be a flat, high-volume baseline.
anchor: YouTube
# region_geos: which Google Trends geographies each region is queried in.
#   Multiple geos are averaged per symbol (Google Trends has no single "Europe").
region_geos:
  US: [US]
  EU: [DE, FR, GB]
```

- [ ] **Step 6: Commit**

```bash
git add config/trends_geo.yaml src/data/trends_symbols.py tests/test_trends_symbols_region.py
git commit -m "feat: trends_geo config with anchor and region->geo map" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Wire the config into `scan.py`

**Files:**
- Modify: `scan.py` — the sentiment section (the trends import block and the `fetch_symbol_trends` call)

**Interfaces:**
- Consumes: `load_geo_config`, `fetch_symbol_trends(anchor=…, region_geos=…)`.

- [ ] **Step 1: Extend the trends import**

Read `scan.py` and find the `from src.data.trends_symbols import (...)` block in the sentiment section. Add `load_geo_config`:

```python
    from src.data.trends_symbols import (
        build_symbol_map, fetch_symbol_trends, score_symbol_sentiment,
        load_entities, derived_signals, load_geo_config,
    )
```

- [ ] **Step 2: Load the geo config and pass it to the fetch**

Find the line `_trends_by_key = fetch_symbol_trends(_symbol_map, entities=_entities)` and replace with:

```python
    _anchor, _region_geos = load_geo_config("config/trends_geo.yaml")
    logger.info("Trends geos: %s (anchor=%s)",
                ", ".join(f"{r}→{'/'.join(g)}" for r, g in _region_geos.items()), _anchor)
    _trends_by_key = fetch_symbol_trends(
        _symbol_map, anchor=_anchor, entities=_entities, region_geos=_region_geos,
    )
```

- [ ] **Step 3: Verify scan.py parses**

Run: `python3 -c "import ast; ast.parse(open('scan.py').read()); print('scan.py parses')"`
Expected: `scan.py parses`

- [ ] **Step 4: Run the scan smoke suite**

Run: `python3 -m pytest tests/test_scan_smoke.py -v`
Expected: PASS (install `psycopg2-binary` first if the module is missing locally, then re-run).

- [ ] **Step 5: Commit**

```bash
git add scan.py
git commit -m "feat: wire region-aware trends geo config into the scan" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Update the sentiment-page explainer (EN + SV)

**Files:**
- Modify: `dashboard/templates/sentiment.html.j2` (the `<h4>Method</h4>` paragraph)
- Modify: `dashboard/templates/_i18n.html.j2` (the SV `guide_body_sentiment` `<h4>Metod</h4>` paragraph)

**Interfaces:** none (copy change).

- [ ] **Step 1: Read both files** and locate the current Method / Metod paragraphs. Preserve existing wording; **append** the sentences below before each paragraph's closing `</p>`.

- [ ] **Step 2: Update the English Method paragraph**

Append to the `<h4>Method</h4>` `<p>` in `sentiment.html.j2`:

```html
         Interest is pulled per region — <strong>US</strong> sectors in US search,
         <strong>EU</strong> sectors averaged across Germany, France and the UK —
         and normalized against a neutral, ubiquitous anchor term rather than a
         market ticker. (This anchor change means sentiment values from before it
         are not directly comparable to later ones.)
```

- [ ] **Step 3: Update the Swedish Metod paragraph**

Append to the `<h4>Metod</h4>` `<p>` in the `guide_body_sentiment` block of `_i18n.html.j2`:

```html
 Intresset hämtas per region — <strong>US</strong>-sektorer i amerikanska sökningar, <strong>EU</strong>-sektorer som ett genomsnitt av Tyskland, Frankrike och Storbritannien — och normaliseras mot en neutral, allmänt spridd ankarterm i stället för en marknadsticker. (Denna ankarändring gör att sentimentvärden från före ändringen inte är direkt jämförbara med senare.)
```

- [ ] **Step 4: Verify the template renders**

Run:
```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('dashboard/templates'))
html = env.get_template('sentiment.html.j2').render(
    scan_date='x', active_scan_id=1, sentiment_scatter_json='{}',
    sentiment_signal_rows=[], plotly_bundle='x')
assert 'averaged across Germany' in html
print('sentiment.html.j2 renders with region note')
"
```
Expected: `sentiment.html.j2 renders with region note`

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/sentiment.html.j2 dashboard/templates/_i18n.html.j2
git commit -m "docs: note region-aware pulls on the sentiment page" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Backlog hygiene

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:** none.

- [ ] **Step 1: Strike the region-aware bullet**

In `BACKLOG.md`, under "Sentiment page — enrichment", find the bullet beginning `- **Region-aware pulls.**` and replace the whole bullet with:

```markdown
- ~~**Region-aware pulls.**~~ *(shipped — see Done)*
```

- [ ] **Step 2: Add the Done entry** at the top of the `## Done` list:

```markdown
- ~~Sentiment — region-aware Trends pulls~~ — `fetch_symbol_trends` now queries US
  sectors in `geo="US"` and EU sectors averaged across `DE`/`FR`/`GB`, normalized
  against a stable ubiquitous anchor (`YouTube`, configurable in
  `config/trends_geo.yaml`) instead of the worldwide `SPY` pull. Symbols are
  partitioned by region (`_symbols_by_region`), fetched per geo (`_fetch_geo`), and
  multi-geo regions averaged per symbol (`_average_geo_series`); `_aggregate`/scoring
  and the entity-mid path are unchanged (ticker-keyed). Toggle-only. Costs ~4× the
  Trends API calls (day-cache remains a separate backlog item), and the anchor change
  breaks comparability with pre-change stored sentiment. *(2026-07-07)*
```

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark region-aware trends pulls done in backlog" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Full suite green:** `python3 -m pytest -q` → branch baseline + new region tests (~11 new), 6 skipped (or DB modules run with `psycopg2-binary`). No regressions.
- [ ] **No `docs/` staged:** `git status --porcelain docs/` → empty.
- [ ] **Diff source-only:** `git diff --stat main...HEAD` touches only `src/`, `config/`, `scan.py`, `dashboard/templates/`, `tests/`, `BACKLOG.md`, `design/`.
- [ ] Final whole-branch review, address findings, then `git push -u origin feature/trends-region-aware` and open a PR with `gh pr create` (per CLAUDE.md — Claude opens the PR; Jonas merges). **Do not merge.**
