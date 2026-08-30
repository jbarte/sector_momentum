"""The monthly restore drill: prove the newest bucket object actually restores.

`tests/test_backup_drill.py` already round-trips dump→write→read→load against a
disposable Postgres, but it builds its own archive. Nothing checked the object
that production actually uploaded. An upload returning 200 proves the bytes left
the machine; it does not prove the zip has all its members, that its CSV columns
still match a schema which has gained columns since (`text_value`,
`prices_asof`, `asof_spread_days`, …), or that `_ARCHIVE_MEMBERS` has not
drifted underneath `restore_from_storage`.

These tests cover the two pieces that can be exercised without the bucket: the
archive verifier, and the guard that keeps the drill pointed at a throwaway
database. Production was wiped on 2026-06-25, and a restore drill is precisely
the shape of job that could do it again.
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.restore_drill as drill
from scripts.restore_drill import assert_disposable_target
from src.backup import (
    _ARCHIVE_MEMBERS,
    _COLUMNS,
    _REQUIRED_TABLES,
    verify_backup_archive,
)


def _archive(*, omit=(), columns=None, counts=None, max_scan_id=7) -> bytes:
    """A well-formed backup zip, with hooks to corrupt one thing at a time."""
    counts = counts or {t: 1 for t in _COLUMNS}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for table, cols in _COLUMNS.items():
            if f"{table}.csv" in omit:
                continue
            use = list(columns) if (columns and table == "scans") else list(cols)
            rows = {c: ["1"] * counts[table] for c in use}
            zf.writestr(f"{table}.csv", pd.DataFrame(rows).to_csv(index=False))
        if "manifest.json" not in omit:
            # A real archive only claims counts for the CSVs it actually ships.
            claimed = {t: n for t, n in counts.items() if f"{t}.csv" not in omit}
            zf.writestr("manifest.json", json.dumps(
                {"row_counts": claimed, "max_scan_id": max_scan_id,
                 "generated_at": "2026-08-30T00:00:00Z"}))
    return buf.getvalue()


def test_a_healthy_archive_reports_its_row_counts():
    report = verify_backup_archive(_archive(counts={t: 3 for t in _COLUMNS}))
    assert report["row_counts"] == {t: 3 for t in _COLUMNS}
    assert report["max_scan_id"] == 7


def test_a_missing_member_is_rejected():
    """The failure an HTTP 200 cannot detect: a zip that uploaded fine but is short."""
    with pytest.raises(ValueError, match="scores.csv"):
        verify_backup_archive(_archive(omit=("scores.csv",)))


def test_a_missing_manifest_is_rejected():
    with pytest.raises(ValueError, match="manifest.json"):
        verify_backup_archive(_archive(omit=("manifest.json",)))


def test_schema_drift_in_a_csv_is_rejected():
    """An archive written before a column was added must fail loudly, not restore
    a table that is silently missing a column."""
    cols = [c for c in _COLUMNS["scans"] if c != "config_hash"]
    with pytest.raises(ValueError, match="config_hash"):
        verify_backup_archive(_archive(columns=cols))


def test_a_manifest_that_disagrees_with_the_csvs_is_rejected():
    """Counts are the drill's assertion; if the manifest lies, the drill is blind."""
    data = _archive(counts={t: 2 for t in _COLUMNS})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["manifest.json"] = json.dumps(
        {"row_counts": {t: 99 for t in _COLUMNS}, "max_scan_id": 7}).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    with pytest.raises(ValueError, match="disagrees"):
        verify_backup_archive(buf.getvalue())


def test_an_empty_scans_table_is_rejected():
    """A structurally perfect archive of nothing restores cleanly and proves nothing."""
    with pytest.raises(ValueError, match="no rows"):
        verify_backup_archive(_archive(counts={**{t: 1 for t in _COLUMNS}, "scans": 0}))


def test_garbage_bytes_are_rejected_as_a_bad_zip():
    with pytest.raises(ValueError, match="not a readable zip"):
        verify_backup_archive(b"this is not a zip file")


