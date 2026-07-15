"""Database backup + restore for the Sector Momentum scanner.

Backups are a full CSV dump of the scans/scores/signals tables, committed to
the repo under backups/. Pure (file-only) helpers live alongside DB-touching
ones so the serialization logic is testable without a live database.
"""
from __future__ import annotations

import io
import json
import logging
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import storage_backup

logger = logging.getLogger(__name__)

# Schema column order (matches src/state.py:_DDL_STATEMENTS).
_COLUMNS = {
    "scans": ("scan_id", "run_at", "config_hash"),
    "scores": ("scan_id", "region", "gics_sector", "level_score", "change_score",
               "data_score", "sentiment_score", "composite", "rank"),
    "signals": ("scan_id", "region", "gics_sector", "signal_name", "raw_value", "z_value"),
    "sentiment_signals": ("scan_id", "region", "gics_sector", "signal_name", "value", "text_value"),
    "theme_scores": ("scan_id", "theme", "level_score", "change_score", "data_score",
                     "sentiment_score", "composite", "rank"),
    "theme_signals": ("scan_id", "theme", "signal_name", "raw_value", "z_value"),
    "theme_sentiment_signals": ("scan_id", "theme", "signal_name", "value", "text_value"),
}

# FK-safe orderings: parents before children (insert), children before parents (delete).
_INSERT_ORDER = ("scans", "signals", "scores", "sentiment_signals", "theme_scores",
                 "theme_signals", "theme_sentiment_signals")
_DELETE_ORDER = tuple(reversed(_INSERT_ORDER))


def write_backup(tables: dict[str, pd.DataFrame], backup_dir: str | Path = "backups") -> Path:
    """Write scans/scores/signals CSVs + manifest.json to backup_dir (overwriting)."""
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, cols in _COLUMNS.items():
        tables[name].reindex(columns=list(cols)).to_csv(d / f"{name}.csv", index=False)
    scans = tables["scans"]
    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "row_counts": {name: int(len(tables[name])) for name in _COLUMNS},
        "max_scan_id": int(scans["scan_id"].max()) if len(scans) else None,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    logger.info("Backup written to %s (%s)", d, manifest["row_counts"])
    return d


_REQUIRED_TABLES = {"scans", "scores", "signals"}


def read_backup(backup_dir: str | Path = "backups") -> dict[str, pd.DataFrame]:
    """Read CSVs back. Missing optional tables (pre-theme backups) get empty DFs."""
    d = Path(backup_dir)
    tables: dict[str, pd.DataFrame] = {}
    for name, cols in _COLUMNS.items():
        f = d / f"{name}.csv"
        if not f.exists():
            if name in _REQUIRED_TABLES:
                raise FileNotFoundError(f"backup file missing: {f}")
            tables[name] = pd.DataFrame(columns=list(cols))
            continue
        df = pd.read_csv(f)
        missing = set(cols) - set(df.columns)
        if missing:
            raise ValueError(f"{f} is missing columns: {sorted(missing)}")
        tables[name] = df[list(cols)]
    return tables


def dump_tables(conn) -> dict[str, pd.DataFrame]:
    """Read every row from all tables into DataFrames (deterministic order)."""
    order = {
        "scans": "ORDER BY scan_id",
        "scores": "ORDER BY scan_id, region, gics_sector",
        "signals": "ORDER BY scan_id, region, gics_sector, signal_name",
        "sentiment_signals": "ORDER BY scan_id, region, gics_sector, signal_name",
        "theme_scores": "ORDER BY scan_id, theme",
        "theme_signals": "ORDER BY scan_id, theme, signal_name",
        "theme_sentiment_signals": "ORDER BY scan_id, theme, signal_name",
    }
    out = {}
    for name, cols in _COLUMNS.items():
        sql = f"SELECT {', '.join(cols)} FROM {name} {order[name]}"
        out[name] = pd.read_sql_query(sql, conn)
    return out


def _rows_with_nulls(df: pd.DataFrame, cols: tuple[str, ...]) -> list[tuple]:
    """Records in column order with pandas NaN/NaT converted to None (SQL NULL)."""
    ordered = df.reindex(columns=list(cols))
    return [tuple(None if pd.isna(v) else v for v in rec)
            for rec in ordered.itertuples(index=False, name=None)]


def load_tables(conn, tables: dict[str, pd.DataFrame], *, force: bool = False) -> dict[str, int]:
    """Insert backup rows into the DB. Refuses a non-empty DB unless force=True."""
    counts: dict[str, int] = {}
    with conn:
        with conn.cursor() as cur:
            non_empty = False
            for name in _COLUMNS:
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                if cur.fetchone()[0]:
                    non_empty = True
            if non_empty and not force:
                raise RuntimeError(
                    "target database is not empty; pass force=True (restore.py --force) "
                    "to delete existing rows before restoring"
                )
            if force:
                for name in _DELETE_ORDER:
                    cur.execute(f"DELETE FROM {name}")
            for name in _INSERT_ORDER:
                cols = _COLUMNS[name]
                df = tables.get(name, pd.DataFrame(columns=list(cols)))
                rows = _rows_with_nulls(df, cols)
                if rows:
                    placeholders = ", ".join(["%s"] * len(cols))
                    cur.executemany(
                        f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({placeholders})",
                        rows,
                    )
                counts[name] = len(rows)
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('scans', 'scan_id'), "
                "(SELECT COALESCE(MAX(scan_id), 1) FROM scans))"
            )
    return counts


def backup_database(conn, backup_dir: str | Path = "backups") -> Path:
    """Dump the DB and write a CSV backup. Returns the backup directory."""
    return write_backup(dump_tables(conn), backup_dir)


def restore_database(conn, backup_dir: str | Path = "backups", *, force: bool = False) -> dict[str, int]:
    """Load a CSV backup into the DB. Returns per-table inserted counts."""
    return load_tables(conn, read_backup(backup_dir), force=force)


_ARCHIVE_MEMBERS = tuple(f"{t}.csv" for t in _COLUMNS) + ("manifest.json",)


def backup_to_storage(conn, bucket: str = storage_backup.DEFAULT_BUCKET) -> str:
    """Dump the DB, zip the CSV backup, and upload it to Supabase Storage."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    object_name = f"backup_{ts}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        write_backup(dump_tables(conn), tmp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for member in _ARCHIVE_MEMBERS:
                zf.write(Path(tmp) / member, arcname=member)
        storage_backup.upload(object_name, buf.getvalue(), bucket=bucket)
    logger.info("Backup uploaded to Storage: %s/%s", bucket, object_name)
    return object_name


def restore_from_storage(conn, object_name: str | None = None,
                         bucket: str = storage_backup.DEFAULT_BUCKET, *,
                         force: bool = False) -> dict[str, int]:
    """Download a backup object (latest if unspecified) and load it into the DB."""
    if object_name is None:
        names = [
            n for n in storage_backup.list_objects(bucket=bucket)
            if n.startswith("backup_") and n.endswith(".zip")
        ]
        if not names:
            raise RuntimeError(f"no backups found in bucket '{bucket}'")
        object_name = names[-1]  # ISO-ish timestamps sort chronologically
    data = storage_backup.download(object_name, bucket=bucket)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tmp)
        tables = read_backup(tmp)
    logger.info("Restoring from Storage object %s/%s", bucket, object_name)
    return load_tables(conn, tables, force=force)
