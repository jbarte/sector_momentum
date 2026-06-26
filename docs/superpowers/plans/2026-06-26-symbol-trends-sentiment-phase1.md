# Symbol-based Trends Sentiment (Phase 1 — ETF symbols) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single generic theme word per sector with **search interest in the sector's ETF symbols** (primary + linked ETFs, both regions), aggregated into a region-aware sentiment score that drops into the existing `score_all(sentiment_score=…)` path.

**Architecture:** A new `src/data/trends_symbols.py` builds a `{region|sector: [symbols]}` map from existing configs, fetches anchor-normalized Google Trends series for those symbols (cross-batch-comparable via a fixed anchor term), aggregates to one 13-week series per `region|sector`, and converts to a sentiment z-score per sector key. `scan.py` swaps the old keyword fetch for this. Scorer composite behavior is unchanged (sentiment stays toggle-only).

**Tech Stack:** Python 3, pandas, numpy, PyYAML, pytrends (existing), pytest.

## Global Constraints

- **Source = instrument symbols, not theme words.** Per `region|sector`: the primary sector ETF (`config/universe.yaml`) + linked ETFs (`config/sector_etfs.yaml`). **Phase 1 excludes** underlying constituents (Phase 2) and the broad-market benchmark (RSP/SPY/EXSA.DE).
- **Region-aware:** `US|Technology` and `EU|Technology` are scored separately (different symbols). Output is a `pd.Series` indexed by `"<REGION>|<sector>"`.
- **Cross-batch normalization:** Trends scales 0–100 within each ≤5-term payload, so every batch includes a fixed **anchor term (`SPY`)** and each symbol series is divided by the anchor to share one scale.
- **Toggle-only:** `score_all(..., sentiment_score=…, blend_sentiment=False)` — never change the canonical composite.
- **Reuse the scorer's z-step:** import `_cross_zscore` from `src/signals/sentiment.py`; do NOT reuse `_search_momentum` (it keys by region-collapsed sector name, incompatible with region-distinct symbols).
- **Resilience mirrors `src/data/trends.py`:** timeframe `"today 3-m"`, `geo=""`, batch retries with backoff, partial-success → neutral `0.0`, one cache/day.
- **Dead/ambiguous terms:** drop symbols whose series is all-zero (no search volume); skip symbols in a blocklist of common-word tickers.
- **Commit style:** conventional commits, subject < 72 chars; end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **`docs/` is CI-owned** — do not commit `docs/` from this branch (no dashboard changes in Phase 1 anyway).

## File structure

- `config/trends_blocklist.yaml` (new) — list of ambiguous common-word tickers to skip.
- `src/data/trends_symbols.py` (new) — symbol map, pure transforms, fetch orchestration, scorer.
- `scan.py` (modify) — swap keyword fetch → symbol fetch + sentiment.
- Tests under `tests/`.

---

### Task 1: Symbol map + blocklist

**Files:**
- Create: `config/trends_blocklist.yaml`
- Create: `src/data/trends_symbols.py`
- Test: `tests/test_trends_symbols_map.py`

**Interfaces:**
- Produces: `build_symbol_map(universe: dict, sector_etfs: dict, blocklist: set[str] | None = None) -> dict[str, list[str]]` — returns `{"US|Technology": ["XLK","VGT"], …, "EU|Technology": ["EXV3.DE"], …}`. Dedups preserving order; drops blocklisted symbols (case-insensitive).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trends_symbols_map.py
from src.data.trends_symbols import build_symbol_map


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Financials": "XLF"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def _sector_etfs():
    return {
        "US": {
            "Technology": [{"ticker": "XLK"}, {"ticker": "VGT"}],
            "Financials": [{"ticker": "XLF"}, {"ticker": "ALL"}],  # ALL is blocklisted
        },
        "EU": {"Technology": [{"ticker": "EXV3.DE"}]},
    }