def test_every_required_member_is_covered_by_the_verifier():
    """Pins the verifier to _REQUIRED_TABLES so a new required table cannot be
    added to the backup without the drill learning to check it."""
    required = [f"{t}.csv" for t in _REQUIRED_TABLES] + ["manifest.json"]
    for member in required:
        with pytest.raises(ValueError, match=member.replace(".", r"\.")):
            verify_backup_archive(_archive(omit=(member,)))


def test_an_optional_table_may_be_absent_from_an_older_archive():
    """The verifier must not be stricter than the restore path it guards:
    read_backup treats a since-added table as optional, so an archive predating
    it restores fine and must not be reported broken."""
    optional = set(_COLUMNS) - _REQUIRED_TABLES
    assert optional, "expected at least one optional table in the schema"
    for table in optional:
        report = verify_backup_archive(_archive(omit=(f"{table}.csv",)))
        assert report["row_counts"][table] == 0


def test_a_manifest_naming_a_retired_table_is_not_a_disagreement():
    """Pre-rename archives list theme_* in row_counts; load_tables skips those
    rather than failing, so the verifier must too."""
    data = _archive(counts={t: 2 for t in _COLUMNS})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["manifest.json"] = json.dumps(
        {"row_counts": {**{t: 2 for t in _COLUMNS}, "theme_scores": 41},
         "max_scan_id": 7}).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    assert verify_backup_archive(buf.getvalue())["row_counts"]["scans"] == 2


# --- the guard that keeps this job away from production ---------------------

@pytest.mark.parametrize("url", [
    "postgresql://postgres:postgres@localhost:5432/postgres",
    "postgresql://postgres:postgres@127.0.0.1:5432/drill",
])
def test_a_local_throwaway_target_is_allowed(url):
    assert_disposable_target(url)


@pytest.mark.parametrize("url", [
    "postgresql://postgres:pw@db.abcdefghij.supabase.co:5432/postgres",
    "postgresql://postgres.abcdefghij:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    "postgresql://user:pw@10.0.0.5:5432/postgres",
])
def test_a_remote_target_is_refused(url):
    """The drill wipes the target. A Supabase host is never localhost."""
    with pytest.raises(RuntimeError, match="refusing"):
        assert_disposable_target(url)


@pytest.mark.parametrize("url", ["", "not a url", None])
def test_an_unusable_target_fails_safe(url):
    """Same posture as tests/test_state_wipe_guard.py: unparseable means refuse,
    never 'probably fine'."""
    with pytest.raises(RuntimeError, match="refusing"):
        assert_disposable_target(url)


# --- the script's own wiring ------------------------------------------------
# No Postgres is needed to pin these, and they are where a drill goes wrong in
# ways the piece-wise tests above cannot see: drilling the wrong object,
# reporting success on a partial restore, or reaching the network before the
# safety guard has run.

LOCAL = "postgresql://postgres:postgres@localhost:5432/postgres"


@pytest.fixture
def fake_bucket(monkeypatch):
    """Stands in for Supabase Storage; records what the drill asked for."""
    state = {"listed": 0, "downloaded": None,
             "objects": ["backup_2026-08-28T06-00-00Z.zip",
                         "backup_2026-08-30T06-00-00Z.zip",
                         "not-a-backup.txt"],
             "payload": _archive(counts={t: 4 for t in _COLUMNS})}

    def _list(bucket=None):
        state["listed"] += 1
        return list(state["objects"])

    def _download(name, bucket=None):
        state["downloaded"] = name
        return state["payload"]

    monkeypatch.setattr(drill.storage_backup, "list_objects", _list)
    monkeypatch.setattr(drill.storage_backup, "download", _download)
    return state


