"""Tests for health-column persistence and retrieval in src/state.py."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


HEALTH_COLUMNS = [
    "duration_s", "prices_total", "prices_cache", "prices_stooq",
    "prices_yfinance", "prices_failed", "sectors_expected",
    "sectors_produced", "finbert_scored", "finbert_total", "gdelt_articles",
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
