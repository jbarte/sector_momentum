#!/usr/bin/env python3
"""Prove the newest bucket backup actually restores — not just that it uploaded.

`scan.py` uploads a full DB zip before every scan and swallows failures as
non-fatal, so the first symptom of a broken backup would be silence. This drill
downloads the object production most recently wrote, verifies its structure,
restores it into a THROWAWAY database, and asserts the restored row counts match
what the archive claims.

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
    python3 scripts/restore_drill.py

`DATABASE_URL` is the RESTORE TARGET and every row in it is deleted. The drill
refuses to run against anything that is not localhost — see
assert_disposable_target. It never needs, and must never be given, the
production database URL: `storage_backup` reaches the bucket over HTTPS using
SUPABASE_URL + SUPABASE_SERVICE_KEY, so the production credential has no reason
to be in this job's environment at all.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import storage_backup
from src.backup import load_tables, table_row_counts, verify_backup_archive
from src.state import init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("restore_drill")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

#: storage_backup.list_objects sends this limit and does not paginate.
_LIST_LIMIT = 1000


def assert_disposable_target(database_url: str | None) -> None:
    """Refuse to restore into anything but a local throwaway database.

    Fails safe in the same posture as `tests/test_state_wipe_guard.py`: an
    empty or unparseable URL is refused rather than assumed harmless. A Supabase
    host is never localhost, so this cannot resolve to production — which is the
    whole point, production having been wiped on 2026-06-25 by a job of exactly
    this shape.
    """
    try:
        host = urlparse(database_url or "").hostname
    except ValueError:
        host = None
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"refusing to run the restore drill against host {host!r}: this job "
            f"DELETES every row in DATABASE_URL and may only target a throwaway "
            f"database on {sorted(_LOCAL_HOSTS)}"
        )


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("object_name", nargs="?", default=None,
                   help="Storage object to drill (default: the newest one)")
    p.add_argument("--bucket", default=storage_backup.DEFAULT_BUCKET)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    assert_disposable_target(os.environ.get("DATABASE_URL"))

    name = args.object_name
    if name is None:
        listing = storage_backup.list_objects(bucket=args.bucket)
        if len(listing) >= _LIST_LIMIT:
            # list_objects sends limit=1000 with no pagination and sorts name
            # ascending, so a full page means "newest" is not necessarily in
            # it. Drilling the 1000th-oldest backup while reporting PASSED is
            # the exact silent-green failure this job exists to prevent.
            logger.error(
                "bucket listing returned %d objects, the API page limit — the "
                "newest backup may not be in it. Prune the bucket or add "
                "pagination to storage_backup.list_objects before trusting "
                "this drill.", len(listing))
            return 1
        names = [n for n in listing
                 if n.startswith("backup_") and n.endswith(".zip")]
        if not names:
            logger.error("no backups found in bucket '%s'", args.bucket)
            return 1
        name = names[-1]  # ISO-ish timestamps sort chronologically

    logger.info("Drilling %s/%s", args.bucket, name)
    data = storage_backup.download(name, bucket=args.bucket)
    logger.info("Downloaded %d bytes", len(data))

    report = verify_backup_archive(data)
    logger.info("Archive verified: %s (max_scan_id=%s)",
                report["row_counts"], report["max_scan_id"])

    conn = init_db()
    try:
        # The verifier's own parse, not a second extraction: what was checked is
        # exactly what gets loaded.
        load_tables(conn, report["tables"], force=True)
        # Read the counts back OUT of the database. load_tables returns the
        # length of the frames it was handed, so comparing that against the
        # archive compares a number with itself and passes even if nothing
        # landed.
        restored = table_row_counts(conn)
    finally:
        conn.close()

    expected = report["row_counts"]
    mismatched = {t: (expected[t], restored.get(t))
                  for t in expected if restored.get(t) != expected[t]}
    if mismatched:
        logger.error("RESTORE MISMATCH — archive says %s, database got %s",
                     expected, restored)
        for t, (want, got) in mismatched.items():
            logger.error("  %s: expected %s rows, restored %s", t, want, got)
        return 1

    logger.info("Restore drill PASSED — %s restored intact from %s",
                ", ".join(f"{t}={n}" for t, n in restored.items()), name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
