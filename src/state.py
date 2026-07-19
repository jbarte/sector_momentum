"""
SQLite state management for the Sector Momentum Scanner.

Persists scan results (signals and scores) so each new scan can be compared
to the previous one. Backed by Supabase (Postgres) via psycopg2.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extensions
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS scans (
        scan_id     SERIAL PRIMARY KEY,
        run_at      TEXT NOT NULL,
        config_hash TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        region      TEXT NOT NULL,
        gics_sector TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        raw_value   REAL,
        z_value     REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scores (
        scan_id         INTEGER NOT NULL REFERENCES scans(scan_id),
        region          TEXT NOT NULL,
        gics_sector     TEXT NOT NULL,
        level_score     REAL,
        change_score    REAL,
        data_score      REAL,
        sentiment_score REAL,
        composite       REAL,
        rank            REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        region      TEXT NOT NULL,
        gics_sector TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        value       REAL,
        text_value  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_scores (
        scan_id      INTEGER NOT NULL REFERENCES scans(scan_id),
        theme        TEXT NOT NULL,
        level_score  REAL,
        change_score REAL,
        data_score   REAL,
        sentiment_score REAL,
        composite    REAL,
        rank         REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        theme       TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        raw_value   REAL,
        z_value     REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_sentiment_signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        theme       TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        value       REAL,
        text_value  TEXT
    )
    """,
]

# Every table with a scan_id FK on scans, deleted before a same-day scan is
# replaced. Must stay in sync with the DDL above (tests/test_state_schema.py
# asserts coverage).
_SCAN_CHILD_TABLES = (
    "theme_sentiment_signals",
    "theme_signals",
    "theme_scores",
    "sentiment_signals",
    "scores",
    "signals",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> psycopg2.extensions.connection:
    """
    Connect to Supabase/Postgres and create tables if they don't exist.
    Reads DATABASE_URL from the environment.
    Returns an open psycopg2 connection.
    """
    db_url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(db_url)
    with conn:
        with conn.cursor() as cur:
            for stmt in _DDL_STATEMENTS:
                cur.execute(stmt)
            cur.execute(
                "ALTER TABLE sentiment_signals "
                "ADD COLUMN IF NOT EXISTS text_value TEXT"
            )
    logger.info("Database initialised (Supabase/Postgres)")
    return conn


def _compute_config_hash(weights_path: str | Path) -> str:
    """Return SHA-256 hex digest of weights_path contents, or empty string if missing."""
    weights_path = Path(weights_path)
    if not weights_path.exists():
        logger.warning("weights_path %s not found; using empty hash", weights_path)
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(weights_path.read_bytes()).hexdigest()


def save_scan(
    conn: psycopg2.extensions.connection,
    run_at: datetime,
    region_sector_signals: pd.DataFrame,
    scores_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
    sentiment_signals_df: pd.DataFrame | None = None,
) -> int:
    """
    Insert a new scan row and all its signals/scores.
    Returns the new scan_id.
    Uses a transaction (all-or-nothing).
    """
    config_hash = _compute_config_hash(weights_path)
    run_at_str = run_at.isoformat()
    run_date_prefix = run_at.strftime("%Y-%m-%d")

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scan_id FROM scans WHERE run_at LIKE %s",
                (run_date_prefix + "%",),
            )
            dup_ids = [r[0] for r in cur.fetchall()]
            if dup_ids:
                logger.info(
                    "Replacing %d existing scan(s) for %s (idempotent re-run)",
                    len(dup_ids), run_date_prefix,
                )
                placeholders = ",".join(["%s"] * len(dup_ids))
                for child in _SCAN_CHILD_TABLES:
                    cur.execute(
                        f"DELETE FROM {child} WHERE scan_id IN ({placeholders})",
                        dup_ids,
                    )
                cur.execute(
                    f"DELETE FROM scans WHERE scan_id IN ({placeholders})",
                    dup_ids,
                )

            cur.execute(
                "INSERT INTO scans (run_at, config_hash) VALUES (%s, %s) RETURNING scan_id",
                (run_at_str, config_hash),
            )
            scan_id = cur.fetchone()[0]

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

    logger.info("Saved scan_id=%d at %s", scan_id, run_at_str)
    return scan_id


