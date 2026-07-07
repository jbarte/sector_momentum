# Durable Trends day-cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache successfully-fetched Trends batches per UTC day in Supabase Storage so a re-triggered CI run (or same-day re-run) reuses them and re-fetches only the batches that failed.

**Architecture:** A new `src/data/trends_cache.py` holds pure key helpers plus fail-open Storage load/save (reusing `src/storage_backup.py`). `_fetch_geo` gains an optional in-memory `cache` dict: batch hit → skip the API call; miss → fetch and record. `scan.py` loads the day's cache before the fetch and saves it after; both guarded so any Storage failure degrades to a live, uncached scan.

**Tech Stack:** Python 3.13, pytrends, pandas, PyYAML, requests (via storage_backup), pytest.

## Global Constraints

- Sentiment stays **toggle-only**; no scoring/composite change.
- **Fail-open:** any Storage or cache error logs a warning and the scan proceeds with a live fetch. The cache must never fail the scan.
- **Additive:** `cache=None` (the default on `_fetch_geo` / `fetch_symbol_trends`) reproduces today's behavior exactly. Existing fetch/region/entity tests must stay green unedited.
- Cache granularity is **per (geo, batch)**; only *successful* batches are written. Batch-key = the batch's tickers sorted and joined with `|`.
- Bucket: `trends-cache` (constant `DEFAULT_CACHE_BUCKET`); one object per UTC day, `trends_cache_<YYYY-MM-DD>.json`. Reuse `storage_backup.upload`/`download`.
- `trends_symbols.py` performs **no Storage I/O** — it only reads/writes the in-memory `cache` dict `scan.py` passes in.
- Use `python3` for pytest. Conventional commits, subject < 72 chars, footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do **not** `git add docs/`. Record the branch test baseline with `python3 -m pytest -q` before Task 1 (6 skips are the psycopg2-less DB modules; install `psycopg2-binary` to run them). No pre-existing test may regress.
- Spec: `design/specs/2026-07-07-trends-day-cache-design.md`.

## File Structure

- `src/data/trends_cache.py` — new: `DEFAULT_CACHE_BUCKET`, `cache_object_name`, `batch_key` (pure); `load_cache`, `save_cache` (fail-open Storage wrappers).
- `src/data/trends_symbols.py` — `_fetch_geo` gains `cache=None`; `fetch_symbol_trends` threads `cache` through. Imports `batch_key` from `trends_cache`.
- `scan.py` — `--no-cache` flag; load cache before the fetch, save after.
- `tests/test_trends_cache.py` — new: key helpers + Storage wrappers (monkeypatched).
- `tests/test_trends_symbols_region.py` — append cache hit/miss/None tests for `_fetch_geo`.
- `BACKLOG.md`, `CLAUDE.md` — Done entry + trends-cache bucket note.

---

### Task 1: Pure cache-key helpers

**Files:**
- Create: `src/data/trends_cache.py`
- Test: `tests/test_trends_cache.py` (create)

