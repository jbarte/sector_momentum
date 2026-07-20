# CI price cache — `actions/cache` for `data/cache/`

## Problem

`data/cache/` is gitignored and CI runners are ephemeral, so every daily
scan live-fetches all ~545 tickers (30 sector/benchmark ETFs + ~500 S&P 500
constituents + 15 theme ETFs) from stooq and yfinance. This is the
pipeline's biggest 429-risk surface and adds unnecessary runtime to every
scan.

## Solution

Add a single `actions/cache@v4` step to `scan.yml` that persists
`data/cache/` across runs. The existing per-file freshness logic
(`_cache_is_fresh` in `prices.py`, `_cache_fresh` in `constituents.py`)
remains the authority on whether a cached file needs re-fetching — the
Actions cache is purely a persistence layer.

## Cache key strategy

```yaml
key: price-cache-${{ github.run_id }}
restore-keys: |
  price-cache-
```

- `run_id` is unique per run, so every run saves a fresh cache snapshot
  (GitHub Actions only writes when no exact key match exists).
- `restore-keys: price-cache-` restores the most recent prior cache on
  miss, regardless of age.
- The per-file freshness check (4-day tolerance for prices, configurable
  TTL for constituents) handles actual staleness.
- GitHub's automatic 7-day eviction of unused cache entries keeps storage
  bounded without manual rotation.

## Placement in `scan.yml`

Insert between "Install dependencies" and "Run tests":

```yaml
- name: Restore price cache
  uses: actions/cache@v4
  with:
    path: data/cache
    key: price-cache-${{ github.run_id }}
    restore-keys: |
      price-cache-
```

Both the test suite and the scan benefit from cached parquets. The cache
post-job runs automatically after the workflow completes, saving the
updated `data/cache/` directory.

## What doesn't change

- `_cache_is_fresh` / `_cache_fresh` logic — unchanged, still the
  authority on per-file staleness.
- `.gitignore` — `data/cache/` stays gitignored.
- `build-docs.yml` — reads from the database, not from `data/cache/`.
- No new secrets, scripts, or dependencies.

## Verification

No code changes to test. Verify by:

1. Checking the Actions log for "Cache restored successfully" on the
   second run after merge.
2. Confirming the per-source stats log shows mostly cache hits
   (`cache N/N`) instead of live fetches (`stooq`/`yfinance`).

## Out of scope

- Cache size monitoring (GitHub's 10 GB limit and 7-day eviction handle
  this).
- Per-source or per-file cache splitting (one directory is sufficient).
- `build-docs.yml` caching (it doesn't use `data/cache/`).
