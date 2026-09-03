"""Pytest tests for the state / Postgres persistence module.

DANGER: the db_conn fixture's teardown runs `DELETE FROM` with no WHERE clause
against all four scan-scoped tables — sentiment_signals, signals, scores,
scans — it wipes EVERY row. It must therefore only ever connect to a
throwaway test database, never production.

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

from src.state import init_db, save_scan, load_last_scan, compute_deltas, get_scan_history, get_signals_for_scan


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
                    for table in (
                        "sentiment_signals", "signals", "scores", "scans",
                    ):
                        cur.execute(f"DELETE FROM {table}")
        finally:
            conn.close()


def _make_scan_data(sectors=None):
    """Return (signals_df, scores_df) for the given member list.

    Rows are written under THEME, the live cohort that readers default to.
    Writing "US" here would exercise the retired sector cohort, which the
    default readers deliberately exclude — every assertion would see 0 rows.
    """
    if sectors is None:
        sectors = ["Technology", "Financials", "Energy"]

    signals_df = pd.DataFrame(
        [
            {
                "region": "THEME",
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
            "region": ["THEME"] * len(sectors),
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
    scan_id = save_scan(db_conn, datetime.datetime.now(datetime.timezone.utc), signals_df, scores_df)
    assert isinstance(scan_id, int)
    assert scan_id > 0


@skipif_no_db
def test_load_last_scan_after_save(db_conn):
    """load_last_scan returns a DataFrame with the saved rows."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.now(datetime.timezone.utc), signals_df, scores_df)
    last = load_last_scan(db_conn)
    assert last is not None
    assert len(last) == 3
    assert "composite" in last.columns


@skipif_no_db
def test_load_last_scan_returns_most_recent(db_conn):
    """After two scans, load_last_scan returns the second scan's data."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.now(datetime.timezone.utc), signals_df, scores_df)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.9, 0.1, -0.5]
    scores_df2["rank"] = [1.0, 2.0, 3.0]
    save_scan(db_conn, datetime.datetime.now(datetime.timezone.utc), signals_df, scores_df2)

    last = load_last_scan(db_conn)
    assert last is not None
    composites = sorted(last["composite"].tolist(), reverse=True)
    assert abs(composites[0] - 0.9) < 1e-9


@skipif_no_db
def test_compute_deltas_columns(db_conn):
    """compute_deltas should produce delta_composite, delta_rank, emerging_flag."""
    signals_df, scores_df = _make_scan_data()
    save_scan(db_conn, datetime.datetime.now(datetime.timezone.utc), signals_df, scores_df)
    last = load_last_scan(db_conn)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.5, 0.1, -0.1]
    scores_df2["rank"] = [1.0, 3.0, 2.0]

    deltas = compute_deltas(scores_df2, last)
    assert "delta_composite" in deltas.columns
    assert "delta_rank" in deltas.columns
    assert "emerging_flag" in deltas.columns


@skipif_no_db
def test_init_db_indexes_exist_in_a_real_database(db_conn):
    """End-to-end proof, not just a source-scan: the `db_conn` fixture already
    calls the real init_db() against the throwaway test Postgres, so by the
    time this test body runs the indexes either exist or they don't — no
    mocking involved. Queries pg_indexes directly, the same system catalog
    used to confirm production had zero non-PK indexes on these tables
    2026-08-23."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename IN "
            "('signals', 'scores', 'sentiment_signals') "
            "ORDER BY tablename, indexname"
        )
        rows = cur.fetchall()
    by_table = {}
    for tablename, indexname, indexdef in rows:
        by_table.setdefault(tablename, []).append((indexname, indexdef))

    for table, expected_idx in [
        ("signals", "signals_scan_region_idx"),
        ("scores", "scores_scan_region_idx"),
        ("sentiment_signals", "sentiment_signals_scan_region_idx"),
    ]:
        names = [n for n, _ in by_table.get(table, [])]
        assert expected_idx in names, (
            f"{expected_idx} does not exist on {table} in a real database "
            f"-- found indexes: {names}"
        )
        indexdef = next(d for n, d in by_table[table] if n == expected_idx)
        assert "scan_id" in indexdef and "region" in indexdef, (
            f"{expected_idx} exists but does not cover (scan_id, region): {indexdef}"
        )