@pytest.fixture
def fake_db(monkeypatch):
    """Stands in for the throwaway Postgres.

    `db_counts` is what the DATABASE reports afterwards — deliberately a
    separate channel from what `load_tables` was handed, because the drill's
    whole assertion is that those two agree. Defaults to a faithful restore.
    """
    state = {"loaded": None, "closed": False, "db_counts": None}

    class _Conn:
        def close(self):
            state["closed"] = True

    def _load(conn, tables, *, force=False):
        state["loaded"] = {"tables": tables, "force": force}
        return {t: len(df) for t, df in tables.items()}

    def _counts(conn):
        if state["db_counts"] is not None:
            return state["db_counts"]
        return {t: len(df) for t, df in state["loaded"]["tables"].items()}

    monkeypatch.setattr(drill, "init_db", lambda: _Conn())
    monkeypatch.setattr(drill, "load_tables", _load)
    monkeypatch.setattr(drill, "table_row_counts", _counts)
    return state


def test_the_drill_takes_the_newest_backup_and_ignores_other_objects(
        monkeypatch, fake_bucket, fake_db):
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    assert drill.main([]) == 0
    assert fake_bucket["downloaded"] == "backup_2026-08-30T06-00-00Z.zip"


def test_the_drill_restores_with_force_and_closes_the_connection(
        monkeypatch, fake_bucket, fake_db):
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    assert drill.main([]) == 0
    assert fake_db["loaded"]["force"] is True, "an empty drill DB still must not block"
    assert fake_db["closed"] is True


def test_a_named_object_is_drilled_without_listing_the_bucket(
        monkeypatch, fake_bucket, fake_db):
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    assert drill.main(["backup_2026-08-28T06-00-00Z.zip"]) == 0
    assert fake_bucket["downloaded"] == "backup_2026-08-28T06-00-00Z.zip"
    assert fake_bucket["listed"] == 0


def test_a_partial_restore_fails_the_drill(monkeypatch, fake_bucket, fake_db):
    """The failure the drill exists to catch: load_tables raised nothing, but
    the database does not actually hold what the archive said."""
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    fake_db["db_counts"] = {t: 4 for t in _COLUMNS} | {"signals": 3}
    assert drill.main([]) == 1


def test_a_restore_that_landed_nothing_fails_the_drill(monkeypatch, fake_bucket, fake_db):
    """The regression this fixture shape exists for: comparing load_tables'
    return against the archive compares a number with itself and passes even
    when the database is empty."""
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    fake_db["db_counts"] = {t: 0 for t in _COLUMNS}
    assert drill.main([]) == 1


def test_a_truncated_bucket_listing_fails_rather_than_drilling_the_wrong_object(
        monkeypatch, fake_bucket, fake_db):
    """list_objects sends limit=1000 and does not paginate, sorted ascending —
    a full page means the newest backup may not be in it. Silently drilling the
    1000th-oldest while reporting PASSED is the failure this job exists to
    prevent."""
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    fake_bucket["objects"] = [f"backup_{i:04d}.zip" for i in range(drill._LIST_LIMIT)]
    assert drill.main([]) == 1
    assert fake_bucket["downloaded"] is None


def test_an_empty_bucket_fails_the_drill(monkeypatch, fake_bucket, fake_db):
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    fake_bucket["objects"] = []
    assert drill.main([]) == 1


def test_the_guard_runs_before_the_bucket_is_ever_touched(
        monkeypatch, fake_bucket, fake_db):
    """A remote target must abort with nothing downloaded and nothing loaded —
    the ordering matters as much as the check."""
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql://postgres:pw@db.abcdefghij.supabase.co:5432/postgres")
    with pytest.raises(RuntimeError, match="refusing"):
        drill.main([])
    assert fake_bucket["downloaded"] is None
    assert fake_bucket["listed"] == 0
    assert fake_db["loaded"] is None


def test_a_corrupt_archive_aborts_before_touching_the_database(
        monkeypatch, fake_bucket, fake_db):
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    fake_bucket["payload"] = _archive(omit=("scores.csv",))
    with pytest.raises(ValueError, match="scores.csv"):
        drill.main([])
    assert fake_db["loaded"] is None
