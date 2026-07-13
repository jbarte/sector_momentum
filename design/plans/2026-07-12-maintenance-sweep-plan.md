# Maintenance Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four small, independent maintenance fixes: delete dead code, fix two latent bugs in the price cache, and deduplicate three repeated patterns in `state.py`.

**Architecture:** Each task is independent and can be reviewed/merged on its own merits — they only share a branch for convenience. No schema changes; no public API changes except `_cache_is_fresh` (private to `prices.py`) gaining an optional `start` parameter.

**Tech Stack:** Python, psycopg2/pandas (state.py), pytest

## Global Constraints

- Branch: `chore/maintenance-sweep` (already created; design spec committed at `e82e581`)
- Never commit `docs/` from feature branches (CI rebuilds on main)
- Follow conventional commits (`feat:`, `fix:`, `chore:`, `refactor:`)
- `state.py` refactors (Task 4) are pure — same SQL semantics, same output shape, same public signatures for every function except internal body changes. The existing DB-backed tests are the regression check; no new tests needed for Task 4.
- `_cache_is_fresh` gets `start: str | None = None` (optional, not required) — several existing tests call it with only `path`, testing only the last-date freshness logic. Making `start` optional avoids rewriting those, while `fetch_prices` always passes its own `start`.

---

### Task 1: Delete dead `stocktwits.py`

**Files:**
- Delete: `src/data/stocktwits.py`
- Delete: `tests/test_stocktwits.py`

**Interfaces:**
- Consumes: nothing (this task only removes code)
- Produces: nothing new; confirms no other module imports `src.data.stocktwits`

- [ ] **Step 1: Confirm nothing else imports it**

Run: `grep -rn "stocktwits" --include="*.py" . | grep -v ".claude/worktrees"`
Expected: only hits inside `src/data/stocktwits.py` and `tests/test_stocktwits.py` themselves

- [ ] **Step 2: Delete both files**

```bash
git rm src/data/stocktwits.py tests/test_stocktwits.py
```

- [ ] **Step 3: Run the full test suite to confirm nothing else broke**