@skipif_no_db
def test_get_scan_history_row_count(db_conn):
    """get_scan_history returns n_sectors * n_scans rows."""
    signals_df, scores_df = _make_scan_data()
    # Distinct days: save_scan replaces same-day scans (see
    # test_save_scan_idempotent_same_day), so two now() saves would collapse
    # into one scan and this would count 3 rows instead of 6.
    save_scan(db_conn, datetime.datetime(2099, 3, 1, 10, 0, 0), signals_df, scores_df)

    scores_df2 = scores_df.copy()
    scores_df2["composite"] = [0.5, 0.1, -0.1]
    save_scan(db_conn, datetime.datetime(2099, 3, 2, 10, 0, 0), signals_df, scores_df2)

    history = get_scan_history(db_conn, n_scans=5)
    assert len(history) == 6


@skipif_no_db
def test_get_scan_history_includes_prices_asof(db_conn):
    """prices_asof rides along in get_scan_history's SELECT (2026-08-23) --
    a real end-to-end round trip, not just an SQL-text pin, since a real DB
    is the only thing that would catch the column silently missing from
    the actual result set (e.g. a typo'd alias)."""
    signals_df, scores_df = _make_scan_data()
    save_scan(
        db_conn, datetime.datetime(2099, 5, 1, 10, 0, 0), signals_df, scores_df,
        health={"prices_asof": "2099-04-30"},
    )
    history = get_scan_history(db_conn, n_scans=1)
    assert "prices_asof" in history.columns
    assert str(history["prices_asof"].iloc[0]) == "2099-04-30"


@skipif_no_db
def test_get_scan_history_none_returns_all_scans(db_conn):
    """n_scans=None returns every scan, not just a window."""
    signals_df, scores_df = _make_scan_data()
    # Distinct days — three now() saves would be replaced down to one scan.
    for day in (1, 2, 3):
        save_scan(db_conn, datetime.datetime(2099, 4, day, 10, 0, 0), signals_df, scores_df)
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


def test_sentiment_signals_ddl_includes_text_value():
    from src.state import _DDL_STATEMENTS
    ddl = " ".join(_DDL_STATEMENTS)
    assert "text_value" in ddl


@skipif_no_db
def test_get_signals_for_scan_returns_only_that_scan(db_conn):
    """get_signals_for_scan should return only signals for a specific scan_id."""
    signals_df, scores_df = _make_scan_data()

    # Save first scan on 2026-07-01
    id1 = save_scan(
        db_conn,
        datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        signals_df,
        scores_df
    )

    # Modify signals for second scan
    signals_df2 = signals_df.copy()
    signals_df2["z_value"] = [0.9, 0.85, 0.7]  # different z values

    # Save second scan on 2026-07-08
    id2 = save_scan(
        db_conn,
        datetime.datetime(2026, 7, 8, tzinfo=datetime.timezone.utc),
        signals_df2,
        scores_df
    )

    # Get signals for first scan — verify only scan 1's rows are returned
    out = get_signals_for_scan(db_conn, id1)
    assert len(out) == 3, "Scan 1 should have exactly 3 rows"
    # Derive expected values from the fixture data (not hard-coded literals)
    expected_z_values_1 = sorted(signals_df["z_value"].tolist())
    actual_z_values_1 = sorted(out["z_value"].tolist())
    assert actual_z_values_1 == expected_z_values_1, \
        f"Scan 1 z_values should match fixture (derived: {expected_z_values_1})"

    # Get signals for second scan — verify only scan 2's rows are returned
    out2 = get_signals_for_scan(db_conn, id2)
    assert len(out2) == 3, "Scan 2 should have exactly 3 rows"
    # Derive expected values from signals_df2 (not hard-coded literals)
    expected_z_values_2 = sorted(signals_df2["z_value"].tolist())
    actual_z_values_2 = sorted(out2["z_value"].tolist())
    assert actual_z_values_2 == expected_z_values_2, \
        f"Scan 2 z_values should match modified fixture (derived: {expected_z_values_2})"

    # Ensure they have the right columns
    expected_cols = {"region", "gics_sector", "signal_name", "raw_value", "z_value"}
    assert set(out.columns) == expected_cols
    assert set(out2.columns) == expected_cols


