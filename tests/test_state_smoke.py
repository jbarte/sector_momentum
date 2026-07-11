"""Pytest tests for the state / Postgres persistence module.

DANGER: the db_conn fixture's teardown runs `DELETE FROM signals/scores/scans`
with no WHERE clause — it wipes EVERY row. It must therefore only ever connect
to a throwaway test database, never production.

These tests are gated on a dedicated TEST_DATABASE_URL env var (NOT the
production DATABASE_URL). If TEST_DATABASE_URL is unset they skip, so a normal
`pytest` run can never wipe the live Supabase project. To run them, point
TEST_DATABASE_URL at a disposable Postgres/Supabase database.

Safety is identity-based, not string-based: the guard (`_same_database`) resolves
each URL to its Supabase project ref (or host) + dbname, so pointing
TEST_DATABASE_URL at production via a different URL form (pooler `:6543` vs direct
`:5432`) is still caught and skipped. As a final backstop, the fixture teardown
calls `_assert_disposable`, which re-checks the LIVE connection and refuses to
DELETE if it resolves to production. (A prior string-only guard let exactly this
slip through and the production DB was wiped on 2026-06-25.)
"""
import datetime
import os
from urllib.parse import urlparse

import pandas as pd
import pytest

from src.state import init_db, save_scan, load_last_scan, compute_deltas, get_scan_history


# ---------------------------------------------------------------------------
# Safety guard — identify whether two Postgres URLs target the SAME database
# ---------------------------------------------------------------------------
# A plain string comparison is NOT enough: the same Supabase project is reachable
# via the pooler (`postgres.<ref>@...pooler...:6543`) and the direct connection
# (`postgres@db.<ref>.supabase.co:5432`). Those strings differ but the data is
# identical, so a string-only guard would let a prod-equivalent TEST_DATABASE_URL
# through and the teardown would wipe production. We compare a resolved identity
# (Supabase project ref when available, else host) plus the database name.

def _db_identity(url: str) -> tuple[str, str]:
    """Best-effort (project_ref_or_host, dbname) identity for a Postgres URL."""
    if not url:
        return ("", "")
    p = urlparse(url)
    host = (p.hostname or "").lower()
    dbname = (p.path or "").strip("/").lower()
    user = p.username or ""
    ref = ""
    if "." in user and user.split(".", 1)[0] == "postgres":
        # Supabase pooler: username is "postgres.<project_ref>"
        ref = user.split(".", 1)[1]
    elif host.endswith(".supabase.co") and host.startswith("db."):
        # Supabase direct: host is "db.<project_ref>.supabase.co"
        parts = host.split(".")
        if len(parts) >= 2:
            ref = parts[1]
    return (ref or host, dbname)


def _same_database(a: str, b: str) -> bool:
    """True if both URLs target the same database. Fails SAFE: if either identity
    can't be determined, assume they are the same (so we refuse to wipe)."""
    ia, ib = _db_identity(a), _db_identity(b)
    if not ia[0] or not ib[0]:
        return True
    return ia == ib


def _assert_disposable(conn, prod_url: str) -> None:
    """Defense in depth: refuse to wipe if the LIVE connection resolves to the
    production database. Re-checks the actual connection params (not just env),
    so it holds even if the skip guard is bypassed or a URL form differs."""
    p = conn.get_dsn_parameters()
    target = (
        f"postgresql://{p.get('user', '')}@{p.get('host', '')}:"
        f"{p.get('port', '')}/{p.get('dbname', '')}"
    )
    if _same_database(target, prod_url):
        raise RuntimeError(
            "Refusing to DELETE rows: the test connection resolves to the "
            "production database. Point TEST_DATABASE_URL at a disposable DB."
        )


_test_db = os.environ.get("TEST_DATABASE_URL")
_prod_db = os.environ.get("DATABASE_URL", "")
# Skip unless a dedicated test DB is configured AND it is a different database
# from production (identity-aware, not string-aware — see _same_database).
skipif_no_db = pytest.mark.skipif(
    not _test_db or _same_database(_test_db, _prod_db),
    reason="TEST_DATABASE_URL not set (or resolves to the same database as "
           "DATABASE_URL); these tests wipe all rows and must never run "
           "against production",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(monkeypatch):
    """Open a connection to the dedicated TEST database and wipe its rows after
    each test. Never touches the production DATABASE_URL (see module docstring)."""
    # Capture the real production URL BEFORE monkeypatching it away — the
    # teardown's defense-in-depth check compares against this.
    prod_url = os.environ.get("DATABASE_URL", "")
    test_url = os.environ["TEST_DATABASE_URL"]
    if _same_database(test_url, prod_url):
        pytest.skip("TEST_DATABASE_URL resolves to the production database")
    # init_db() reads DATABASE_URL; point it at the test DB for this fixture only.
    monkeypatch.setenv("DATABASE_URL", test_url)
    conn = init_db()
    try:
        yield conn
    finally:
        try:
            # Last line of defense: never DELETE if the live connection is production.
            _assert_disposable(conn, prod_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM signals")
                    cur.execute("DELETE FROM scores")
                    cur.execute("DELETE FROM scans")
        finally:
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


@skipif_no_db
def test_get_scan_history_none_returns_all_scans(db_conn):
    """n_scans=None returns every scan, not just a window."""
    signals_df, scores_df = _make_scan_data()
    for _ in range(3):
        save_scan(db_conn, datetime.datetime.utcnow(), signals_df, scores_df)
    all_rows = get_scan_history(db_conn, n_scans=None)
    assert all_rows["scan_id"].nunique() == 3


@skipif_no_db
def test_save_scan_idempotent_same_day(db_conn):
    """A second save_scan on the same UTC day replaces the first scan."""
    signals_df, scores_df = _make_scan_data()
    run_at = datetime.datetime(2099, 1, 15, 10, 0, 0)

    id1 = save_scan(db_conn, run_at, signals_df, scores_df)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.99, 0.88, 0.77]
    run_at2 = datetime.datetime(2099, 1, 15, 14, 30, 0)
    id2 = save_scan(db_conn, run_at2, signals_df, scores_df2)

    assert id2 != id1

    with db_conn.cursor() as cur:
        cur.execute("SELECT scan_id FROM scans WHERE run_at LIKE '2099-01-15%%'")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == id2

    last = load_last_scan(db_conn)
    assert last is not None
    assert abs(last["composite"].max() - 0.99) < 1e-9


@skipif_no_db
def test_save_scan_different_days_not_replaced(db_conn):
    """Scans on different UTC days are NOT replaced — both survive."""
    signals_df, scores_df = _make_scan_data()
    id1 = save_scan(db_conn, datetime.datetime(2099, 2, 1, 10, 0), signals_df, scores_df)
    id2 = save_scan(db_conn, datetime.datetime(2099, 2, 2, 10, 0), signals_df, scores_df)

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM scans WHERE run_at LIKE '2099-02-%%'")
        count = cur.fetchone()[0]
    assert count == 2
