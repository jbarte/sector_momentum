"""Restore the database from a CSV backup written by src/backup.py.

Usage:
    python restore.py [backup_dir]        # restore into an EMPTY db (default: backups/)
    python restore.py [backup_dir] --force  # delete existing rows first, then restore
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.state import init_db
from src.backup import restore_database

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")


def _parse_args():
    p = argparse.ArgumentParser(description="Restore the DB from a CSV backup")
    p.add_argument("backup_dir", nargs="?", default="backups",
                   help="Directory holding scans.csv/scores.csv/signals.csv (default: backups)")
    p.add_argument("--force", action="store_true",
                   help="Delete all existing rows before restoring (otherwise refuses a non-empty DB)")
    return p.parse_args()


def main():
    args = _parse_args()
    conn = init_db()
    try:
        counts = restore_database(conn, args.backup_dir, force=args.force)
    finally:
        conn.close()
    print(f"Restored from {args.backup_dir}: " +
          ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