# ---------------------------------------------------------------------------
# Personal-alert helpers (stubbed _read_sql — no DB required)
# ---------------------------------------------------------------------------

def test_get_all_positions_returns_records(monkeypatch):
    import src.state as state
    monkeypatch.setattr(state, "_read_sql", lambda conn, q, p=None: pd.DataFrame([
        {"user_id": "u1", "item_type": "sector", "region": "US", "name": "Energy"},
        {"user_id": "u2", "item_type": "theme", "region": "", "name": "AI"},
    ]))
    out = state.get_all_positions(None)
    assert out == [
        {"user_id": "u1", "item_type": "sector", "region": "US", "name": "Energy"},
        {"user_id": "u2", "item_type": "theme", "region": "", "name": "AI"},
    ]


def test_get_all_positions_empty(monkeypatch):
    import src.state as state
    monkeypatch.setattr(state, "_read_sql", lambda conn, q, p=None: pd.DataFrame())
    assert state.get_all_positions(None) == []


def test_get_alert_prefs_returns_records(monkeypatch):
    import src.state as state
    monkeypatch.setattr(state, "_read_sql", lambda conn, q, p=None: pd.DataFrame([
        {"user_id": "u1", "ntfy_topic": "sm-abc", "enabled": True},
    ]))
    assert state.get_alert_prefs(None) == [
        {"user_id": "u1", "ntfy_topic": "sm-abc", "enabled": True},
    ]


def test_get_alert_prefs_empty(monkeypatch):
    import src.state as state
    monkeypatch.setattr(state, "_read_sql", lambda conn, q, p=None: pd.DataFrame())
    assert state.get_alert_prefs(None) == []


def test_get_alert_prefs_query_filters_enabled(monkeypatch):
    """The SQL must filter to enabled rows — the caller does not re-filter."""
    import src.state as state
    seen = {}
    def _capture(conn, q, p=None):
        seen["q"] = q
        return pd.DataFrame()
    monkeypatch.setattr(state, "_read_sql", _capture)
    state.get_alert_prefs(None)
    normalized = " ".join(seen["q"].lower().split())
    assert "where enabled = true" in normalized


@skipif_no_db
def test_same_day_rerun_leaves_one_set_of_theme_rows(db_conn):
    """Re-scanning the same date must replace, not duplicate, the theme rows."""
    from datetime import datetime
    from src.state import save_scan, THEME_REGION

    # "Space" is carried as an ordinary member rather than written by a
    # separate call: save_scan is the only persist path since the sector
    # cohort was retired (2026-08-05). Keep the list at three — the helper's
    # score columns come from 3-element literals, so a longer one goes ragged.
    signals_df, scores_df = _make_scan_data(
        ["Technology", "Financials", "Space"]
    )

    run_at = datetime(2026, 8, 1, 9, 0, 0)
    for _ in range(2):
        save_scan(db_conn, run_at, signals_df, scores_df)

    rows = get_scan_history(db_conn, n_scans=None, regions=(THEME_REGION,))
    # The point of the test is that a same-day re-run REPLACES: two passes
    # must not double any of them.
    assert len(rows) == len(scores_df), \
        f"expected one set of rows after re-run, got {len(rows)}"
    assert (rows["gics_sector"] == "Space").sum() == 1, "theme row duplicated"
    assert not rows["gics_sector"].duplicated().any(), "rows duplicated on re-run"


