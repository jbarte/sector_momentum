"""Pytest tests for the state / Postgres persistence module.

DANGER: the db_conn fixture's teardown runs `DELETE FROM signals/scores/scans`
with no WHERE clause — it wipes EVERY row. It must therefore only ever connect
to a throwaway test database, never production.

These tests are gated on a dedicated TEST_DATABASE_URL env var (NOT the
production DATABASE_URL). If TEST_DATABASE_URL is unset they skip, so a normal
`pytest` run can never wipe the live Supabase project. To run them, point
TEST_DATABASE_URL at a disposable Postgres/Supabase database.
"""
import datetime
import os

import pandas as pd
import pytest

from src.state import init_db, save_scan, load_last_scan, compute_deltas, get_scan_history

_test_db = os.environ.get("TEST_DATABASE_URL")
_prod_db = os.environ.get("DATABASE_URL")
# Skip unless a dedicated test DB is configured. Also refuse to run if it points
# at the same place as production — these tests delete all rows.
skipif_no_db = pytest.mark.skipif(
    not _test_db or _test_db == _prod_db,
    reason="TEST_DATABASE_URL not set (or equals DATABASE_URL); "
           "these tests wipe all rows and must never run against production",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(monkeypatch):
    """Open a connection to the dedicated TEST database and wipe its rows after
    each test. Never touches the production DATABASE_URL (see module docstring)."""
    # init_db() reads DATABASE_URL; point it at the test DB for this fixture only.
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    conn = init_db()
    yield conn
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM signals")
            cur.execute("DELETE FROM scores")
            cur.execute("DELETE FROM scans")
    conn.close()


def _make_scan_data(sectors=None):
    """Return (signals_df, scores_df) for the given sector list."""
    if sectors is None:
        sectors = ["Technology", "Financials", "Energy"]

    signals_df = pd.DataFrame(
        [
            {
                "region": "US",
                "gics_sector": s,
                "signal_name": "rs_ratio",
                "raw_value": float(i),
                "z_value": float(i - 1),
            }
            for i, s in enumerate(sectors)
        ]
    )

    scores_df = pd.DataFrame(
        {
            "region": ["US"] * len(sectors),
            "gics_sector": sectors,
            "level_score": [0.5, 0.3, -0.2][: len(sectors)],
            "change_score": [0.4, 0.1, -0.3][: len(sectors)],
            "data_score": [0.45, 0.2, -0.25][: len(sectors)],
            "sentiment_score": [float("nan")] * len(sectors),
            "composite": [0.45, 0.2, -0.25][: len(sectors)],
            "rank": [float(i + 1) for i in range(len(sectors))],
        }
    )

    return signals_df, scores_df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skipif_no_db
def test_save_scan_returns_positive_int(db_conn):
    """save_scan should return a positive integer scan_id."""
    signals_df, scores_df = _make_scan_data()
    scan_id = save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)
    assert isinstance(scan_id, int)
    assert scan_id > 0


@skipif_no_db
def test_load_last_scan_after_save(db_conn):
    """load_last_scan returns a DataFrame with the saved rows."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)
    last = load_last_scan(db_conn)
    assert last is not None
    assert len(last) == 3
    assert "composite" in last.columns


@skipif_no_db
def test_load_last_scan_returns_most_recent(db_conn):
    """After two scans, load_last_scan returns the second scan's data."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.9, 0.1, -0.5]
    scores_df2["rank"] = [1.0, 2.0, 3.0]
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df2)

    last = load_last_scan(db_conn)
    assert last is not None
    composites = sorted(last["composite"].tolist(), reverse=True)
    assert abs(composites[0] - 0.9) < 1e-9


@skipif_no_db
def test_compute_deltas_columns(db_conn):
    """compute_deltas should produce delta_composite, delta_rank, emerging_flag."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)
    last = load_last_scan(db_conn)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.5, 0.1, -0.1]
    scores_df2["rank"] = [1.0, 3.0, 2.0]

    deltas = compute_deltas(scores_df2, last)
    assert "delta_composite" in deltas.columns
    assert "delta_rank" in deltas.columns
    assert "emerging_flag" in deltas.columns


@skipif_no_db
def test_get_scan_history_row_count(db_conn):
    """get_scan_history returns n_sectors * n_scans rows."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.5, 0.1, -0.1]
    save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df2)

    history = get_scan_history(db_conn, n_scans=5)
    assert len(history) == 6
