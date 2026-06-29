"""Restore the database from a Supabase Storage backup (or a local CSV dir).

Usage:
    python restore.py                       # restore the LATEST Storage backup (empty DB)
    python restore.py backup_<ts>.zip       # restore a specific Storage object
    python restore.py --list                # list Storage backups and exit
    python restore.py --local backups       # restore from a local CSV dir (old git backups)
    add --force to delete existing rows first
"""
import argparse
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from src.state import init_db
from src.backup import restore_database, restore_from_storage
from src import storage_backup

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Restore the DB from a Supabase Storage backup")
    p.add_argument("object_name", nargs="?", default=None,
                   help="Storage object to restore (default: latest)")
    p.add_argument("--list", action="store_true", help="List Storage backups and exit")
    p.add_argument("--local", metavar="DIR", default=None,
                   help="Restore from a local CSV backup dir instead of Storage")
    p.add_argument("--force", action="store_true",
                   help="Delete existing rows before restoring (else refuses a non-empty DB)")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.list:
        for name in storage_backup.list_objects():
            print(name)
        return
    conn = init_db()
    try:
        if args.local:
            counts = restore_database(conn, args.local, force=args.force)
            src = args.local
        else:
            counts = restore_from_storage(conn, args.object_name, force=args.force)
            src = args.object_name or "latest"
    finally:
        conn.close()
    print(f"Restored from {src}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