def load_last_scan(
    conn: psycopg2.extensions.connection,
) -> pd.DataFrame | None:
    """
    Load the scores for the most recent scan.
    Returns a DataFrame with columns:
        region, gics_sector, composite, rank, scan_id
    Returns None if no prior scan exists.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT 1")
        row = cur.fetchone()
    if row is None:
        return None

    scan_id = row[0]
    df = pd.read_sql_query(
        "SELECT region, gics_sector, composite, rank, scan_id "
        "FROM scores WHERE scan_id = %s",
        conn,
        params=(scan_id,),
    )
    return df if not df.empty else None


def compute_deltas(
    current_scores: pd.DataFrame,
    prior_scores: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Join current scores to prior scores and compute:
        delta_composite = current.composite - prior.composite
        delta_rank      = prior.rank - current.rank  (positive = climbing)
        emerging_flag   = (delta_rank > 0) AND (delta_composite > 0)

    If prior_scores is None all delta columns are zero/False.
    Returns current_scores with the three new columns appended.
    """
    result = current_scores.copy()

    if prior_scores is None or prior_scores.empty:
        result["delta_composite"] = 0.0
        result["delta_rank"] = 0.0
        result["emerging_flag"] = False
        return result

    prior_slim = prior_scores[["region", "gics_sector", "composite", "rank"]].rename(
        columns={"composite": "_prior_composite", "rank": "_prior_rank"}
    )

    result = result.merge(prior_slim, on=["region", "gics_sector"], how="left")
    result["delta_composite"] = result["composite"] - result["_prior_composite"].fillna(
        result["composite"]
    )
    result["delta_rank"] = result["_prior_rank"].fillna(result["rank"]) - result["rank"]
    result["emerging_flag"] = (result["delta_rank"] > 0) & (result["delta_composite"] > 0)

    result = result.drop(columns=["_prior_composite", "_prior_rank"])
    return result


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


def save_theme_scan(
    conn: psycopg2.extensions.connection,
    scan_id: int,
    scores_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    sentiment_signals_df: pd.DataFrame | None = None,
) -> None:
    """Insert theme scores/signals for an existing scan_id (theme = gics_sector).

    scores_df columns: region, gics_sector, level_score, change_score, data_score,
    sentiment_score, composite, rank (region is "THEME"; gics_sector is the theme
    name). signals_df columns: region, gics_sector, signal_name, raw_value, z_value.
    sentiment_signals_df (optional) columns: theme, signal_name, value, text_value
    — the info-only derived Trends signals for the theme cohort.
    """
    score_cols = ["level_score", "change_score", "data_score",
                  "sentiment_score", "composite", "rank"]
    with conn:
        with conn.cursor() as cur:
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
            if sentiment_signals_df is not None and not sentiment_signals_df.empty:
                sent_rows = _rows_from_df(
                    sentiment_signals_df, scan_id,
                    key_cols=["theme", "signal_name"],
                    float_cols=["value"],
                    raw_cols=["text_value"],
                )
                cur.executemany(
                    "INSERT INTO theme_sentiment_signals "
                    "(scan_id, theme, signal_name, value, text_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    sent_rows,
                )
    logger.info("Saved %d theme scores for scan_id=%d", len(scores_df), scan_id)


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


def get_theme_sentiment_signals_for_latest_scan(
    conn: psycopg2.extensions.connection,
) -> pd.DataFrame:
    """Derived Trends sentiment rows for the theme cohort, most recent scan.

    Aliased ``'THEME' AS region, theme AS gics_sector`` so the shared
    ``_build_sentiment_signal_rows`` dashboard builder consumes it unchanged.
    Columns: region, gics_sector, signal_name, value, text_value. Empty DataFrame
    if no theme sentiment rows exist.
    """
    return _latest_scan_query(
        conn, "theme_sentiment_signals",
        "'THEME' AS region, t.theme AS gics_sector, t.signal_name, t.value, t.text_value",
    )


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float_or_none(value) -> float | None:
    """Convert NaN / None to None so Postgres stores NULL, otherwise float."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


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
    through as-is but with missing values (None / NaN) normalized to None so
    Postgres stores NULL — covers columns like sentiment_signals.text_value that
    carry real text on some rows and NaN on others after a mixed-column concat."""
    raw_cols = raw_cols or []

    def _raw(v):
        # NaN (from mixed-dtype columns) and falsy values → SQL NULL; keep text.
        if v is None or (isinstance(v, float) and math.isnan(v)) or not v:
            return None
        return v

    return [
        (scan_id, *(row[k] for k in key_cols),
         *(_to_float_or_none(row.get(c)) for c in float_cols),
         *(_raw(row.get(c)) for c in raw_cols))
        for _, row in df.iterrows()
    ]