@skipif_no_db
def test_readers_exclude_retired_sector_rows(db_conn):
    """The retired US/EU sector rows were deliberately left in the database, so
    the default readers must keep filtering them out. Without the filter they
    would reappear in the leaderboard, charts and movers — retiring the cohort
    removed the writer, not the rows."""
    from src.state import THEME_REGION

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scans (run_at, config_hash) VALUES (%s, %s) RETURNING scan_id",
                ("2026-08-01T00:00:00", "test"),
            )
            scan_id = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO scores (scan_id, region, gics_sector, level_score, "
                "change_score, data_score, sentiment_score, composite, rank) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (scan_id, "US", "Technology", 1.0, 0.5, 0.8, None, 0.9, 1.0),
                    (scan_id, THEME_REGION, "Space", 1.0, 0.5, 0.8, None, 0.9, 1.0),
                ],
            )

    default = get_scan_history(db_conn, n_scans=None)
    assert set(default["region"]) == {THEME_REGION}, \
        "a retired sector row reached the default reader"

    every = get_scan_history(db_conn, n_scans=None, regions=None)
    assert set(every["region"]) == {"US", THEME_REGION}

    sectors_only = get_scan_history(db_conn, n_scans=None, regions=("US",))
    assert set(sectors_only["region"]) == {"US"}


@skipif_no_db
def test_theme_readers_exclude_sector_rows(db_conn):
    """Theme readers now read tables holding sector rows — a missing region
    filter would put sectors on the themes page."""
    from src.state import (get_theme_scan_history, get_theme_signals_for_latest_scan,
                           THEME_REGION)

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scans (run_at, config_hash) VALUES (%s, %s) RETURNING scan_id",
                ("2099-05-01T00:00:00", "test"),
            )
            scan_id = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO scores (scan_id, region, gics_sector, level_score, "
                "change_score, data_score, sentiment_score, composite, rank) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (scan_id, "US", "Technology", 1.0, 0.5, 0.8, None, 0.9, 1.0),
                    (scan_id, THEME_REGION, "Space", 1.0, 0.5, 0.8, None, 0.9, 1.0),
                ],
            )
            cur.executemany(
                "INSERT INTO signals (scan_id, region, gics_sector, signal_name, "
                "raw_value, z_value) VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (scan_id, "US", "Technology", "rs_ratio", 101.0, 1.0),
                    (scan_id, THEME_REGION, "Space", "rs_ratio", 102.0, 1.5),
                ],
            )

    hist = get_theme_scan_history(db_conn, n_scans=None)
    assert set(hist["region"]) == {THEME_REGION}
    assert set(hist["gics_sector"]) == {"Space"}

    sigs = get_theme_signals_for_latest_scan(db_conn)
    assert "theme" in sigs.columns, "the theme column contract was broken"
    assert set(sigs["theme"]) == {"Space"}


@skipif_no_db
def test_sentiment_signals_reader_excludes_retired_sector_rows(db_conn):
    """get_sentiment_signals_for_latest_scan defaults to DEFAULT_REGIONS — a
    missing region filter would put retired sector rows on the sentiment page,
    and this also exercises the shared _latest_scan_query ORDER BY against
    sentiment_signals for real."""
    from src.state import get_sentiment_signals_for_latest_scan, THEME_REGION

    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scans (run_at, config_hash) VALUES (%s, %s) RETURNING scan_id",
                ("2099-05-01T00:00:00", "test"),
            )
            scan_id = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO sentiment_signals (scan_id, region, gics_sector, "
                "signal_name, value, text_value) VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (scan_id, "US", "Technology", "news_sentiment", 0.5, "positive"),
                    (scan_id, THEME_REGION, "Space", "news_sentiment", 0.7, "positive"),
                ],
            )

    sigs = get_sentiment_signals_for_latest_scan(db_conn)
    assert set(sigs["region"]) == {THEME_REGION}
    assert set(sigs["gics_sector"]) == {"Space"}


