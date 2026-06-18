"""
SQLite state management for the Sector Momentum Scanner.

Persists scan results (signals and scores) so each new scan can be compared
to the previous one. Uses WAL mode for better concurrency.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
    region      TEXT NOT NULL,
    gics_sector TEXT NOT NULL,
    signal_name TEXT NOT NULL,
    raw_value   REAL,
    z_value     REAL
);

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
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path = "data/momentum.db") -> sqlite3.Connection:
    """
    Create the database and tables if they don't exist.
    Returns an open connection with WAL mode enabled (better concurrency).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    conn.commit()
    logger.info("Database initialised at %s", db_path)
    return conn


def _compute_config_hash(weights_path: str | Path) -> str:
    """Return SHA-256 hex digest of weights_path contents, or empty string if missing."""
    weights_path = Path(weights_path)
    if not weights_path.exists():
        logger.warning("weights_path %s not found; using empty hash", weights_path)
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(weights_path.read_bytes()).hexdigest()


def save_scan(
    conn: sqlite3.Connection,
    run_at: datetime,
    region_sector_signals: pd.DataFrame,
    scores_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
) -> int:
    """
    Insert a new scan row and all its signals/scores.
    Returns the new scan_id.
    config_hash is computed from the weights.yaml file contents.
    Uses a transaction (all-or-nothing).
    """
    config_hash = _compute_config_hash(weights_path)
    run_at_str = run_at.isoformat()

    with conn:  # transaction – auto-commits or rolls back
        cur = conn.execute(
            "INSERT INTO scans (run_at, config_hash) VALUES (?, ?)",
            (run_at_str, config_hash),
        )
        scan_id = cur.lastrowid

        # --- signals ---
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
            conn.executemany(
                "INSERT INTO signals "
                "(scan_id, region, gics_sector, signal_name, raw_value, z_value) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                signals_rows,
            )

        # --- scores ---
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
            conn.executemany(
                "INSERT INTO scores "
                "(scan_id, region, gics_sector, level_score, change_score, "
                "data_score, sentiment_score, composite, rank) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                scores_rows,
            )

    logger.info("Saved scan_id=%d at %s", scan_id, run_at_str)
    return scan_id


def load_last_scan(
    conn: sqlite3.Connection,
) -> pd.DataFrame | None:
    """
    Load the scores for the most recent scan.
    Returns a DataFrame with columns:
        region, gics_sector, composite, rank, scan_id
    Returns None if no prior scan exists.
    """
    row = conn.execute(
        "SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None

    scan_id = row[0]
    df = pd.read_sql_query(
        "SELECT region, gics_sector, composite, rank, scan_id "
        "FROM scores WHERE scan_id = ?",
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
                          [Phase 1 simplified; Phase 2 will use multi-scan history]

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


def get_signals_for_latest_scan(conn: sqlite3.Connection) -> pd.DataFrame:
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


def get_scan_history(
    conn: sqlite3.Connection,
    n_scans: int = 10,
) -> pd.DataFrame:
    """
    Return scores for the last n_scans scans joined with scan metadata.
    Columns: scan_id, run_at, region, gics_sector,
             level_score, change_score, data_score, sentiment_score, composite, rank
    Ordered by (run_at ASC, region, gics_sector).
    Returns empty DataFrame if no scans exist.
    """
    query = """
        SELECT sc.scan_id, sc.run_at, s.region, s.gics_sector,
               s.level_score, s.change_score, s.data_score, s.sentiment_score,
               s.composite, s.rank
        FROM scores s
        JOIN scans sc ON sc.scan_id = s.scan_id
        WHERE sc.scan_id IN (
            SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT ?
        )
        ORDER BY sc.run_at ASC, s.region, s.gics_sector
    """
    df = pd.read_sql_query(query, conn, params=(n_scans,))
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float_or_none(value) -> float | None:
    """Convert NaN / None to None so SQLite stores NULL, otherwise float."""
    if value is None:
        return None
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None
