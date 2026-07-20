# Restore stooq as a working price source

## Problem

`pandas-datareader`'s stooq driver is broken with pandas 3.x
(`data_source='stooq' is not implemented`). Every scan silently falls
through to yfinance — the same single point of failure that caused the
Jul 18–19 outage. There is also no visibility into which source actually
served data during a run.

## Solution

Replace `_fetch_stooq` in `src/data/prices.py` with a direct
`requests.get` to stooq's CSV endpoint. Remove `pandas-datareader` from
`requirements.txt`. Add per-source success/failure counters with a
WARNING log when any source goes 0-for-N in a run.

## Scope

### 1. Replace `_fetch_stooq` implementation

Current code uses `pdr.DataReader(ticker, "stooq", start, end)`.

Replace with:

```
GET https://stooq.com/q/d/l/?s={symbol}&d1={YYYYMMDD}&d2={YYYYMMDD}&i=d
```

Response is a CSV with columns: `Date,Open,High,Low,Close,Volume`.
Parse with `pd.read_csv(io.StringIO(resp.text))`, set `Date` as index.

**Symbol mapping** (`_stooq_symbol(ticker)`):
- Ticker has no dot (US ETFs like `XLK`, `RSP`): lowercase + `.us`
  → `xlk.us`, `rsp.us`
- Ticker has a dot (EU ETFs like `EXV3.DE`, `EXSA.DE`): lowercase as-is
  → `exv3.de`, `exsa.de`

**Error handling**: raise on non-200 status or if the response body
contains fewer than 2 lines (stooq returns a header-only CSV for unknown
symbols). The existing `_fetch_single` try/except already handles
exceptions from fetch functions.

**Timeout**: 15-second timeout on the request (matches the kind of
latency acceptable for a batch fetch of ~30 tickers).

### 2. Remove `pandas-datareader` dependency

Delete `pandas-datareader>=0.10` from `requirements.txt`. No other code
imports it.

### 3. Per-source success/failure logging

After the `fetch_prices` loop completes, count how many tickers each
source served (track which source won in `_fetch_single` via its return
value). Log at INFO level: `"Price sources: stooq {n}/{total}, yfinance
{m}/{total}, cache {c}/{total}"`. If any live source (stooq or yfinance)
was attempted but went 0-for-N, log at WARNING: `"stooq: 0/{attempted}
succeeded — source may be down"`.

This requires `_fetch_single` to return `(source_name, df)` instead of
just `df`, and `fetch_prices` to track the winning source per ticker.

### 4. Tests

- **`test_stooq_symbol_mapping`**: verify `_stooq_symbol("XLK")` →
  `"xlk.us"`, `_stooq_symbol("EXV3.DE")` → `"exv3.de"`.
- **`test_fetch_stooq_parses_csv`**: mock `requests.get` to return a
  valid CSV string, verify the returned DataFrame has the right shape
  and columns.
- **`test_fetch_stooq_raises_on_bad_status`**: mock a 404 response,
  verify an exception is raised.
- **`test_source_stats_warning`**: mock `_fetch_stooq` to always fail,
  `_fetch_yfinance` to succeed, call `fetch_prices` with 2 tickers,
  verify the WARNING log about stooq going 0-for-N.
- Update existing tests: they mock `_fetch_stooq` at the function level,
  so the function name staying the same means no mock-target changes
  needed.

## Out of scope

- Rate limiting / retry logic for stooq (the existing per-ticker
  try/except + fallback is sufficient).
- Changing the source priority order (stooq first, yfinance second).
- EU-specific symbol edge cases beyond the `.DE` suffix pattern.
