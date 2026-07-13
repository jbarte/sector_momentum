# Maintenance Sweep

**Date:** 2026-07-12
**Status:** Approved
**Backlog item:** Maintenance sweep (verified open, 2026-07-12)

## Summary

Four small, independent fixes bundled as one sweep:

1. Delete dead `src/data/stocktwits.py` + `tests/test_stocktwits.py`
2. Fix `_last_trading_day`/`_cache_is_fresh` triggering an unnecessary live
   re-fetch the day after a market holiday
3. Fix the price cache silently truncating a longer `start` range than what
   was originally cached
4. Deduplicate three repeated query/insert patterns in `src/state.py`
   (536 lines)

No schema changes, no changes to `state.py`'s public function signatures
(except `_cache_is_fresh`, private to `prices.py`, gaining a `start` param).

## 1. Delete dead `stocktwits.py`

Delete `src/data/stocktwits.py` (86 lines) and `tests/test_stocktwits.py`
(55 lines). Confirmed via grep that nothing outside its own test imports it —
the multi-source sentiment engine (Finnhub/StockTwits/Reddit) was already
fully removed in favor of symbol-based Google Trends; this module was the
one leftover.

## 2. Holiday-aware trading day (loosened freshness check)

`_last_trading_day()` stays unchanged (a Mon-Fri weekday proxy). The fix is
in `_cache_is_fresh()`: instead of requiring
`last_cached >= _last_trading_day()` exactly, tolerate a 4-calendar-day
window. (The snippet below already shows the `start` parameter added by
Section 3, since both changes land in the same function — this section only
concerns the last-date check.)

```python
def _cache_is_fresh(path: str, start: str) -> bool:
    ...
    last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
    if last_cached < date.today() - timedelta(days=4):
        return False
    ...
```

Four days covers a normal weekend (Fri close, checked Mon = 3 days back) plus
one extra day for the day after a single-day holiday, without needing any
holiday calendar or new dependency. It does not loosen freshness for the
common case (a stale Monday cache still fails the check).

## 3. Price cache respects requested `start`

`_cache_is_fresh` gains a required `start: str` parameter. After the
existing last-date check, it also verifies:

```python
cached_start = df.index.min().date() if hasattr(df.index.min(), "date") else df.index.min()
requested_start = pd.Timestamp(start).date()
if cached_start > requested_start + timedelta(days=7):
    return False
```

The 7-day tolerance absorbs a `start` landing on a weekend/holiday (the
actual earliest trading day on/after `start` may be a few days later than
`start` itself — that's still correct coverage, not truncation). If the
cache doesn't cover the requested range, it's treated as stale:
`fetch_prices` re-fetches live for the full requested range and overwrites
the cache file, so subsequent calls with the same (or shorter) range hit the
now-correctly-ranged cache.

`fetch_prices` passes its own `start` argument through to
`_cache_is_fresh(path, start)`.

## 4. `state.py` deduplication

Three shared helpers, each replacing an identical-shape pattern:

**Latest-scan query helper** — replaces the bodies of
`get_signals_for_latest_scan`, `get_sentiment_signals_for_latest_scan`,
`get_theme_scores_for_latest_scan`, `get_theme_signals_for_latest_scan`
(public signatures unchanged):

```python
def _latest_scan_query(conn, table: str, columns: str) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT {columns} FROM {table} t "
        f"JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON t.scan_id = m.max_id",
        conn,
    )
```

`table`/`columns` are always hardcoded literals at the 4 call sites, never
external input — same f-string-interpolation-of-trusted-literals pattern
`save_scan`'s existing delete loop already uses (`f"DELETE FROM {child} ..."`).

**Recent-scan-filter builder** — replaces the repeated
`"WHERE scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)"`
string across `get_rrg_history`, `get_theme_rrg_history`, `get_scan_history`,
`get_theme_scan_history`:

```python
def _recent_scan_filter(n_scans: int | None) -> tuple[str, tuple]:
    """Returns (SQL WHERE fragment or '', query params) for the last n_scans scans."""
    if n_scans is None:
        return "", ()
    return (
        "WHERE scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)",
        (n_scans,),
    )
```

`get_rrg_history`/`get_theme_rrg_history` keep their `n_scans: int = 6`
default (never `None` today) — only the WHERE-clause construction is shared,
not the default value semantics. Today only `get_scan_history`/
`get_theme_scan_history` use the `{scan_filter}`-templated query string;
`get_rrg_history`/`get_theme_rrg_history` currently embed the `WHERE ... LIMIT
%s` clause directly inline. This item restructures all four to build their
query string the same way — call `_recent_scan_filter(n_scans)`, then
`.format(scan_filter=...)` (or an f-string) to splice the returned fragment
into the base query, passing the returned params tuple to
`pd.read_sql_query`.

**DataFrame-to-rows insert helper** — replaces the repeated
"build tuples via list comprehension + `_to_float_or_none`" blocks (3 inside
`save_scan`, 2 inside `save_theme_scan`):

```python
def _rows_from_df(
    df, scan_id: int, key_cols: list[str], float_cols: list[str],
    raw_cols: list[str] | None = None,
) -> list[tuple]:
    """Build (scan_id, *key_cols, *float_cols, *raw_cols) tuples.

    float_cols are converted via _to_float_or_none; raw_cols pass through
    as-is (None if falsy) — covers sentiment_signals.text_value, the one
    column that isn't float data.
    """
    raw_cols = raw_cols or []
    return [
        (scan_id, *(row[k] for k in key_cols),
         *(_to_float_or_none(row.get(c)) for c in float_cols),
         *(row.get(c) or None for c in raw_cols))
        for _, row in df.iterrows()
    ]
```

Each insert call site still writes its own
`cur.executemany("INSERT INTO ... VALUES (...)", rows)` — only row-building
is shared, so each table's SQL stays explicit.

## Testing

- Existing tests (`tests/test_state_*.py`, `tests/test_prices.py`) already
  cover the *behavior* of every function touched here; since items 4's three
  helpers are pure refactors (identical SQL/output shape, just
  parameterized), running the existing suite after each refactor is the
  primary regression check — no new tests needed for item 4.
- Two new tests for `prices.py`:
  - `_cache_is_fresh` returns `True` for a cache one day past a simulated
    holiday gap (last_cached = 4 days ago, today = day after holiday)
  - `_cache_is_fresh` returns `False` (triggering re-fetch) when the cached
    data's earliest date doesn't cover a newly-requested longer `start`
- `tests/test_stocktwits.py` is deleted, not modified.

## Out of Scope

- No schema changes.
- No public API changes to `state.py` beyond the four latest-scan functions
  keeping their existing signatures (only their bodies change) and the two
  insert functions keeping their existing signatures.
- No changes to `_last_trading_day()` itself — it stays a simple weekday
  proxy; the fix lives entirely in the freshness-check tolerance.
- No new dependencies (ruled out `pandas_market_calendars` per design
  discussion — the tolerance-window approach avoids needing a real holiday
  calendar).
