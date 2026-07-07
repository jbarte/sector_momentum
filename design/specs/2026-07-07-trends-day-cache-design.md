# Durable Trends day-cache — design

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/trends-day-cache`

## Problem

Region-aware Trends pulls now cost ~4× the API calls (US + EU×3 geos ≈ 36 batches
per scan), which raises Google 429 risk and runtime. A single 429 zeroes a batch,
degrading that scan's sentiment. There is no way to reuse already-fetched data:
GitHub Actions runs on an ephemeral filesystem, so a re-triggered daily run starts
cold and re-hits every batch.

## Goal

A **durable, per-day cache** of successfully-fetched Trends batches, stored in
Supabase Storage (like the DB backups), so a re-triggered CI run — or a manual
same-day re-run — reuses the batches that already succeeded and re-fetches only the
ones that failed. The cache is a pure optimization: if it is unavailable for any
reason, the scan behaves exactly as it does today.

**Non-goals:** cross-day caching (a new UTC day = a fresh fetch — by design),
cache pruning (deferred), changing batching/sleep tuning (separate concern), any
change to scoring or the composite.

## Key decisions

1. **Per-(geo, batch) granularity, not whole-result.** Caching the entire
   `_trends_by_key` would persist a 429-holed partial result and reuse the holes
   forever. Caching each *successful* batch means failed batches are simply absent
   and get retried next run — the precise 429-resume behavior wanted. Each cached
   entry is independently valid because `_normalize_by_anchor` divides the anchor
   out *within* the batch, so a batch's normalized series is self-contained.
2. **Durable in Supabase Storage**, reusing `src/storage_backup.py`
   (`upload`/`download`/`list_objects`, service-key auth already wired for backups).
   Dedicated bucket `trends-cache`, one JSON object per UTC day.
3. **Separation of concerns:** `trends_symbols.py` does batch-level read/write on an
   in-memory `cache` dict passed to it; `scan.py` owns the Storage load-before /
   save-after and all failure guards. `trends_symbols.py` gains no Storage import.
4. **Fail-open.** Any Storage error (missing `SUPABASE_SERVICE_KEY`, network,
   missing bucket, corrupt object) logs a warning and the scan proceeds with a live
   fetch — the cache never fails the scan.

## Cache shape

One JSON object per UTC day, `trends_cache_<YYYY-MM-DD>.json`, in bucket
`trends-cache`:

```json
{
  "US": {
    "XLK|VGT|XLE|XLF": { "XLK": [/* 13 floats */], "VGT": [ ... ], ... }
  },
  "DE": { "...": { ... } }
}
```

- Top level keyed by **geo** (`"US"`, `"DE"`, `"FR"`, `"GB"`, or `""` for worldwide).
- Second level keyed by **batch-key** = the batch's tickers sorted and joined with
  `|` (deterministic across runs, since batches derive deterministically from the
  region symbol list and `batch_size`).
- Value = the batch's anchor-normalized `{ticker: series}` — exactly what
  `_normalize_by_anchor` returns for that batch.

## Components

### 1. `src/data/trends_cache.py` (new)

```python
DEFAULT_CACHE_BUCKET = "trends-cache"

def cache_object_name(date_str: str) -> str:
    """'2026-07-07' -> 'trends_cache_2026-07-07.json'."""

def batch_key(tickers: list[str]) -> str:
    """Deterministic key for a batch: sorted tickers joined by '|'."""

def load_cache(date_str: str, bucket: str = DEFAULT_CACHE_BUCKET) -> dict:
    """Download + parse the day's cache object. Missing object or any Storage
    error -> {} (fail-open, logged). Corrupt JSON -> {} (logged)."""

def save_cache(date_str: str, cache: dict, bucket: str = DEFAULT_CACHE_BUCKET) -> None:
    """Serialize + upload the day's cache. Any Storage error is logged and
    swallowed (never raises)."""
