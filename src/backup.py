"""Database backup + restore for the Sector Momentum scanner.

Backups are a full CSV dump of the scans/scores/signals tables, committed to
the repo under backups/. Pure (file-only) helpers live alongside DB-touching
ones so the serialization logic is testable without a live database.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Schema column order (matches src/state.py:_DDL_STATEMENTS).
_COLUMNS = {
    "scans": ("scan_id", "run_at", "config_hash"),
    "scores": ("scan_id", "region", "gics_sector", "level_score", "change_score",
               "data_score", "sentiment_score", "composite", "rank"),
    "signals": ("scan_id", "region", "gics_sector", "signal_name", "raw_value", "z_value"),
}


def write_backup(tables: dict[str, pd.DataFrame], backup_dir: str | Path = "backups") -> Path:
    """Write scans/scores/signals CSVs + manifest.json to backup_dir (overwriting)."""
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, cols in _COLUMNS.items():
        tables[name].reindex(columns=list(cols)).to_csv(d / f"{name}.csv", index=False)
    scans = tables["scans"]
    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "row_counts": {name: int(len(tables[name])) for name in _COLUMNS},
        "max_scan_id": int(scans["scan_id"].max()) if len(scans) else None,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    logger.info("Backup written to %s (%s)", d, manifest["row_counts"])
    return d


def read_backup(backup_dir: str | Path = "backups") -> dict[str, pd.DataFrame]:
    """Read the 3 CSVs back. Raises FileNotFoundError / ValueError on a bad backup."""
    d = Path(backup_dir)
    tables: dict[str, pd.DataFrame] = {}
    for name, cols in _COLUMNS.items():
        f = d / f"{name}.csv"
        if not f.exists():
            raise FileNotFoundError(f"backup file missing: {f}")
        df = pd.read_csv(f)
        missing = set(cols) - set(df.columns)
        if missing:
            raise ValueError(f"{f} is missing columns: {sorted(missing)}")
        tables[name] = df[list(cols)]
    return tables
