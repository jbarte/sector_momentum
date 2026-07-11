"""
SQLite state management for the Sector Momentum Scanner.

Persists scan results (signals and scores) so each new scan can be compared
to the previous one. Backed by Supabase (Postgres) via psycopg2.
"""

from __future__ import annotations

import hashlib
import logging
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
        value       REAL
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
]


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
                for child in (
                    "theme_signals", "theme_scores", "sentiment_signals",
                    "scores", "signals",
                ):
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

            if sentiment_signals_df is not None and not sentiment_signals_df.empty:
                sent_rows = [
                    (
                        scan_id,
                        row["region"],
                        row["gics_sector"],
                        row["signal_name"],
                        _to_float_or_none(row.get("value")),
                    )
                    for _, row in sentiment_signals_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO sentiment_signals "
                    "(scan_id, region, gics_sector, signal_name, value) "
                    "VALUES (%s, %s, %s, %s, %s)",
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
        SELECT ss.region, ss.gics_sector, ss.signal_name, ss.value
        FROM sentiment_signals ss
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON ss.scan_id = m.max_id
        """,
        conn,
    )


def save_theme_scan(
    conn: psycopg2.extensions.connection,
    scan_id: int,
    scores_df: pd.DataFrame,
    signals_df: pd.DataFrame,
) -> None:
    """Insert theme scores/signals for an existing scan_id (theme = gics_sector).

    scores_df columns: region, gics_sector, level_score, change_score, data_score,
    sentiment_score, composite, rank (region is "THEME"; gics_sector is the theme
    name). signals_df columns: region, gics_sector, signal_name, raw_value, z_value.
    """
    score_cols = ["level_score", "change_score", "data_score",
                  "sentiment_score", "composite", "rank"]
    with conn:
        with conn.cursor() as cur:
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
    logger.info("Saved %d theme scores for scan_id=%d", len(scores_df), scan_id)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float_or_none(value) -> float | None:
    """Convert NaN / None to None so Postgres stores NULL, otherwise float."""
    if value is None:
        return None
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None