```

`load_cache`/`save_cache` wrap `storage_backup.download`/`upload` and catch broadly
(the cache is optional). `cache_object_name` and `batch_key` are pure and unit-tested.

### 2. `_fetch_geo` becomes cache-aware (`src/data/trends_symbols.py`)

Add a final param `cache: dict | None = None`. When `None`, behavior is **identical
to today** (no caching). When a dict is passed, per batch:

- Compute `key = batch_key(batch)` and look up `cache.get(geo, {}).get(key)`.
- **Hit:** `norm_by_symbol.update(cached)`, skip the `build_payload`/`interest_over_time`
  call *and* the inter-batch sleep for that batch.
- **Miss:** fetch and normalize as today; on success, write the batch's normalized
  dict into `cache.setdefault(geo, {})[key] = <normalized>`. On failure (429/empty),
  write nothing — it will be retried on the next run.

The batch-key is computed from `batch` (the raw tickers), not the substituted query
terms, so entity-mid changes don't fragment keys.

### 3. `fetch_symbol_trends` threads the cache through

Add a final param `cache: dict | None = None`; pass it into each `_fetch_geo(...)`
call. No other logic changes. `cache=None` (the default) preserves today's behavior
exactly — the additivity guard for all existing tests.

### 4. `scan.py` owns Storage load/save

In the sentiment section:

```python
from datetime import datetime, timezone
from src.data import trends_cache

_use_cache = not args.no_cache
_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
_cache = trends_cache.load_cache(_today) if _use_cache else None
_trends_by_key = fetch_symbol_trends(
    _symbol_map, anchor=_anchor, entities=_entities,
    region_geos=_region_geos, cache=_cache,
)
if _use_cache and _cache is not None:
    trends_cache.save_cache(_today, _cache)
```

- New CLI flag `--no-cache` (argparse) to bypass the cache for local/manual runs.
- `load_cache` returns `{}` on any failure; passing `{}` still works (all misses,
  populated as batches succeed, saved at the end). Passing `None` (flag off) disables
  caching in `_fetch_geo`.

## Data flow

```
scan.py: date = utc today
  trends_cache.load_cache(date)  ──(Storage download; fail-open → {})──▶ cache dict
  fetch_symbol_trends(..., cache=cache)
     └─ per region/geo: _fetch_geo(..., cache)
           per batch: key = batch_key(batch)
             hit  → reuse cached {ticker: series}, no API call, no sleep
             miss → fetch → normalize → cache[geo][key] = normalized
  trends_cache.save_cache(date, cache)  ──(Storage upload; fail-open)
```

## Error handling / degradation

- No `SUPABASE_SERVICE_KEY` / network error / missing bucket on load → `load_cache`
  logs a warning, returns `{}`; scan does a full live fetch and attempts a save
  (which will also warn-and-continue if Storage is down).
- Corrupt/unparseable cache object → `{}` (logged), full refetch.
- `save_cache` failure → logged, swallowed; the scan already has its results.
- A batch that 429s is never written to the cache, so it retries next run.
- `cache=None` (flag off or load returned None) → `_fetch_geo` skips all cache logic.

## Testing

Pure-function unit tests (no network):
- `cache_object_name` maps a date to the expected filename.
- `batch_key` is order-independent (`["VGT","XLK"]` and `["XLK","VGT"]` → same key).

`_fetch_geo` cache behavior with a `FakeClient` + in-memory cache dict:
- **Miss then write:** empty cache → client *is* called; afterwards the cache
  contains the batch's normalized series under `[geo][batch_key]`.
- **Hit skips the call:** a pre-populated cache entry → `build_payload` is **not**
  called for that batch, and the returned series equals the cached values.
- `cache=None` → behaves exactly as the existing no-cache path (regression guard).

`load_cache`/`save_cache` Storage I/O is tested by monkeypatching
`storage_backup.download`/`upload` (assert fail-open returns `{}` on raised errors;
assert a round-trip serializes/deserializes the dict). No real network.

Full suite must stay green.

## Honest caveats / follow-ups

- **New bucket** `trends-cache` must be created in Supabase (like `db-backups`) — a
  one-time manual setup step (post-merge). Until it exists, `load_cache`/`save_cache`
  warn-and-continue, so the scan still works (just uncached).
- **Same-day config changes** (anchor, entity mids, geo set, window/timeframe) are
  *not* reflected until the next UTC day, because cached batches are keyed only by
  geo + tickers. Acceptable for a day-cache; noted so it isn't surprising.
- **No pruning:** day-cache objects accumulate in the bucket. They're small; pruning
  old objects is a deferred follow-up.
- **`window` is a crash edge, not just staleness:** `window` (fixed at 13, not
  config-driven) is not part of the cache key. A same-day change to `window` would
  mix cached series of the old length with freshly-fetched series of the new length
  inside one run, which can raise a ragged-array error in `_average_geo_series`
  (`np.array` over unequal-length lists). Bump the UTC day or clear the cache if
  `window` ever changes.