**Interfaces:**
- Produces: `DEFAULT_CACHE_BUCKET = "trends-cache"`; `cache_object_name(date_str: str) -> str` → `f"trends_cache_{date_str}.json"`; `batch_key(tickers: list[str]) -> str` → sorted tickers joined by `"|"` (order-independent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trends_cache.py
from src.data.trends_cache import cache_object_name, batch_key, DEFAULT_CACHE_BUCKET


def test_cache_object_name():
    assert cache_object_name("2026-07-07") == "trends_cache_2026-07-07.json"


def test_batch_key_is_order_independent():
    assert batch_key(["XLK", "VGT"]) == batch_key(["VGT", "XLK"]) == "VGT|XLK"


def test_default_cache_bucket():
    assert DEFAULT_CACHE_BUCKET == "trends-cache"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.trends_cache'`

- [ ] **Step 3: Implement the helpers**

Create `src/data/trends_cache.py`:

```python
"""Durable per-day cache of Google Trends batches in Supabase Storage.

One JSON object per UTC day, keyed by geo then batch-key, holding each
successful batch's anchor-normalized {ticker: series}. Fail-open: any Storage
error degrades to an empty cache / no-op so the scan proceeds with a live fetch.
"""
from __future__ import annotations

import json
import logging

from src import storage_backup

logger = logging.getLogger(__name__)

DEFAULT_CACHE_BUCKET = "trends-cache"


def cache_object_name(date_str: str) -> str:
    """'2026-07-07' -> 'trends_cache_2026-07-07.json'."""
    return f"trends_cache_{date_str}.json"


def batch_key(tickers: list[str]) -> str:
    """Deterministic, order-independent key for a batch: sorted tickers joined by '|'."""
    return "|".join(sorted(tickers))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_cache.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_cache.py tests/test_trends_cache.py
git commit -m "feat: trends cache key helpers" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Fail-open Storage load/save

**Files:**
- Modify: `src/data/trends_cache.py` (add `load_cache`, `save_cache`)
- Test: `tests/test_trends_cache.py` (append)

**Interfaces:**
- Consumes: `storage_backup.download(object_name, bucket) -> bytes` (raises on missing/HTTP error), `storage_backup.upload(object_name, data: bytes, bucket) -> None`.
- Produces: `load_cache(date_str: str, bucket: str = DEFAULT_CACHE_BUCKET) -> dict` (parsed cache, or `{}` on any error); `save_cache(date_str: str, cache: dict, bucket: str = DEFAULT_CACHE_BUCKET) -> None` (uploads JSON; never raises).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trends_cache.py`:

```python
import json
import pytest
from src.data import trends_cache
from src.data.trends_cache import load_cache, save_cache


def test_load_cache_parses_downloaded_json(monkeypatch):
    monkeypatch.setattr(trends_cache.storage_backup, "download",
                        lambda name, bucket=None: b'{"US": {"XLK": {"XLK": [1.0]}}}')
    assert load_cache("2026-07-07") == {"US": {"XLK": {"XLK": [1.0]}}}


def test_load_cache_fail_open_on_error(monkeypatch):
    def boom(name, bucket=None):
        raise RuntimeError("404 not found")
    monkeypatch.setattr(trends_cache.storage_backup, "download", boom)
    assert load_cache("2026-07-07") == {}          # missing object -> empty, no raise


def test_load_cache_fail_open_on_bad_json(monkeypatch):
    monkeypatch.setattr(trends_cache.storage_backup, "download",
                        lambda name, bucket=None: b"not json{{{")
    assert load_cache("2026-07-07") == {}


def test_save_cache_uploads_json(monkeypatch):
    captured = {}
    def fake_upload(name, data, bucket=None):
        captured["name"] = name
        captured["data"] = data
    monkeypatch.setattr(trends_cache.storage_backup, "upload", fake_upload)
    save_cache("2026-07-07", {"US": {"XLK": {"XLK": [1.0]}}})
    assert captured["name"] == "trends_cache_2026-07-07.json"
    assert json.loads(captured["data"]) == {"US": {"XLK": {"XLK": [1.0]}}}


def test_save_cache_swallows_upload_error(monkeypatch):
    def boom(name, data, bucket=None):
        raise RuntimeError("network down")
    monkeypatch.setattr(trends_cache.storage_backup, "upload", boom)
    save_cache("2026-07-07", {})    # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_cache.py -k "load_cache or save_cache" -v`
Expected: FAIL with `ImportError: cannot import name 'load_cache'`

- [ ] **Step 3: Implement**

Append to `src/data/trends_cache.py`:

```python
def load_cache(date_str: str, bucket: str = DEFAULT_CACHE_BUCKET) -> dict:
    """Download + parse the day's cache object. Any error -> {} (fail-open)."""
    try:
        data = storage_backup.download(cache_object_name(date_str), bucket=bucket)
        return json.loads(data)
    except Exception as exc:
        logger.warning("Trends cache load skipped (%s) — proceeding uncached", exc)
        return {}


def save_cache(date_str: str, cache: dict, bucket: str = DEFAULT_CACHE_BUCKET) -> None:
    """Serialize + upload the day's cache. Any error is logged and swallowed."""
    try:
        payload = json.dumps(cache).encode("utf-8")
        storage_backup.upload(cache_object_name(date_str), payload, bucket=bucket)
    except Exception as exc:
        logger.warning("Trends cache save skipped (%s)", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_cache.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_cache.py tests/test_trends_cache.py
git commit -m "feat: fail-open Supabase Storage load/save for trends cache" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Cache-aware fetch (`_fetch_geo` + `fetch_symbol_trends`)

**Files:**
- Modify: `src/data/trends_symbols.py` (`_fetch_geo`, `fetch_symbol_trends`, and a new import)
- Test: `tests/test_trends_symbols_region.py` (append)

**Interfaces:**
- Consumes: `batch_key` from `src.data.trends_cache`.
- Produces: `_fetch_geo(..., cache: dict | None = None)` — when `cache` is a dict, a batch whose `[geo][batch_key]` entry exists is served from cache (no `build_payload`, no inter-batch sleep) and each freshly-fetched batch's normalized `{ticker: series}` is written to `cache[geo][batch_key]`; when `cache is None`, behavior is unchanged. `fetch_symbol_trends(..., cache: dict | None = None)` threads `cache` into every `_fetch_geo` call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trends_symbols_region.py`:

```python
from src.data.trends_symbols import _fetch_geo as _fg_cache  # alias to avoid earlier-import clash
from src.data.trends_cache import batch_key


class _CountingClient:
    """Counts build_payload calls; returns a fixed frame."""
    def __init__(self, frame):
        self._frame = frame
        self.build_calls = 0

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.build_calls += 1

    def interest_over_time(self):
        return self._frame


def test_fetch_geo_cache_miss_then_writes():
    import pandas as pd
    frame = pd.DataFrame({"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]})
    client = _CountingClient(frame)
    cache = {}
    out = _fg_cache(client, ["XLK"], anchor="SPY", geo="US", timeframe="today 3-m",
                    window=3, batch_size=4, sleep_s=0.0, max_retries=3, entities={},
                    cache=cache)
    assert client.build_calls == 1                       # miss -> fetched
    assert out["XLK"] == [50.0, 100.0, 200.0]
    # the successful batch was written to the cache under [geo][batch_key]
    assert cache["US"][batch_key(["XLK"])]["XLK"] == [50.0, 100.0, 200.0]


def test_fetch_geo_cache_hit_skips_call():
    import pandas as pd
    frame = pd.DataFrame({"SPY": [10.0], "XLK": [5.0]})   # would give different values if used
    client = _CountingClient(frame)
    cache = {"US": {batch_key(["XLK"]): {"XLK": [42.0, 42.0, 42.0]}}}
    out = _fg_cache(client, ["XLK"], anchor="SPY", geo="US", timeframe="today 3-m",
                    window=3, batch_size=4, sleep_s=0.0, max_retries=3, entities={},
                    cache=cache)
    assert client.build_calls == 0                       # hit -> no API call
    assert out["XLK"] == [42.0, 42.0, 42.0]              # served from cache


def test_fetch_geo_cache_none_is_unchanged():
    import pandas as pd
    frame = pd.DataFrame({"SPY": [10.0, 10.0], "XLK": [10.0, 20.0]})
    client = _CountingClient(frame)
    out = _fg_cache(client, ["XLK"], anchor="SPY", geo="US", timeframe="today 3-m",
                    window=2, batch_size=4, sleep_s=0.0, max_retries=3, entities={},
                    cache=None)
    assert client.build_calls == 1
    assert out["XLK"] == [100.0, 200.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_region.py -k "cache" -v`
Expected: FAIL — `_fetch_geo` has no `cache` parameter (TypeError) / `build_calls` assertions don't hold.

- [ ] **Step 3: Implement**

At the top of `src/data/trends_symbols.py`, add the import (with the other `from src.data...` / project imports):

```python
from src.data.trends_cache import batch_key
```

Change the `_fetch_geo` signature to add a final `cache` param:

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
    cache: dict | None = None,
) -> dict[str, list[float]]:
```

Replace the loop body so a cache hit short-circuits and a successful miss is recorded. The full loop becomes:

```python
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    norm_by_symbol: dict[str, list[float]] = {}
    for bi, batch in enumerate(batches):
        if cache is not None:
            key = batch_key(batch)
            cached = cache.get(geo, {}).get(key)
            if cached is not None:
                norm_by_symbol.update(cached)
                continue                      # skip API call and inter-batch sleep
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
            normalized = _normalize_by_anchor(raw, anchor)
            norm_by_symbol.update(normalized)
            if cache is not None:
                cache.setdefault(geo, {})[batch_key(batch)] = normalized
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)
    return norm_by_symbol