Run: `python3 -m pytest -q`
Expected: same pass count as before minus the 3 tests that were in `test_stocktwits.py`, 0 failures

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete dead src/data/stocktwits.py"
```

---

### Task 2: Holiday-aware trading day (loosened freshness check)

**Files:**
- Modify: `src/data/prices.py` (`_cache_is_fresh`, ~line 41)
- Modify: `tests/test_prices.py` (`test_cache_is_fresh_returns_true_for_current_data`, plus 2 new tests)

**Interfaces:**
- Consumes: nothing new
- Produces: `_cache_is_fresh(path: str, start: str | None = None) -> bool` — the `start`
  parameter is added here (required by Task 3) but this task's logic change only
  concerns the last-date tolerance; Task 3 adds the start-coverage check inside
  the same function body.

- [ ] **Step 1: Write the failing tests**

In `tests/test_prices.py`, replace `test_cache_is_fresh_returns_true_for_current_data`
(it currently mocks `_last_trading_day`, which this fix stops calling) with:

```python
def test_cache_is_fresh_returns_true_for_current_data(tmp_path):
    """A cache file whose last date is within the tolerance window is fresh."""
    df = _make_price_df(n=10, start_date=str(date.today() - timedelta(days=14)))
    path = str(tmp_path / "test.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is True
```

Add two new tests right after it:

```python
def test_cache_is_fresh_tolerates_gap_after_holiday(tmp_path):
    """A cache 4 days old (e.g. the day after a single-day market holiday) is still fresh."""
    idx = pd.DatetimeIndex([pd.Timestamp(date.today() - timedelta(days=4))])
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "gap.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is True


def test_cache_is_fresh_false_beyond_tolerance(tmp_path):
    """A cache 5 days old (past the tolerance window) is stale."""
    idx = pd.DatetimeIndex([pd.Timestamp(date.today() - timedelta(days=5))])
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "toostale.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_prices.py -k "cache_is_fresh" -v`
Expected: `test_cache_is_fresh_tolerates_gap_after_holiday` and
`test_cache_is_fresh_false_beyond_tolerance` FAIL (function doesn't exist yet /
current logic doesn't match); `test_cache_is_fresh_returns_true_for_current_data`
may currently pass by coincidence but re-run after Step 3 to confirm intent

- [ ] **Step 3: Implement the loosened freshness check**

In `src/data/prices.py`, replace:

```python
def _cache_is_fresh(path: str) -> bool:
    """Return True if the cache file exists and its last date >= last trading day."""
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return False
        last_trading = _last_trading_day()
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        return last_cached >= last_trading
    except Exception:
        return False
```

With:

```python
def _cache_is_fresh(path: str, start: str | None = None) -> bool:
    """Return True if the cache file exists, its last date is within a
    4-day tolerance of today (covers weekends and the day after a single
    market holiday without needing a holiday calendar), and — when `start`
    is given — its earliest date covers the requested range."""
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return False
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        if last_cached < date.today() - timedelta(days=4):
            return False
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prices.py -k "cache_is_fresh" -v`
Expected: PASS (all `cache_is_fresh` tests)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all pass (no regressions in `fetch_prices` tests, which mock `_cache_is_fresh` wholesale and are unaffected by this internal logic change)

- [ ] **Step 6: Commit**

```bash
git add src/data/prices.py tests/test_prices.py
git commit -m "fix: tolerate post-holiday gap in price cache freshness check"
```

---

### Task 3: Price cache respects requested `start`

**Files:**
- Modify: `src/data/prices.py` (`_cache_is_fresh`, `fetch_prices`)
- Modify: `tests/test_prices.py` (2 new tests, 1 existing test's `side_effect` signature)

**Interfaces:**
- Consumes: `_cache_is_fresh(path, start=None)` from Task 2
- Produces: `_cache_is_fresh(path, start)` now also validates range coverage;
  `fetch_prices` passes its own `start` through

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prices.py`, after the Task 2 tests:

```python
def test_cache_is_fresh_false_when_start_not_covered(tmp_path):
    """Cache covers only the last 30 days; a 2-year lookback request is not covered."""
    df = _make_price_df(n=20, start_date=str(date.today() - timedelta(days=30)))
    path = str(tmp_path / "short.parquet")
    df.to_parquet(path)
    long_start = str(date.today() - timedelta(days=730))
    assert _cache_is_fresh(path, start=long_start) is False


def test_cache_is_fresh_true_when_start_covered(tmp_path):
    """Cache's earliest date is on/before the requested start (within tolerance)."""
    df = _make_price_df(n=20, start_date=str(date.today() - timedelta(days=30)))
    path = str(tmp_path / "covered.parquet")
    df.to_parquet(path)
    recent_start = str(date.today() - timedelta(days=25))
    assert _cache_is_fresh(path, start=recent_start) is True
```

In the same file, update `test_fetch_prices_handles_mix_of_cached_and_fresh`'s
`side_effect` function — find:

```python
    def fresh_side_effect(path):
        return "XLK" in path
```

Replace with:

```python
    def fresh_side_effect(path, start=None):
        return "XLK" in path
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/test_prices.py -k "start_covered or start_not_covered" -v`
Expected: FAIL (`_cache_is_fresh` doesn't check `start` yet)

- [ ] **Step 3: Implement the start-coverage check**

In `src/data/prices.py`, extend `_cache_is_fresh` (from Task 2) by replacing:

```python
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        if last_cached < date.today() - timedelta(days=4):
            return False
        return True
```

With:

```python
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        if last_cached < date.today() - timedelta(days=4):
            return False
        if start is not None:
            cached_start = df.index.min().date() if hasattr(df.index.min(), "date") else df.index.min()
            requested_start = pd.Timestamp(start).date()
            if cached_start > requested_start + timedelta(days=7):
                return False
        return True
```

- [ ] **Step 4: Wire `start` through `fetch_prices`**

In `src/data/prices.py`, inside `fetch_prices`, find:

```python
        if _cache_is_fresh(path):
```

Replace with:

```python
        if _cache_is_fresh(path, start):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prices.py -v`
Expected: all PASS, including the 2 new tests and the updated `fresh_side_effect`

- [ ] **Step 6: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/data/prices.py tests/test_prices.py
git commit -m "fix: price cache re-fetches when it doesn't cover requested start"
```

---

### Task 4: `state.py` deduplication

**Files:**
- Modify: `src/state.py` (add 3 helpers; rewrite 9 function bodies to use them)

**Interfaces:**
- Consumes: nothing new
- Produces: `_latest_scan_query(conn, table, columns)`,
  `_recent_scan_filter(n_scans) -> (condition: str, params: tuple)`,
  `_rows_from_df(df, scan_id, key_cols, float_cols, raw_cols=None) -> list[tuple]`
  — all private helpers. Every existing public function keeps its exact
  signature; only bodies change.

This task is a pure refactor: every rewritten function must produce
byte-identical query results to its current version. The existing DB-backed
tests (`tests/test_state_smoke.py`, `tests/test_theme_state.py`) are the
regression check — no new tests are added in this task.

- [ ] **Step 1: Add the three helpers**

In `src/state.py`, in the `# Helpers` section at the bottom, after
`_to_float_or_none`, add:

```python
def _latest_scan_query(conn, table: str, columns: str) -> pd.DataFrame:
    """Shared shape for 'all rows from <table> belonging to the most recent
    scan'. `columns` must reference the table via alias 't'
    (e.g. 't.region, t.gics_sector')."""
    return pd.read_sql_query(
        f"SELECT {columns} FROM {table} t "
        f"JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON t.scan_id = m.max_id",
        conn,
    )


def _recent_scan_filter(n_scans: int | None) -> tuple[str, tuple]:
    """Returns (SQL boolean condition on sc.scan_id, params) restricting to
    the last n_scans scans — assumes the query aliases the scans table as
    'sc'. When n_scans is None, returns a condition matching all rows."""
    if n_scans is None:
        return "TRUE", ()
    return (
        "sc.scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)",
        (n_scans,),
    )


def _rows_from_df(
    df: pd.DataFrame,
    scan_id: int,
    key_cols: list[str],
    float_cols: list[str],
    raw_cols: list[str] | None = None,
) -> list[tuple]:
    """Build (scan_id, *key_cols, *float_cols, *raw_cols) tuples from a
    DataFrame. float_cols are converted via _to_float_or_none; raw_cols pass
    through as-is (None if falsy) — covers columns like
    sentiment_signals.text_value that aren't float data."""
    raw_cols = raw_cols or []
    return [
        (scan_id, *(row[k] for k in key_cols),
         *(_to_float_or_none(row.get(c)) for c in float_cols),
         *(row.get(c) or None for c in raw_cols))
        for _, row in df.iterrows()
    ]
```

- [ ] **Step 2: Rewrite the four latest-scan query functions**

In `src/state.py`, replace:

```python
def get_signals_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """
    Return all signal rows for the most recent scan.
    Columns: region, gics_sector, signal_name, raw_value, z_value
    Returns empty DataFrame if no scans exist.
    """
    return pd.read_sql_query(
        """
        SELECT s.region, s.gics_sector, s.signal_name, s.raw_value, s.z_value
        FROM signals s
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON s.scan_id = m.max_id
        """,
        conn,
    )


def get_sentiment_signals_for_latest_scan(
    conn: psycopg2.extensions.connection,
) -> pd.DataFrame:
    """
    Return all derived sentiment-signal rows for the most recent scan.
    Columns: region, gics_sector, signal_name, value
    Returns empty DataFrame if no scans (or no sentiment rows) exist.
    """
    return pd.read_sql_query(
        """
        SELECT ss.region, ss.gics_sector, ss.signal_name, ss.value, ss.text_value
        FROM sentiment_signals ss
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON ss.scan_id = m.max_id
        """,
        conn,
    )
```

With:

```python
def get_signals_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """
    Return all signal rows for the most recent scan.
    Columns: region, gics_sector, signal_name, raw_value, z_value
    Returns empty DataFrame if no scans exist.
    """
    return _latest_scan_query(
        conn, "signals", "t.region, t.gics_sector, t.signal_name, t.raw_value, t.z_value"
    )


def get_sentiment_signals_for_latest_scan(
    conn: psycopg2.extensions.connection,
) -> pd.DataFrame:
    """
    Return all derived sentiment-signal rows for the most recent scan.
    Columns: region, gics_sector, signal_name, value
    Returns empty DataFrame if no scans (or no sentiment rows) exist.
    """
    return _latest_scan_query(
        conn, "sentiment_signals",
        "t.region, t.gics_sector, t.signal_name, t.value, t.text_value",
    )
```

Then replace:

```python
def get_theme_scores_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme score rows for the most recent scan. Empty DataFrame if none."""
    return pd.read_sql_query(
        """
        SELECT ts.theme, ts.level_score, ts.change_score, ts.data_score,
               ts.sentiment_score, ts.composite, ts.rank
        FROM theme_scores ts
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON ts.scan_id = m.max_id
        """,
        conn,
    )


def get_theme_signals_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme signal rows for the most recent scan. Empty DataFrame if none."""
    return pd.read_sql_query(
        """
        SELECT tsg.theme, tsg.signal_name, tsg.raw_value, tsg.z_value
        FROM theme_signals tsg
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON tsg.scan_id = m.max_id
        """,
        conn,
    )
```

With:

```python
def get_theme_scores_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme score rows for the most recent scan. Empty DataFrame if none."""
    return _latest_scan_query(
        conn, "theme_scores",
        "t.theme, t.level_score, t.change_score, t.data_score, t.sentiment_score, t.composite, t.rank",
    )


def get_theme_signals_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme signal rows for the most recent scan. Empty DataFrame if none."""
    return _latest_scan_query(
        conn, "theme_signals", "t.theme, t.signal_name, t.raw_value, t.z_value"
    )
```

- [ ] **Step 3: Run the DB-backed tests to verify the latest-scan queries still work**

Run: `python3 -m pytest tests/test_state_smoke.py tests/test_theme_state.py -v`
Expected: all PASS (requires `TEST_DATABASE_URL` env var per this repo's test setup — see `tests/test_state_smoke.py` header if any fail with a connection error)

- [ ] **Step 4: Rewrite the four history query functions**

Replace:

```python
def get_theme_rrg_history(
    conn: psycopg2.extensions.connection,
    n_scans: int = 6,
) -> pd.DataFrame:
    """rs_ratio and rs_momentum for themes over the last n_scans, for RRG tail traces.

    Columns: scan_id, run_at, region, gics_sector, rs_ratio, rs_momentum
    (aliased to match get_rrg_history output so _build_rrg_figure works as-is).
    """
    return pd.read_sql_query(
        """
        SELECT sc.scan_id, sc.run_at, 'THEME' AS region, tsg.theme AS gics_sector,
               MAX(CASE WHEN tsg.signal_name = 'rs_ratio'    THEN tsg.raw_value END) AS rs_ratio,
               MAX(CASE WHEN tsg.signal_name = 'rs_momentum' THEN tsg.raw_value END) AS rs_momentum
        FROM theme_signals tsg
        JOIN scans sc ON sc.scan_id = tsg.scan_id
        WHERE tsg.scan_id IN (
            SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s
        )
        AND tsg.signal_name IN ('rs_ratio', 'rs_momentum')
        GROUP BY sc.scan_id, sc.run_at, tsg.theme
        ORDER BY sc.scan_id ASC, tsg.theme
        """,
        conn,
        params=(n_scans,),
    )


def get_theme_scan_history(
    conn: psycopg2.extensions.connection,
    n_scans: int | None = None,
) -> pd.DataFrame:
    """Theme scores across scans, aliased region="THEME"/gics_sector=theme for reuse.

    Columns: scan_id, run_at, region, gics_sector, level_score, change_score,
    data_score, sentiment_score, composite, rank. Ordered by run_at ASC, theme.
    n_scans=None returns all scans. Empty DataFrame if no theme rows exist.
    """
    base = """
        SELECT sc.scan_id, sc.run_at, 'THEME' AS region, ts.theme AS gics_sector,
               ts.level_score, ts.change_score, ts.data_score, ts.sentiment_score,
               ts.composite, ts.rank
        FROM theme_scores ts
        JOIN scans sc ON sc.scan_id = ts.scan_id
        {scan_filter}
        ORDER BY sc.run_at ASC, ts.theme
    """
    if n_scans is None:
        return pd.read_sql_query(base.format(scan_filter=""), conn)
    return pd.read_sql_query(
        base.format(
            scan_filter="WHERE sc.scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)"
        ),
        conn,
        params=(n_scans,),
    )


def get_rrg_history(
    conn: psycopg2.extensions.connection,
    n_scans: int = 6,
) -> pd.DataFrame:
    """
    Return rs_ratio and rs_momentum for the last n_scans scans, for RRG tail traces.
    Columns: scan_id, run_at, region, gics_sector, rs_ratio, rs_momentum
    """
    return pd.read_sql_query(
        """
        SELECT sc.scan_id, sc.run_at, sig.region, sig.gics_sector,
               MAX(CASE WHEN sig.signal_name = 'rs_ratio'    THEN sig.raw_value END) AS rs_ratio,
               MAX(CASE WHEN sig.signal_name = 'rs_momentum' THEN sig.raw_value END) AS rs_momentum
        FROM signals sig
        JOIN scans sc ON sc.scan_id = sig.scan_id
        WHERE sig.scan_id IN (
            SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s
        )
        AND sig.signal_name IN ('rs_ratio', 'rs_momentum')
        GROUP BY sc.scan_id, sc.run_at, sig.region, sig.gics_sector
        ORDER BY sc.scan_id ASC, sig.region, sig.gics_sector
        """,
        conn,
        params=(n_scans,),
    )


def get_scan_history(
    conn: psycopg2.extensions.connection,
    n_scans: int | None = 10,
) -> pd.DataFrame:
    """
    Return scores for the last n_scans scans joined with scan metadata.
    When n_scans is None, returns ALL scans.
    Columns: scan_id, run_at, region, gics_sector,
             level_score, change_score, data_score, sentiment_score, composite, rank
    Ordered by (run_at ASC, region, gics_sector).
    Returns empty DataFrame if no scans exist.
    """
    base = """
        SELECT sc.scan_id, sc.run_at, s.region, s.gics_sector,
               s.level_score, s.change_score, s.data_score, s.sentiment_score,
               s.composite, s.rank
        FROM scores s
        JOIN scans sc ON sc.scan_id = s.scan_id
        {scan_filter}
        ORDER BY sc.run_at ASC, s.region, s.gics_sector
    """
    if n_scans is None:
        query = base.format(scan_filter="")
        return pd.read_sql_query(query, conn)
    query = base.format(
        scan_filter="WHERE sc.scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT %s)"
    )
    return pd.read_sql_query(query, conn, params=(n_scans,))
```

With:

```python
def get_theme_rrg_history(
    conn: psycopg2.extensions.connection,
    n_scans: int = 6,
) -> pd.DataFrame:
    """rs_ratio and rs_momentum for themes over the last n_scans, for RRG tail traces.

    Columns: scan_id, run_at, region, gics_sector, rs_ratio, rs_momentum
    (aliased to match get_rrg_history output so _build_rrg_figure works as-is).
    """
    condition, params = _recent_scan_filter(n_scans)
    query = f"""
        SELECT sc.scan_id, sc.run_at, 'THEME' AS region, tsg.theme AS gics_sector,
               MAX(CASE WHEN tsg.signal_name = 'rs_ratio'    THEN tsg.raw_value END) AS rs_ratio,
               MAX(CASE WHEN tsg.signal_name = 'rs_momentum' THEN tsg.raw_value END) AS rs_momentum
        FROM theme_signals tsg
        JOIN scans sc ON sc.scan_id = tsg.scan_id
        WHERE {condition}
        AND tsg.signal_name IN ('rs_ratio', 'rs_momentum')
        GROUP BY sc.scan_id, sc.run_at, tsg.theme
        ORDER BY sc.scan_id ASC, tsg.theme
    """
    return pd.read_sql_query(query, conn, params=params)


def get_theme_scan_history(
    conn: psycopg2.extensions.connection,
    n_scans: int | None = None,
) -> pd.DataFrame:
    """Theme scores across scans, aliased region="THEME"/gics_sector=theme for reuse.

    Columns: scan_id, run_at, region, gics_sector, level_score, change_score,
    data_score, sentiment_score, composite, rank. Ordered by run_at ASC, theme.
    n_scans=None returns all scans. Empty DataFrame if no theme rows exist.
    """
    condition, params = _recent_scan_filter(n_scans)
    query = f"""
        SELECT sc.scan_id, sc.run_at, 'THEME' AS region, ts.theme AS gics_sector,
               ts.level_score, ts.change_score, ts.data_score, ts.sentiment_score,
               ts.composite, ts.rank
        FROM theme_scores ts
        JOIN scans sc ON sc.scan_id = ts.scan_id
        WHERE {condition}
        ORDER BY sc.run_at ASC, ts.theme
    """
    return pd.read_sql_query(query, conn, params=params)


def get_rrg_history(
    conn: psycopg2.extensions.connection,
    n_scans: int = 6,
) -> pd.DataFrame:
    """
    Return rs_ratio and rs_momentum for the last n_scans scans, for RRG tail traces.
    Columns: scan_id, run_at, region, gics_sector, rs_ratio, rs_momentum
    """
    condition, params = _recent_scan_filter(n_scans)
    query = f"""
        SELECT sc.scan_id, sc.run_at, sig.region, sig.gics_sector,
               MAX(CASE WHEN sig.signal_name = 'rs_ratio'    THEN sig.raw_value END) AS rs_ratio,
               MAX(CASE WHEN sig.signal_name = 'rs_momentum' THEN sig.raw_value END) AS rs_momentum
        FROM signals sig
        JOIN scans sc ON sc.scan_id = sig.scan_id
        WHERE {condition}
        AND sig.signal_name IN ('rs_ratio', 'rs_momentum')
        GROUP BY sc.scan_id, sc.run_at, sig.region, sig.gics_sector
        ORDER BY sc.scan_id ASC, sig.region, sig.gics_sector
    """
    return pd.read_sql_query(query, conn, params=params)


def get_scan_history(
    conn: psycopg2.extensions.connection,
    n_scans: int | None = 10,
) -> pd.DataFrame:
    """
    Return scores for the last n_scans scans joined with scan metadata.
    When n_scans is None, returns ALL scans.
    Columns: scan_id, run_at, region, gics_sector,
             level_score, change_score, data_score, sentiment_score, composite, rank
    Ordered by (run_at ASC, region, gics_sector).
    Returns empty DataFrame if no scans exist.
    """
    condition, params = _recent_scan_filter(n_scans)
    query = f"""
        SELECT sc.scan_id, sc.run_at, s.region, s.gics_sector,
               s.level_score, s.change_score, s.data_score, s.sentiment_score,
               s.composite, s.rank
        FROM scores s
        JOIN scans sc ON sc.scan_id = s.scan_id
        WHERE {condition}
        ORDER BY sc.run_at ASC, s.region, s.gics_sector
    """
    return pd.read_sql_query(query, conn, params=params)
```

- [ ] **Step 5: Run the DB-backed tests to verify the history queries still work**

Run: `python3 -m pytest tests/test_state_smoke.py tests/test_theme_state.py -v`
Expected: all PASS, including `test_get_scan_history_row_count` and
`test_get_scan_history_none_returns_all_scans`

- [ ] **Step 6: Rewrite the two insert functions' row-building blocks**

In `save_scan`, replace:

```python
            if not region_sector_signals.empty:
                signals_rows = [
                    (
                        scan_id,
                        row["region"],
                        row["gics_sector"],
                        row["signal_name"],
                        _to_float_or_none(row.get("raw_value")),
                        _to_float_or_none(row.get("z_value")),
                    )
                    for _, row in region_sector_signals.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO signals "
                    "(scan_id, region, gics_sector, signal_name, raw_value, z_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    signals_rows,
                )
```

With:

```python
            if not region_sector_signals.empty:
                signals_rows = _rows_from_df(
                    region_sector_signals, scan_id,
                    key_cols=["region", "gics_sector", "signal_name"],
                    float_cols=["raw_value", "z_value"],
                )
                cur.executemany(
                    "INSERT INTO signals "
                    "(scan_id, region, gics_sector, signal_name, raw_value, z_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    signals_rows,
                )
```

Then replace:

```python
            if not scores_df.empty:
                score_cols = [
                    "level_score",
                    "change_score",
                    "data_score",
                    "sentiment_score",
                    "composite",
                    "rank",
                ]
                scores_rows = [
                    (
                        scan_id,
                        row["region"],
                        row["gics_sector"],
                        *(_to_float_or_none(row.get(c)) for c in score_cols),
                    )
                    for _, row in scores_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO scores "
                    "(scan_id, region, gics_sector, level_score, change_score, "
                    "data_score, sentiment_score, composite, rank) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    scores_rows,
                )
```

With:

```python
            if not scores_df.empty:
                scores_rows = _rows_from_df(
                    scores_df, scan_id,
                    key_cols=["region", "gics_sector"],
                    float_cols=["level_score", "change_score", "data_score",
                                "sentiment_score", "composite", "rank"],
                )
                cur.executemany(
                    "INSERT INTO scores "
                    "(scan_id, region, gics_sector, level_score, change_score, "
                    "data_score, sentiment_score, composite, rank) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    scores_rows,
                )
```

Then replace:

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

With:

```python
            if sentiment_signals_df is not None and not sentiment_signals_df.empty:
                sent_rows = _rows_from_df(
                    sentiment_signals_df, scan_id,
                    key_cols=["region", "gics_sector", "signal_name"],
                    float_cols=["value"],
                    raw_cols=["text_value"],
                )
                cur.executemany(
                    "INSERT INTO sentiment_signals "
                    "(scan_id, region, gics_sector, signal_name, value, text_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    sent_rows,
                )
```

In `save_theme_scan`, replace:

```python
            if not scores_df.empty:
                rows = [
                    (scan_id, row["gics_sector"],
                     *(_to_float_or_none(row.get(c)) for c in score_cols))
                    for _, row in scores_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO theme_scores "
                    "(scan_id, theme, level_score, change_score, data_score, "
                    "sentiment_score, composite, rank) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
```

With:

```python
            if not scores_df.empty:
                rows = _rows_from_df(
                    scores_df, scan_id,
                    key_cols=["gics_sector"],
                    float_cols=score_cols,
                )
                cur.executemany(
                    "INSERT INTO theme_scores "
                    "(scan_id, theme, level_score, change_score, data_score, "
                    "sentiment_score, composite, rank) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
```

Then replace:

```python
            if not signals_df.empty:
                srows = [
                    (scan_id, row["gics_sector"], row["signal_name"],
                     _to_float_or_none(row.get("raw_value")),
                     _to_float_or_none(row.get("z_value")))
                    for _, row in signals_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO theme_signals "
                    "(scan_id, theme, signal_name, raw_value, z_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    srows,
                )
```

With:

```python
            if not signals_df.empty:
                srows = _rows_from_df(
                    signals_df, scan_id,
                    key_cols=["gics_sector", "signal_name"],
                    float_cols=["raw_value", "z_value"],
                )
                cur.executemany(
                    "INSERT INTO theme_signals "
                    "(scan_id, theme, signal_name, raw_value, z_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    srows,
                )
```

- [ ] **Step 7: Run the full DB-backed test suite**

Run: `python3 -m pytest tests/test_state_smoke.py tests/test_theme_state.py tests/test_state_wipe_guard.py -v`
Expected: all PASS — this exercises `save_scan`/`save_theme_scan` end-to-end
against a real test DB, confirming the inserted rows are byte-identical to
before

- [ ] **Step 8: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all pass, 0 failures

- [ ] **Step 9: Commit**

```bash
git add src/state.py
git commit -m "refactor: dedupe latest-scan queries, history filters, and insert row-building in state.py"
```

---

### Task 5: BACKLOG.md + full test suite + push + PR

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: all changes from Tasks 1-4
- Produces: PR against `main`

- [ ] **Step 1: Run the full test suite one more time**

Run: `python3 -m pytest -v`
Expected: all pass, 0 failures

- [ ] **Step 2: Update `BACKLOG.md`**

Delete the entire "Maintenance sweep" Queued section (all four bullets — the
whole sweep ships together). Add this entry at the top of `# Done`:

```markdown
- ~~Maintenance sweep~~ — deleted dead `src/data/stocktwits.py` +
  `tests/test_stocktwits.py` (superseded by symbol-based Trends sentiment);
  `_cache_is_fresh` (`src/data/prices.py`) now tolerates a 4-day gap so the
  day after a market holiday no longer triggers a spurious live re-fetch, and
  also checks that cached data covers a newly-requested longer `start` range
  (re-fetches instead of silently truncating); deduped three repeated
  patterns in `src/state.py` — a shared latest-scan query helper, a shared
  recent-scan-filter builder, and a shared DataFrame-to-rows insert helper —
  all pure refactors, same output/behavior, verified by the existing test
  suite. *(2026-07-12)*
```

- [ ] **Step 3: Commit the backlog update**

```bash
git add BACKLOG.md
git commit -m "chore: move maintenance sweep to Done in BACKLOG.md"
```

- [ ] **Step 4: Push and create the PR**

```bash
git push -u origin chore/maintenance-sweep
```

```bash
gh pr create --title "chore: maintenance sweep — dead code, cache fixes, state.py dedup" --body "$(cat <<'EOF'
## Summary

- **Deleted dead code**: `src/data/stocktwits.py` + its test — the multi-source sentiment engine was already fully removed; nothing else imported it
- **Holiday-aware cache freshness**: `_cache_is_fresh` now tolerates a 4-day gap (covers weekends + the day after a single-day market holiday) instead of requiring an exact last-trading-day match — no new dependency, no holiday calendar needed
- **Price cache respects requested `start`**: `_cache_is_fresh` now also checks the cached data's earliest date covers the requested range; if not, `fetch_prices` re-fetches live instead of silently returning a truncated cache
- **`state.py` dedup** (536 → smaller, same behavior): three shared helpers replace repeated patterns — `_latest_scan_query` (4 call sites), `_recent_scan_filter` (4 call sites), `_rows_from_df` (5 call sites across `save_scan`/`save_theme_scan`). Pure refactor: identical SQL semantics and output shape, same public signatures.

## Test plan

- [x] Full suite passes
- [x] New `prices.py` tests: freshness tolerance boundary (4 days fresh, 5 days stale), start-coverage (short cache + long request → re-fetch; short cache + covered request → cache hit)
- [x] `state.py` refactor verified via existing DB-backed tests (`test_state_smoke.py`, `test_theme_state.py`) — no new tests needed since behavior is unchanged

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
