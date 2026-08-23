"""Tests for health-column persistence and retrieval in src/state.py."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


HEALTH_COLUMNS = [
    "duration_s", "prices_total", "prices_cache", "prices_stooq",
    "prices_yfinance", "prices_failed", "sectors_expected",
    "sectors_produced", "finbert_scored", "finbert_total", "gdelt_articles",
    "prices_asof", "asof_spread_days", "asof_dropped_count",
]


def _mock_conn_and_cursor():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


def test_init_db_adds_health_columns():
    """init_db should execute ALTER TABLE for each health column."""
    from src.state import init_db

    with patch("src.state.os.environ", {"DATABASE_URL": "fake"}), \
         patch("src.state.psycopg2.connect") as mock_connect:
        conn, cur = _mock_conn_and_cursor()
        mock_connect.return_value = conn

        init_db()

        executed_sql = " ".join(
            str(call) for call in cur.execute.call_args_list
        )
        for col in HEALTH_COLUMNS:
            assert col in executed_sql, f"Missing ALTER TABLE for {col}"


def test_init_db_creates_indexes_on_scan_scoped_tables():
    """init_db should CREATE INDEX IF NOT EXISTS on (scan_id, region) for each
    of the three tables every scan-scoped read/write actually touches.

    Confirmed live against production 2026-08-23: signals/scores/
    sentiment_signals had zero non-PK indexes despite every read filtering
    on scan_id (and often region), and the same-day-replace DELETE filtering
    on scan_id alone.
    """
    from src.state import init_db

    with patch("src.state.os.environ", {"DATABASE_URL": "fake"}), \
         patch("src.state.psycopg2.connect") as mock_connect:
        conn, cur = _mock_conn_and_cursor()
        mock_connect.return_value = conn

        init_db()

        executed_sql = " ".join(
            str(call) for call in cur.execute.call_args_list
        )
        for table in ("signals", "scores", "sentiment_signals"):
            assert f"CREATE INDEX IF NOT EXISTS" in executed_sql, (
                "init_db never issues a CREATE INDEX IF NOT EXISTS statement"
            )
            assert f"ON {table} (scan_id, region)" in executed_sql, (
                f"init_db does not index {table} on (scan_id, region)"
            )


def test_init_db_index_creation_uses_if_not_exists():
    """Every CREATE INDEX must be idempotent (IF NOT EXISTS) — init_db runs
    on every scan (production) and every test-suite DB-backed test, so a
    plain CREATE INDEX would fail on the second call."""
    from src.state import init_db

    with patch("src.state.os.environ", {"DATABASE_URL": "fake"}), \
         patch("src.state.psycopg2.connect") as mock_connect:
        conn, cur = _mock_conn_and_cursor()
        mock_connect.return_value = conn

        init_db()

        for call in cur.execute.call_args_list:
            sql = str(call)
            if "CREATE INDEX" in sql:
                assert "IF NOT EXISTS" in sql, (
                    f"non-idempotent CREATE INDEX would fail on init_db's "
                    f"next call: {sql}"
                )


def test_save_scan_includes_health_columns():
    """save_scan with health dict should INSERT health values."""
    from datetime import datetime, timezone
    from src.state import save_scan

    conn, cur = _mock_conn_and_cursor()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (42,)

    health = {
        "duration_s": 35.2,
        "prices_total": 27,
        "prices_cache": 20,
        "prices_stooq": 5,
        "prices_yfinance": 2,
        "prices_failed": 0,
        "sectors_expected": 25,
        "sectors_produced": 25,
        "finbert_scored": 11,
        "finbert_total": 11,
        "gdelt_articles": 847,
        "prices_asof": "2026-07-19",
        "asof_spread_days": 0,
    }

    result = save_scan(
        conn=conn,
        run_at=datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc),
        region_sector_signals=pd.DataFrame(),
        scores_df=pd.DataFrame(),
        health=health,
    )

    assert result == 42
    insert_calls = [
        c for c in cur.execute.call_args_list
        if "INSERT INTO scans" in str(c)
    ]
    assert len(insert_calls) == 1
    sql = str(insert_calls[0])
    assert "duration_s" in sql
    assert "prices_total" in sql
    assert "gdelt_articles" in sql
    assert "prices_asof" in sql
    assert "asof_spread_days" in sql


def test_save_scan_works_without_health():
    """save_scan without health= still works (backward compat)."""
    from datetime import datetime, timezone
    from src.state import save_scan

    conn, cur = _mock_conn_and_cursor()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (1,)

    result = save_scan(
        conn=conn,
        run_at=datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc),
        region_sector_signals=pd.DataFrame(),
        scores_df=pd.DataFrame(),
    )

    assert result == 1
    insert_calls = [
        c for c in cur.execute.call_args_list
        if "INSERT INTO scans" in str(c)
    ]
    sql = str(insert_calls[0])
    assert "duration_s" not in sql


def test_get_latest_health_returns_dict():
    """get_latest_health returns a dict with all health keys."""
    from src.state import get_latest_health

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame([{
            "run_at": "2026-07-20T06:00:00+00:00",
            "duration_s": 35.2,
            "prices_total": 27,
            "prices_cache": 20,
            "prices_stooq": 5,
            "prices_yfinance": 2,
            "prices_failed": 0,
            "sectors_expected": 25,
            "sectors_produced": 25,
            "finbert_scored": 11,
            "finbert_total": 11,
            "gdelt_articles": 847,
        }])

        result = get_latest_health(conn)

    assert result is not None
    assert result["duration_s"] == 35.2
    assert result["prices_total"] == 27
    assert result["finbert_scored"] == 11


def test_get_health_for_scan_selects_a_specific_scan():
    """Mirrors get_sentiment_signals_for_scan / get_signals_for_scan: the
    gated build needs the health of the scan a GUEST ACTUALLY SEES (the
    lagged scan_id), not the true latest one. Code review, 2026-08-23: this
    function did not exist, so dashboard/build.py had no way to ask for it --
    `health_row = get_latest_health(conn)` ran unconditionally and was never
    touched by the lag-gating block below it, leaking the true latest scan's
    run_at (and, after this branch, prices_asof/asof_spread_days) to every
    guest regardless of the 7-day lag. Same class of leak the sentiment_signals_df
    re-fetch two lines below it in build.py already exists to prevent.
    """
    from src.state import get_health_for_scan

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame([{
            "run_at": "2026-08-06T06:00:00+00:00",
            "prices_asof": "2026-08-06", "asof_spread_days": 0,
        }])
        result = get_health_for_scan(conn, 162)

    assert result["run_at"] == "2026-08-06T06:00:00+00:00"
    call_args = mock_read.call_args
    sql = str(call_args)
    assert "WHERE" in sql and "scan_id" in sql, (
        "get_health_for_scan does not scope its query to a specific scan_id"
    )
    # _read_sql(conn, query, params) — params is a tuple, so 162 lives INSIDE
    # the third positional arg, not as a bare positional arg itself.
    all_args = call_args.args + tuple(call_args.kwargs.values())
    flattened = [v for a in all_args if isinstance(a, tuple) for v in a] + list(all_args)
    assert 162 in flattened, (
        "get_health_for_scan does not pass scan_id as a query parameter"
    )


def test_get_health_for_scan_returns_none_for_a_scan_with_no_health_row():
    from src.state import get_health_for_scan

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame()
        result = get_health_for_scan(conn, 1)

    assert result is None


def test_get_health_for_scan_shares_the_same_column_set_as_get_latest_health():
    """The two functions must request the SAME columns, or the gated and
    ungated health panels would silently show different information --
    exactly the kind of drift a hand-duplicated column list invites (code
    review flagged the three-way duplication across init_db/save_scan/
    get_latest_health; this pins the fourth call site to the same source)."""
    from src.state import get_health_for_scan

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame()
        get_health_for_scan(conn, 1)

    sql = str(mock_read.call_args)
    for col in HEALTH_COLUMNS:
        assert col in sql, f"get_health_for_scan's SELECT omits {col}"


def test_prices_asof_and_asof_spread_days_round_trip():
    """`align_cohort_asof` (src/data/prices.py) computes both values into
    `stats_out`, but scan.py never carried them into the persisted `_health`
    dict -- so "which date was this snapshot actually scored on?" was
    answerable only by reading `align_cohort_asof: ... scoring as-of ...`
    out of a scan's log, or by hand-diffing the price cache (how the
    2026-08-22 weekend-staleness bug was actually found). Pins the full
    write -> read round trip for both new columns with real values, not just
    that the column names appear somewhere in the SQL text.
    """
    from datetime import datetime, timezone
    from src.state import save_scan

    conn, cur = _mock_conn_and_cursor()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (7,)

    save_scan(
        conn=conn,
        run_at=datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc),
        region_sector_signals=pd.DataFrame(),
        scores_df=pd.DataFrame(),
        health={"prices_asof": "2026-08-06", "asof_spread_days": 2},
    )

    insert_calls = [
        c for c in cur.execute.call_args_list
        if "INSERT INTO scans" in str(c)
    ]
    assert len(insert_calls) == 1
    # The values, not just the column names, must reach the INSERT — a
    # column-name-only check would pass even if the dict lookup silently
    # pulled from the wrong key.
    call_args = insert_calls[0].args
    vals = call_args[1] if len(call_args) > 1 else insert_calls[0][0][1]
    assert "2026-08-06" in vals
    assert 2 in vals


def test_get_latest_health_selects_prices_asof_and_spread_from_the_db():
    """The tests above mock `_read_sql` to return an already-complete
    DataFrame, so they cannot catch a column dropped from the SQL query
    itself -- a real DB simply would not return a key that was never
    requested, and `row = df.iloc[0].to_dict()` would then be missing it
    silently (Undefined in the template, not a crash). Confirmed by sabotage:
    removing the two columns from get_latest_health's own `_health_cols`
    left every other test in this file green. This test reads the actual
    SQL text `_read_sql` was called with, the same way
    test_init_db_adds_health_columns checks init_db's executed SQL.
    """
    from src.state import get_latest_health
    import pandas as pd

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame()  # empty is fine; only the query text matters here
        get_latest_health(conn)

    sql = str(mock_read.call_args)
    assert "prices_asof" in sql, "get_latest_health's SELECT does not request prices_asof"
    assert "asof_spread_days" in sql, "get_latest_health's SELECT does not request asof_spread_days"


def test_get_latest_health_reads_prices_asof_and_spread():
    from src.state import get_latest_health

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame([{
            "run_at": "2026-08-09T06:00:00+00:00",
            "prices_asof": "2026-08-06",
            "asof_spread_days": 2,
        }])
        result = get_latest_health(conn)

    assert result["prices_asof"] == "2026-08-06"
    assert result["asof_spread_days"] == 2


def test_get_latest_health_returns_none_when_no_scans():
    """get_latest_health returns None when the scans table is empty."""
    from src.state import get_latest_health

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame()

        result = get_latest_health(conn)

    assert result is None


def test_get_latest_health_converts_nan_to_none():
    """Old scans with NaN health columns should return None values."""
    from src.state import get_latest_health
    import math

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        row = {"run_at": "2026-07-01T06:00:00+00:00"}
        for col in HEALTH_COLUMNS:
            row[col] = float("nan")
        mock_read.return_value = pd.DataFrame([row])

        result = get_latest_health(conn)

    assert result is not None
    for col in HEALTH_COLUMNS:
        assert result[col] is None, f"{col} should be None, got {result[col]}"


def test_asof_dropped_count_round_trip():
    """`align_cohort_asof` (src/data/prices.py) already computes `asof_dropped`
    (the list of tickers it dropped for lagging the cohort) into `stats_out`,
    but scan.py never carried the COUNT into the persisted `_health` dict --
    so up to 3 of 18 themes could silently vanish from a scan (the coverage
    guard only aborts below 80% coverage) with nothing on the health panel
    saying so. Pins the write -> read round trip with a real value, not just
    that the column name appears somewhere in the SQL text -- mirrors
    test_prices_asof_and_asof_spread_days_round_trip above.
    """
    from datetime import datetime, timezone
    from src.state import save_scan

    conn, cur = _mock_conn_and_cursor()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (7,)

    save_scan(
        conn=conn,
        run_at=datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc),
        region_sector_signals=pd.DataFrame(),
        scores_df=pd.DataFrame(),
        health={"asof_dropped_count": 2},
    )

    insert_calls = [
        c for c in cur.execute.call_args_list
        if "INSERT INTO scans" in str(c)
    ]
    assert len(insert_calls) == 1
    call_args = insert_calls[0].args
    vals = call_args[1] if len(call_args) > 1 else insert_calls[0][0][1]
    assert 2 in vals


def test_get_latest_health_selects_asof_dropped_count_from_the_db():
    """Reads the actual SQL text, the same way the prices_asof/asof_spread_days
    equivalent above does -- a DataFrame-only check would pass even with the
    column dropped from get_latest_health's own SELECT."""
    from src.state import get_latest_health

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame()
        get_latest_health(conn)

    sql = str(mock_read.call_args)
    assert "asof_dropped_count" in sql, (
        "get_latest_health's SELECT does not request asof_dropped_count"
    )


def test_get_latest_health_reads_asof_dropped_count():
    from src.state import get_latest_health

    conn = MagicMock()
    with patch("src.state._read_sql") as mock_read:
        mock_read.return_value = pd.DataFrame([{
            "run_at": "2026-08-09T06:00:00+00:00",
            "asof_dropped_count": 3,
        }])
        result = get_latest_health(conn)

    assert result["asof_dropped_count"] == 3