```

Add `cache: dict | None = None` as the final param of `fetch_symbol_trends` (after `region_geos`), and pass it into each `_fetch_geo` call:

```python
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, pd.Series]:
```

and in the region loop:

```python
        per_geo = [
            _fetch_geo(client, symbols, anchor, geo, timeframe, window,
                       batch_size, sleep_s, max_retries, entities, cache=cache)
            for geo in geos
        ]
```

- [ ] **Step 4: Run the cache tests + the full trends suites (no regressions)**

Run: `python3 -m pytest tests/test_trends_symbols_region.py tests/test_trends_symbols_fetch.py tests/test_trends_symbols_entities.py tests/test_trends_cache.py -v`
Expected: PASS — new cache tests green; existing region/fetch/entity tests unchanged and green (they pass no `cache`, so the default `None` path is byte-identical to before).

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_region.py
git commit -m "feat: per-batch cache hit/write in the trends fetch loop" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire the cache into `scan.py`

**Files:**
- Modify: `scan.py` — argparse (`--no-cache`) and the sentiment fetch section

**Interfaces:**
- Consumes: `trends_cache.load_cache`, `trends_cache.save_cache`, `fetch_symbol_trends(cache=…)`.

- [ ] **Step 1: Add the `--no-cache` flag**

In `scan.py`, after the `--no-backup` argument block (around line 75), add:

```python
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the durable Trends day-cache (always live-fetch, no save).",
    )