@skipif_no_db
def test_save_scan_persists_finbert_sentiment_rows_end_to_end(db_conn):
    """The FinBERT sentiment frame scan.py builds must survive a real INSERT.

    `sentiment_signals.region` and `.gics_sector` are both NOT NULL. Between
    1ff80d8 (2026-08-05) and this fix, scan.py concatenated theme-keyed rows
    (`theme`) straight into an accumulator seeded with `region`/`gics_sector`
    columns, so both key columns arrived NaN. No unit test on the frame shape
    caught it and no scan ever reached this code (the FinBERT NameError fired
    first), so the failure would only have surfaced here, at the INSERT.

    Builds the frame through scan.py's own `_compute_finbert_sentiment` rather
    than hand-rolling one, so the test tracks whatever that function actually
    produces.
    """
    import pandas as pd
    from types import SimpleNamespace
    from unittest.mock import patch

    import scan
    from src.state import save_scan, get_sentiment_signals_for_latest_scan, THEME_REGION

    wide_df = pd.DataFrame(
        {"x": [1.0, 2.0]}, index=["THEME|Cybersecurity", "THEME|Clean Energy"]
    )
    themes_cfg = {
        "themes": {
            "Cybersecurity": {"ticker": "CIBR", "gdelt_keywords": ["cybersecurity"]},
            "Clean Energy": {"ticker": "ICLN", "gdelt_keywords": ["clean energy"]},
        }
    }
    finbert_scores = {
        "Cybersecurity": {"mean_polarity": 0.20, "count": 30,
                          "positive_pct": 0.5, "negative_pct": 0.2},
        "Clean Energy": {"mean_polarity": -0.10, "count": 25,
                         "positive_pct": 0.3, "negative_pct": 0.4},
    }

    with patch("src.data.news_sentiment.fetch_headlines",
               return_value={"Cybersecurity": ["a"] * 30, "Clean Energy": ["b"] * 25}), \
         patch("src.data.news_sentiment.score_headlines", return_value=finbert_scores), \
         patch("src.data.news_sentiment.zscore_polarity",
               return_value={"Cybersecurity": 1.0, "Clean Energy": -1.0}):
        _score, sent_df, health = scan._compute_finbert_sentiment(
            wide_df, themes_cfg, SimpleNamespace(no_finbert=False)
        )

    assert not sent_df.empty, "no sentiment rows built"
    assert health["finbert_scored"] == 2, "health metrics not recorded"

    scores_df = pd.DataFrame({
        "region": [THEME_REGION, THEME_REGION],
        "gics_sector": ["Cybersecurity", "Clean Energy"],
        "level_score": [0.1, 0.2], "change_score": [0.1, 0.2],
        "data_score": [0.1, 0.2], "sentiment_score": [1.0, -1.0],
        "composite": [0.3, 0.4], "rank": [1, 2],
    })
    signals_df = pd.DataFrame(
        columns=["region", "gics_sector", "signal_name", "raw_value", "z_value"]
    )

    # The assertion that matters: this INSERT raises on NULL region/gics_sector.
    save_scan(
        conn=db_conn,
        run_at=datetime.datetime(2099, 6, 1, tzinfo=datetime.timezone.utc),
        region_sector_signals=signals_df,
        scores_df=scores_df,
        sentiment_signals_df=sent_df,
    )

    stored = get_sentiment_signals_for_latest_scan(db_conn)
    assert not stored.empty, "sentiment rows did not reach the database"
    assert set(stored["region"]) == {THEME_REGION}
    assert set(stored["gics_sector"]) == {"Cybersecurity", "Clean Energy"}
    assert set(stored["signal_name"]) == {
        "news_polarity", "news_count", "news_positive_pct", "news_negative_pct",
    }