def test_build_symbol_map_combines_and_dedups():
    m = build_symbol_map(_universe(), _sector_etfs(), blocklist={"ALL"})
    assert m["US|Technology"] == ["XLK", "VGT"]        # primary + alternate, deduped
    assert m["US|Financials"] == ["XLF"]               # ALL dropped by blocklist
    assert m["EU|Technology"] == ["EXV3.DE"]
    # benchmark tickers never appear
    assert all("RSP" not in v and "EXSA.DE" not in v and "SPY" not in v for v in m.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trends_symbols_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.trends_symbols'`.

- [ ] **Step 3: Create the blocklist config**

```yaml
# config/trends_blocklist.yaml
# Tickers that collide with common English words — skipped as Google Trends
# query terms because their search volume is dominated by non-financial use.
- ALL
- KEY
- IT
- ON
- A
- SO
- DD
- "NOW"
- "CAT"
- "GAP"
```

- [ ] **Step 4: Implement `build_symbol_map`**

```python
# src/data/trends_symbols.py
"""Symbol-based Google Trends sentiment.

Builds {region|sector: [instrument symbols]} from the existing universe + sector
ETF configs, fetches anchor-normalized search interest, aggregates to one series
per region|sector, and scores it as a cross-sectional z. Region-aware; toggle-only.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_symbol_map(
    universe: dict,
    sector_etfs: dict,
    blocklist: set[str] | None = None,
) -> dict[str, list[str]]:
    block = {b.upper() for b in (blocklist or set())}
    out: dict[str, list[str]] = {}
    for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for sector, primary in universe.get(key, {}).items():
            symbols: list[str] = []
            candidates = [primary] + [
                e.get("ticker")
                for e in sector_etfs.get(region, {}).get(sector, [])
                if e.get("ticker")
            ]
            for sym in candidates:
                if not sym or sym.upper() in block or sym in symbols:
                    continue
                symbols.append(sym)
            if symbols:
                out[f"{region}|{sector}"] = symbols
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_trends_symbols_map.py -v`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add config/trends_blocklist.yaml src/data/trends_symbols.py tests/test_trends_symbols_map.py
git commit -m "feat: build region|sector symbol map for Trends sentiment

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pure transforms (slope, anchor-normalize, aggregate)

**Files:**
- Modify: `src/data/trends_symbols.py`
- Test: `tests/test_trends_symbols_transforms.py`

**Interfaces:**
- Produces:
  - `_slope(series: list[float]) -> float` — OLS slope of the series vs index; `0.0` if < 3 points or all-equal/all-zero.
  - `_normalize_by_anchor(raw: dict[str, list[float]], anchor: str) -> dict[str, list[float]]` — divides each non-anchor term's series pointwise by the anchor series (×100); anchor points of 0 → contribute 0; the anchor key is dropped from the output. If the anchor series is all-zero, returns the non-anchor series unchanged.
  - `_aggregate(norm_by_symbol: dict[str, list[float]], symbol_map: dict[str, list[str]], window: int = 13) -> dict[str, pd.Series]` — per `sector_key`, mean of its **live** symbols (a symbol is live if its series has any non-zero value); no live symbols → a zero series of length `window`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trends_symbols_transforms.py
import numpy as np
import pandas as pd
from src.data.trends_symbols import _slope, _normalize_by_anchor, _aggregate


def test_slope_sign():
    assert _slope([1, 2, 3, 4]) > 0
    assert _slope([4, 3, 2, 1]) < 0
    assert _slope([0, 0, 0, 0]) == 0.0
    assert _slope([5]) == 0.0


def test_normalize_by_anchor_divides_and_drops_anchor():
    raw = {"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert "SPY" not in out
    assert out["XLK"] == [50.0, 100.0, 200.0]   # (x/anchor)*100


def test_normalize_anchor_all_zero_passthrough():
    raw = {"SPY": [0.0, 0.0], "XLK": [3.0, 4.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert out["XLK"] == [3.0, 4.0]


def test_aggregate_means_live_symbols_and_zeros_dead():
    norm = {"XLK": [2.0, 4.0], "VGT": [4.0, 8.0], "DEAD": [0.0, 0.0]}
    smap = {"US|Technology": ["XLK", "VGT", "DEAD"], "US|Energy": ["DEAD"]}
    agg = _aggregate(norm, smap, window=2)
    assert list(agg["US|Technology"]) == [3.0, 6.0]   # mean of XLK,VGT; DEAD excluded
    assert list(agg["US|Energy"]) == [0.0, 0.0]       # no live symbols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trends_symbols_transforms.py -v`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement the transforms (append to `src/data/trends_symbols.py`)**

```python
def _slope(series: list[float]) -> float:
    vals = [float(v) for v in series]
    if len(vals) < 3 or len(set(vals)) <= 1:
        return 0.0
    x = np.arange(len(vals))
    slope, _ = np.polyfit(x, np.array(vals, dtype=float), 1)
    return float(slope)


def _normalize_by_anchor(raw: dict[str, list[float]], anchor: str) -> dict[str, list[float]]:
    anchor_series = raw.get(anchor)
    out: dict[str, list[float]] = {}
    anchor_dead = not anchor_series or all(a == 0 for a in anchor_series)
    for term, series in raw.items():
        if term == anchor:
            continue
        if anchor_dead:
            out[term] = [float(v) for v in series]
            continue
        norm = []
        for v, a in zip(series, anchor_series):
            norm.append(float(v) / a * 100.0 if a else 0.0)
        out[term] = norm
    return out


def _aggregate(
    norm_by_symbol: dict[str, list[float]],
    symbol_map: dict[str, list[str]],
    window: int = 13,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for sector_key, symbols in symbol_map.items():
        live = [
            norm_by_symbol[s]
            for s in symbols
            if s in norm_by_symbol and any(v != 0 for v in norm_by_symbol[s])
        ]
        if not live:
            out[sector_key] = pd.Series([0.0] * window, dtype=float)
        else:
            arr = np.array(live, dtype=float)
            out[sector_key] = pd.Series(arr.mean(axis=0), dtype=float)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trends_symbols_transforms.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_transforms.py
git commit -m "feat: anchor-normalize + aggregate transforms for symbol Trends

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Score symbol sentiment (slope → cross-sectional z)

**Files:**
- Modify: `src/data/trends_symbols.py`
- Test: `tests/test_trends_symbols_score.py`

**Interfaces:**
- Consumes: `_slope` (Task 2); `_cross_zscore` from `src/signals/sentiment.py`.
- Produces: `score_symbol_sentiment(trends_by_key: dict[str, pd.Series]) -> pd.Series` — slope of each sector key's series, cross-sectionally z-scored across all keys; returns a `pd.Series` indexed by `region|sector` (the shape `score_all(sentiment_score=…)` expects).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trends_symbols_score.py
import pandas as pd
from src.data.trends_symbols import score_symbol_sentiment


def test_rising_key_scores_above_falling():
    trends = {
        "US|Technology": pd.Series([1.0, 2.0, 3.0, 4.0]),   # rising
        "US|Energy": pd.Series([4.0, 3.0, 2.0, 1.0]),       # falling
        "US|Utilities": pd.Series([2.0, 2.0, 2.0, 2.0]),    # flat
    }
    s = score_symbol_sentiment(trends)
    assert set(s.index) == set(trends)
    assert s["US|Technology"] > s["US|Utilities"] > s["US|Energy"]
    # cross-sectional z is centered near zero
    assert abs(s.mean()) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trends_symbols_score.py -v`
Expected: FAIL — name not defined.

- [ ] **Step 3: Implement `score_symbol_sentiment` (append to `src/data/trends_symbols.py`)**

```python
def score_symbol_sentiment(trends_by_key: dict[str, pd.Series]) -> pd.Series:
    from src.signals.sentiment import _cross_zscore

    slopes = {key: _slope(list(series)) for key, series in trends_by_key.items()}
    z = _cross_zscore(slopes)
    return pd.Series(z, dtype=float)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trends_symbols_score.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_score.py
git commit -m "feat: score symbol Trends sentiment (slope + cross-sectional z)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Fetch orchestration (anchor-batched, injectable client)

**Files:**
- Modify: `src/data/trends_symbols.py`
- Test: `tests/test_trends_symbols_fetch.py`

**Interfaces:**
- Consumes: `_normalize_by_anchor`, `_aggregate` (Task 2).
- Produces: `fetch_symbol_trends(symbol_map: dict[str, list[str]], anchor: str = "SPY", client=None, timeframe: str = "today 3-m", window: int = 13, batch_size: int = 4, sleep_s: float = 20.0, max_retries: int = 3) -> dict[str, pd.Series]` — gathers unique symbols, fetches in batches of `batch_size` (+ the anchor as the 5th term), anchor-normalizes, aggregates per sector key. `client` is any object with `build_payload(kw_list, timeframe, geo)` and `interest_over_time() -> pd.DataFrame`; `None` → a real `pytrends.request.TrendReq`. A batch that fails all retries leaves its symbols absent (→ those sectors fall back to neutral via `_aggregate`).

- [ ] **Step 1: Write the failing test (fake client, no network)**

```python
# tests/test_trends_symbols_fetch.py
import pandas as pd
from src.data.trends_symbols import fetch_symbol_trends


class FakeClient:
    """Returns a deterministic interest frame for whatever terms were last built."""
    def __init__(self):
        self._terms = []

    def build_payload(self, kw_list, timeframe=None, geo=None):
        self._terms = list(kw_list)

    def interest_over_time(self):
        # anchor flat at 10; each ticker ramps so normalized series is rising
        data = {}
        for i, t in enumerate(self._terms):
            data[t] = [10.0] * 13 if t == "SPY" else [float(i + 1)] * 13
        return pd.DataFrame(data)


def test_fetch_aggregates_per_sector_key_via_fake_client():
    smap = {"US|Technology": ["XLK", "VGT"], "EU|Technology": ["EXV3.DE"]}
    out = fetch_symbol_trends(smap, anchor="SPY", client=FakeClient(), sleep_s=0.0)
    assert set(out) == set(smap)
    assert len(out["US|Technology"]) == 13
    # all series are non-negative numbers
    assert (out["US|Technology"] >= 0).all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trends_symbols_fetch.py -v`
Expected: FAIL — name not defined.

- [ ] **Step 3: Implement `fetch_symbol_trends` (append to `src/data/trends_symbols.py`)**

```python
import random
import time


def _new_client(timeout=(10, 25)):
    from pytrends.request import TrendReq
    return TrendReq(hl="en-US", tz=0, timeout=timeout)


def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = "SPY",
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
) -> dict[str, pd.Series]:
    if client is None:
        try:
            client = _new_client()
        except Exception as exc:
            logger.warning("Trends client init failed (%s) — sentiment neutral", exc)
            return _aggregate({}, symbol_map, window=window)

    symbols = sorted({s for syms in symbol_map.values() for s in syms})
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    norm_by_symbol: dict[str, list[float]] = {}

    for bi, batch in enumerate(batches):
        terms = [anchor] + batch
        df = None
        for attempt in range(max_retries):
            try:
                client.build_payload(terms, timeframe=timeframe, geo="")
                df = client.interest_over_time()
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                else:
                    logger.warning("Trends batch %d failed (%s) — %d symbols neutral",
                                   bi + 1, exc, len(batch))
        if df is not None and not df.empty:
            raw = {t: [float(v) for v in df[t].tolist()[-window:]]
                   for t in terms if t in df.columns}
            norm_by_symbol.update(_normalize_by_anchor(raw, anchor))
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)

    return _aggregate(norm_by_symbol, symbol_map, window=window)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trends_symbols_fetch.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_fetch.py
git commit -m "feat: anchor-batched symbol Trends fetch (injectable client)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Wire into the scan

**Files:**
- Modify: `scan.py` (the sentiment block, ~lines 442–460, and the `_compute_sentiment_for_scan` usage)
- Test: `tests/test_scan_smoke.py` (extend) or rely on existing smoke

**Interfaces:**
- Consumes: `build_symbol_map`, `fetch_symbol_trends`, `score_symbol_sentiment` (Tasks 1–4).
- Produces: the live scan computes `sentiment_score` from instrument symbols instead of theme keywords; still passed to `score_all(..., blend_sentiment=False)`.

- [ ] **Step 1: Replace the keyword sentiment block in `scan.py`**

Find (≈ scan.py:442–451):
```python
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
```
Replace with:
```python
    logger.info("Fetching symbol-based Google Trends sentiment …")
    from src.data.trends_symbols import (
        build_symbol_map, fetch_symbol_trends, score_symbol_sentiment,
    )
    with open("config/sector_etfs.yaml", "r") as _fh:
        _sector_etfs = yaml.safe_load(_fh) or {}
    try:
        with open("config/trends_blocklist.yaml", "r") as _fh:
            _blocklist = set(yaml.safe_load(_fh) or [])
    except FileNotFoundError:
        _blocklist = set()
    _symbol_map = build_symbol_map(universe, _sector_etfs, blocklist=_blocklist)
    _trends_by_key = fetch_symbol_trends(_symbol_map)
    sentiment_score = score_symbol_sentiment(_trends_by_key)
    sentiment_score = sentiment_score.reindex(wide_df.index, fill_value=0.0)
    _live = int((sentiment_score != 0).sum())
    logger.info("Symbol sentiment: %d/%d sector-keys non-neutral", _live, len(wide_df.index))
```
Leave the existing `score_all(wide_df, weights_path=…, sentiment_score=sentiment_score, blend_sentiment=False)` call unchanged. The old `_compute_sentiment_for_scan` helper and the `from src.data.trends import fetch_trends` import may now be unused — remove them if so (keep `src/data/trends.py` and `src/signals/sentiment.py` for `_cross_zscore`).

- [ ] **Step 2: Run the existing scan smoke tests**

Run: `pytest tests/test_scan_smoke.py -v`
Expected: PASS — confirms the import + wiring don't break the pipeline (sentiment is mocked/neutral in smoke).

- [ ] **Step 3: Optional live check (network; minutes — skip if offline)**

Run: `.venv/bin/python scan.py --dry-run`
Expected: logs `Symbol sentiment: N/22 sector-keys non-neutral`. **Acceptance gate:** US sector-keys should be mostly non-neutral; EU may be largely neutral (obscure `.DE` tickers) — that's the documented Phase-1 limitation, not a bug. If **all** keys are neutral, investigate (anchor failing / rate-limited).

- [ ] **Step 4: Commit**

```bash
git add scan.py tests/test_scan_smoke.py
git commit -m "feat: scan uses symbol-based Trends sentiment

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Backlog hygiene + full suite

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Full suite**

Run: `pytest -q`
Expected: green (existing + 4 new test files).

- [ ] **Step 2: Move the backlog item to Done**

In `BACKLOG.md`, under `## Symbol-based Google Trends sentiment …`, replace the queued section with a Done entry at the top of `## Done` (dated 2026-06-26):
`- ~~Symbol-based Google Trends sentiment (Phase 1 — ETF symbols)~~ — Trends now queries the sector ETF symbols (primary + linked, both regions), anchor-normalized (SPY) and aggregated to a region-aware sentiment z per region|sector via `src/data/trends_symbols.py`; replaces the generic-keyword source. Toggle-only. Phase 2 (US constituents) pending. *(2026-06-26)*`

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: backlog — symbol-based Trends sentiment phase 1 shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Symbol universe from existing configs (primary + linked ETFs, both regions; benchmark excluded) → Task 1. ✓
- Anchor normalization across batches → Tasks 2 (`_normalize_by_anchor`) + 4 (fetch). ✓
- Aggregate per sector + drop dead symbols → Task 2 (`_aggregate`). ✓
- Region-aware sentiment z feeding `score_all` unchanged → Task 3 + Task 5. ✓
- Ambiguous-ticker blocklist → Task 1. ✓
- Resilience (retry/backoff/partial-neutral) → Task 4. ✓
- Acceptance gate (validate not-all-zero; EU may be sparse) → Task 5 Step 3 + the non-neutral log. ✓
- Out of scope (constituents, Topics, regional geo, dashboard tab) → not in this plan, per spec phasing. ✓

**Deviation from spec (intentional):** spec said reuse `_search_momentum`; that helper is region-collapsed, so the plan reuses `_cross_zscore` and computes slope per region|sector instead (Task 3). Documented in Global Constraints.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `build_symbol_map → {sector_key: [str]}` consumed by `fetch_symbol_trends` (Task 4) and `_aggregate` (Task 2); `fetch_symbol_trends → {sector_key: pd.Series}` consumed by `score_symbol_sentiment` (Task 3) → `pd.Series[sector_key]` consumed by `score_all` (Task 5). Anchor `"SPY"` consistent across Tasks 2/4. ✓

## Caching note (deferred, not blocking)

Phase 1 fetch does not add a day-cache (unlike `trends.py`). If live-run rate-limiting bites, add a `trends_symbols_<date>.json` cache mirroring `trends.py` — left out to keep Phase 1 lean; flagged here so it isn't forgotten.