```

- [ ] **Step 2: Load the cache before the fetch and save after**

In the sentiment section, replace the current fetch call:

```python
    _trends_by_key = fetch_symbol_trends(
        _symbol_map, anchor=_anchor, entities=_entities, region_geos=_region_geos,
    )
```

with:

```python
    from src.data import trends_cache
    _use_cache = not args.no_cache
    _cache_date = datetime.utcnow().strftime("%Y-%m-%d")
    _cache = trends_cache.load_cache(_cache_date) if _use_cache else None
    _trends_by_key = fetch_symbol_trends(
        _symbol_map, anchor=_anchor, entities=_entities, region_geos=_region_geos,
        cache=_cache,
    )
    if _use_cache:
        trends_cache.save_cache(_cache_date, _cache)
```

(`datetime` is already imported in `scan.py`. `load_cache` returns `{}` on any Storage failure, so `_cache` is a dict whenever `_use_cache` is true — safe to pass to both `fetch_symbol_trends` and `save_cache`.)

- [ ] **Step 3: Verify scan.py parses**

Run: `python3 -c "import ast; ast.parse(open('scan.py').read()); print('scan.py parses')"`
Expected: `scan.py parses`

- [ ] **Step 4: Run the scan smoke suite**

Run: `python3 -m pytest tests/test_scan_smoke.py -v`
Expected: PASS (install `psycopg2-binary` first if the module is missing, then re-run). If the smoke test exercises the live fetch and now reaches `trends_cache.load_cache`, it fail-opens to `{}` without network — no failure expected. If any smoke test fails, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add scan.py
git commit -m "feat: wire durable day-cache into the scan (--no-cache to bypass)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Docs — backlog Done + CLAUDE.md bucket note

**Files:**
- Modify: `BACKLOG.md`, `CLAUDE.md`

**Interfaces:** none.

- [ ] **Step 1: Add the Done entry** at the top of the `## Done` list in `BACKLOG.md`:

```markdown
- ~~Sentiment — durable Trends day-cache~~ — successfully-fetched Trends batches are
  cached per UTC day in Supabase Storage (bucket `trends-cache`, one
  `trends_cache_<date>.json` object) so a re-triggered CI run or same-day re-run
  reuses them and re-fetches only the batches that 429'd/failed. Cache is per
  `(geo, batch)` (`src/data/trends_cache.py`), read/written in `_fetch_geo`, and
  loaded/saved around the fetch in `scan.py`. Fully **fail-open** — any Storage error
  logs a warning and the scan runs live/uncached; `--no-cache` bypasses it. Cuts the
  429 exposure from the ~4× region-aware call volume. *(2026-07-07)*
```

- [ ] **Step 2: Note the bucket in `CLAUDE.md`**

In `CLAUDE.md`, in the `## Backups` section, after the paragraph describing the
`db-backups` bucket, add:

```markdown
A second private bucket **`trends-cache`** holds the durable Google Trends day-cache
(`trends_cache_<UTC-date>.json`, one per day) so re-triggered scans reuse
already-fetched batches instead of re-hitting Google (429 mitigation). Same
`SUPABASE_SERVICE_KEY` credential as the backups; the cache is **fail-open**, so a
missing bucket or key only means scans run uncached. Bypass with `python3 scan.py
--no-cache`.
```

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md CLAUDE.md
git commit -m "docs: record durable trends day-cache and bucket" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Full suite green:** `python3 -m pytest -q` → branch baseline + ~11 new cache tests, 6 skipped (or DB modules run with `psycopg2-binary`). No regressions.
- [ ] **No `docs/` staged:** `git status --porcelain docs/` → empty.
- [ ] **Diff source-only:** `git diff --stat main...HEAD` touches only `src/`, `scan.py`, `tests/`, `BACKLOG.md`, `CLAUDE.md`, `design/`.
- [ ] Final whole-branch review, address findings, then `git push -u origin feature/trends-day-cache` and open a PR with `gh pr create` (per CLAUDE.md — Claude opens the PR; Jonas merges). **Do not merge.** Note in the PR body that the `trends-cache` bucket already exists (created during design).
