# Supabase Storage Pre-Run DB Backups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the git-committed `backups/` CSV dump with a **pre-run backup uploaded to Supabase Storage**, and stop committing `backups/` to the repo.

**Architecture:** A new `requests`-based Storage client (`src/storage_backup.py`) plus two functions in `src/backup.py` (`backup_to_storage`, `restore_from_storage`) that reuse the existing table dump/load logic and zip the dump into one timestamped object. `scan.py` takes the backup before it writes; `restore.py` pulls from Storage; CI drops the `backups/` commit.

**Tech Stack:** Python 3, `requests` (already a dep), pandas, psycopg2, zipfile/tempfile (stdlib), pytest.

## Global Constraints

- **No new dependency** — use `requests` (already in `requirements.txt`) against the Supabase Storage REST API. No `supabase`/`boto3`.
- **One new secret** — `SUPABASE_SERVICE_KEY` (service-role key). Storage base URL derived from `DATABASE_URL` (`db.<ref>.supabase.co` → `https://<ref>.supabase.co`), overridable via `SUPABASE_URL`.
- **Bucket:** private bucket `db-backups`; objects named `backup_<UTC>.zip` (no `:` in the name) containing `scans.csv`/`scores.csv`/`signals.csv`/`manifest.json`.
- **Non-fatal in the scan** — a backup failure logs a warning and the scan continues; gated by the existing `--no-backup`. The backup runs **before** `save_scan`.
- **`docs/` is CI-owned** — feature branch is source-only.
- **Reuse** `dump_tables`/`load_tables`/`write_backup`/`read_backup`/`_COLUMNS` in `src/backup.py` — do not reimplement table serialization.
- **Commit style:** conventional commits, subject < 72 chars; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File structure

- `src/storage_backup.py` (new) — Storage REST client.
- `src/backup.py` (modify) — `backup_to_storage`, `restore_from_storage`.
- `scan.py` (modify) — pre-run backup.
- `restore.py` (modify) — Storage default + `--list`/`--local`.
- `.github/workflows/scan.yml`, `.gitignore`, `.env.example`, `CLAUDE.md`, `BACKLOG.md` (modify).
- Tests under `tests/`.

---

### Task 1: Supabase Storage REST client

**Files:**
- Create: `src/storage_backup.py`
- Test: `tests/test_storage_backup.py`

**Interfaces:**
- Produces:
  - `upload(object_name: str, data: bytes, bucket: str = "db-backups") -> None`
  - `download(object_name: str, bucket: str = "db-backups") -> bytes`
  - `list_objects(bucket: str = "db-backups") -> list[str]` (names, ascending)
  - `_base_url() -> str` (explicit `SUPABASE_URL` or derived from `DATABASE_URL`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_backup.py
import types
import pytest
from src import storage_backup


def test_base_url_derives_from_database_url(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw@db.abcdef123.supabase.co:5432/postgres")
    assert storage_backup._base_url() == "https://abcdef123.supabase.co"


def test_base_url_explicit_override(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/")
    assert storage_backup._base_url() == "https://xyz.supabase.co"


class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


def test_upload_posts_to_object_url_with_bearer(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    calls = {}
    def fake_post(url, data=None, json=None, headers=None, timeout=None):
        calls.update(url=url, data=data, headers=headers)
        return _Resp()
    monkeypatch.setattr(storage_backup.requests, "post", fake_post)
    storage_backup.upload("backup_x.zip", b"ZIPBYTES", bucket="db-backups")
    assert calls["url"] == "https://xyz.supabase.co/storage/v1/object/db-backups/backup_x.zip"
    assert calls["data"] == b"ZIPBYTES"
    assert calls["headers"]["Authorization"] == "Bearer svc-key"


def test_list_objects_returns_sorted_names(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    monkeypatch.setattr(storage_backup.requests, "post",
                        lambda *a, **k: _Resp(payload=[{"name": "backup_b.zip"}, {"name": "backup_a.zip"}]))
    assert storage_backup.list_objects() == ["backup_a.zip", "backup_b.zip"]


def test_service_key_required(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        storage_backup.download("x.zip")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_backup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.storage_backup'`.

- [ ] **Step 3: Implement `src/storage_backup.py`**

```python
# src/storage_backup.py
"""Thin Supabase Storage REST client (backups bucket) over `requests`.

Credentials: SUPABASE_SERVICE_KEY (service-role). Base URL is SUPABASE_URL if
set, else derived from DATABASE_URL's db.<ref>.supabase.co host.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "db-backups"
_TIMEOUT = 30


def _base_url() -> str:
    explicit = os.environ.get("SUPABASE_URL")
    if explicit:
        return explicit.rstrip("/")
    host = urlparse(os.environ.get("DATABASE_URL", "")).hostname or ""
    if host.startswith("db.") and host.endswith(".supabase.co"):
        ref = host[len("db."):-len(".supabase.co")]
        return f"https://{ref}.supabase.co"
    raise RuntimeError(
        "cannot resolve Supabase URL: set SUPABASE_URL or a db.<ref>.supabase.co DATABASE_URL"
    )


def _service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is not set")
    return key


def _headers(extra: dict | None = None) -> dict:
    key = _service_key()
    h = {"Authorization": f"Bearer {key}", "apikey": key}
    if extra:
        h.update(extra)
    return h


def upload(object_name: str, data: bytes, bucket: str = DEFAULT_BUCKET) -> None:
    url = f"{_base_url()}/storage/v1/object/{bucket}/{object_name}"
    resp = requests.post(
        url, data=data,
        headers=_headers({"Content-Type": "application/zip", "x-upsert": "true"}),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def download(object_name: str, bucket: str = DEFAULT_BUCKET) -> bytes:
    url = f"{_base_url()}/storage/v1/object/{bucket}/{object_name}"
    resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def list_objects(bucket: str = DEFAULT_BUCKET) -> list[str]:
    url = f"{_base_url()}/storage/v1/object/list/{bucket}"
    resp = requests.post(
        url,
        json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return sorted(item["name"] for item in resp.json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage_backup.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/storage_backup.py tests/test_storage_backup.py
git commit -m "feat: Supabase Storage REST client for DB backups

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `backup_to_storage` / `restore_from_storage`

**Files:**
- Modify: `src/backup.py`
- Test: `tests/test_backup_storage.py`

**Interfaces:**
- Consumes: `dump_tables`, `write_backup`, `read_backup`, `load_tables` (this module); `src.storage_backup.upload/download/list_objects`.
- Produces:
  - `backup_to_storage(conn, bucket: str = "db-backups") -> str` — returns the uploaded object name `backup_<UTC>.zip`.
  - `restore_from_storage(conn, object_name: str | None = None, bucket: str = "db-backups", *, force: bool = False) -> dict[str, int]` — `None` → latest object.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backup_storage.py
import io
import zipfile
import pandas as pd
import pytest
from src import backup as bk


def _tables():
    return {
        "scans": pd.DataFrame([{"scan_id": 1, "run_at": "2026-01-01", "config_hash": "h"}]),
        "scores": pd.DataFrame([{"scan_id": 1, "region": "US", "gics_sector": "Energy",
                                 "level_score": 0.1, "change_score": 0.2, "data_score": 0.3,
                                 "sentiment_score": 0.0, "composite": 0.4, "rank": 1.0}]),
        "signals": pd.DataFrame([{"scan_id": 1, "region": "US", "gics_sector": "Energy",
                                  "signal_name": "return_1m", "raw_value": 0.5, "z_value": 0.6}]),
    }


def test_backup_to_storage_uploads_valid_zip(monkeypatch):
    captured = {}
    monkeypatch.setattr(bk, "dump_tables", lambda conn: _tables())
    monkeypatch.setattr(bk.storage_backup, "upload",
                        lambda name, data, bucket="db-backups": captured.update(name=name, data=data))
    name = bk.backup_to_storage(conn=object())
    assert name.startswith("backup_") and name.endswith(".zip") and ":" not in name
    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        names = set(zf.namelist())
    assert {"scans.csv", "scores.csv", "signals.csv", "manifest.json"} <= names


def test_restore_from_storage_latest_then_load(monkeypatch):
    # Build a zip the same way backup does, serve it via mocked storage.
    cap = {}
    monkeypatch.setattr(bk, "dump_tables", lambda conn: _tables())
    monkeypatch.setattr(bk.storage_backup, "upload",
                        lambda name, data, bucket="db-backups": cap.update(data=data))
    bk.backup_to_storage(conn=object())

    monkeypatch.setattr(bk.storage_backup, "list_objects",
                        lambda bucket="db-backups": ["backup_2026-01-01T00-00-00Z.zip"])
    monkeypatch.setattr(bk.storage_backup, "download",
                        lambda name, bucket="db-backups": cap["data"])
    loaded = {}
    monkeypatch.setattr(bk, "load_tables",
                        lambda conn, tables, force=False: loaded.update(tables=tables, force=force) or {"scans": 1})
    out = bk.restore_from_storage(conn=object(), force=True)
    assert out == {"scans": 1}
    assert set(loaded["tables"]) == {"scans", "scores", "signals"}
    assert loaded["force"] is True


def test_restore_from_storage_empty_bucket_raises(monkeypatch):
    monkeypatch.setattr(bk.storage_backup, "list_objects", lambda bucket="db-backups": [])
    with pytest.raises(RuntimeError):
        bk.restore_from_storage(conn=object())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backup_storage.py -v`
Expected: FAIL — `backup_to_storage` not defined / `bk.storage_backup` missing.

- [ ] **Step 3: Add the functions to `src/backup.py`**

Add the imports near the top (after the existing imports):
```python
import io
import tempfile
import zipfile
from datetime import datetime, timezone

from src import storage_backup
```
Append the two functions:
```python
_ARCHIVE_MEMBERS = ("scans.csv", "scores.csv", "signals.csv", "manifest.json")


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
        names = storage_backup.list_objects(bucket=bucket)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backup_storage.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backup.py tests/test_backup_storage.py
git commit -m "feat: backup_to_storage / restore_from_storage (zip + Supabase Storage)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Pre-run backup in the scan

**Files:**
- Modify: `scan.py`
- Test: `tests/test_scan_smoke.py`

**Interfaces:**
- Consumes: `backup_to_storage` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scan_smoke.py  (add this test)
def test_pre_run_backup_is_called_and_nonfatal(monkeypatch):
    """backup_to_storage runs before save_scan and a failure does not abort the scan."""
    import src.backup as bk
    calls = []
    def boom(conn, *a, **k):
        calls.append("backup")
        raise RuntimeError("storage down")
    monkeypatch.setattr(bk, "backup_to_storage", boom)
    # The scan must still reach scoring/persistence despite the backup raising.
    # (Assert via the existing smoke harness that the run completes; `calls` shows it tried.)
    # See the existing smoke test for how `run()` is invoked with mocks.
    assert boom  # placeholder anchor; wire into the existing smoke run below
```
Adapt to the existing `test_scan_smoke.py` harness: in the smoke run that already mocks fetch/score/DB, assert `backup_to_storage` is invoked and that raising inside it does not propagate (the run still returns its success path). Use the smoke test's existing monkeypatch style for `init_db`/`save_scan`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scan_smoke.py -k backup -v`
Expected: FAIL (pre-run backup not wired / not called).

- [ ] **Step 3: Wire the pre-run backup into `scan.py`**

Change the top import (line ≈ 49) from:
```python
from src.backup import backup_database
```
to:
```python
from src.backup import backup_to_storage
```
Remove the post-scan backup block (≈ lines 337–342):
```python
        if not args.no_backup:
            try:
                backup_database(conn)
                logger.info("Database backup written to backups/")
            except Exception as exc:  # non-fatal: a backup failure must not fail the scan
                logger.warning("Database backup failed (%s) — continuing", exc)
```
Add a **pre-run** backup immediately after `conn = init_db()` (≈ line 303):
```python
    conn = init_db()

    if not args.no_backup:
        try:
            name = backup_to_storage(conn)
            logger.info("Pre-run DB backup uploaded to Storage (%s)", name)
        except Exception as exc:  # non-fatal: a backup failure must not fail the scan
            logger.warning("Pre-run backup failed (%s) — continuing", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scan_smoke.py -v`
Expected: PASS (existing smoke tests + the new pre-run backup test).

- [ ] **Step 5: Commit**

```bash
git add scan.py tests/test_scan_smoke.py
git commit -m "feat: pre-run DB backup to Supabase Storage in scan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `restore.py` — Storage default + `--list`/`--local`

**Files:**
- Modify: `restore.py`
- Test: `tests/test_restore_cli.py`

**Interfaces:**
- Consumes: `restore_from_storage`, `restore_database` (`src.backup`), `storage_backup.list_objects`.
- Produces: `_parse_args()` with `object_name` (optional), `--list`, `--local DIR`, `--force`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_restore_cli.py
import restore


def test_parse_args_defaults():
    ns = restore._parse_args([])
    assert ns.object_name is None and ns.list is False and ns.local is None and ns.force is False


def test_parse_args_object_and_force():
    ns = restore._parse_args(["backup_x.zip", "--force"])
    assert ns.object_name == "backup_x.zip" and ns.force is True


def test_parse_args_local_and_list():
    ns = restore._parse_args(["--local", "backups", "--list"])
    assert ns.local == "backups" and ns.list is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_restore_cli.py -v`
Expected: FAIL — `_parse_args()` takes no args / new flags absent.

- [ ] **Step 3: Rewrite `restore.py`**

```python
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


def main():
    args = _parse_args()
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_restore_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add restore.py tests/test_restore_cli.py
git commit -m "feat: restore.py pulls from Supabase Storage (--list/--local)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: CI, gitignore, docs

**Files:**
- Modify: `.github/workflows/scan.yml`, `.gitignore`, `.env.example`, `CLAUDE.md`
- Remove from tracking: `backups/`

- [ ] **Step 1: Update `scan.yml`**

In the **Run scan** step's `env:`, add the service key alongside `DATABASE_URL`:
```yaml
      - name: Run scan
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
        run: python scan.py --no-dashboard
```
In the **Commit results** step, drop `backups/` from the `git add`:
```yaml
          git add docs/
          git diff --staged --quiet || git commit -m "scan: $(date -u +%Y-%m-%d) automated run"
          git push
```

- [ ] **Step 2: Stop tracking `backups/`**

```bash
git rm -r --cached backups/
printf '\n# DB backups now live in Supabase Storage, not git.\nbackups/\n' >> .gitignore
```

- [ ] **Step 3: Update `.env.example`**

Append:
```bash

# Supabase service-role key — required to write the private db-backups Storage bucket.
# Get from: Supabase Dashboard → Project Settings → API → service_role key.
# (Storage URL is derived from DATABASE_URL; set SUPABASE_URL to override.)
SUPABASE_SERVICE_KEY=your-service-role-key
```

- [ ] **Step 4: Note the bucket + secret in `CLAUDE.md`**

Under the project overview / CI section, add a short note:
```markdown
## Backups

The DB is backed up to a **private Supabase Storage bucket `db-backups`** (one
`backup_<UTC>.zip` per scan, taken *before* each run) — not git. Requires the
`SUPABASE_SERVICE_KEY` secret (CI) / env var (local) and the bucket to exist.
Restore with `python restore.py` (latest) / `--list` / `--local <dir>` (old git backups).
```

- [ ] **Step 5: Verify tracking + commit**

Run: `git check-ignore backups/manifest.json && echo "backups ignored OK"`
Expected: `backups ignored OK`.
Run: `git status --short` — confirm `backups/` files show as deleted-from-index (`D`), not present as tracked.

```bash
git add .github/workflows/scan.yml .gitignore .env.example CLAUDE.md
git commit -m "ci: back up to Supabase Storage; stop committing backups/

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full suite + backlog note

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Full suite**

Run: `pytest -q`
Expected: green (existing + 3 new test files; the existing `src/backup.py` dump/load tests still pass).

- [ ] **Step 2: Add a Done entry**

At the top of `## Done` in `BACKLOG.md`:
`- ~~DB backup → Supabase Storage (pre-run)~~ — replaced the git-committed \`backups/\` CSV dump with a pre-run zip uploaded to a private \`db-backups\` Supabase Storage bucket (\`src/storage_backup.py\` + \`backup_to_storage\`/\`restore_from_storage\`); \`scan.py\` backs up before writing; \`scan.yml\` no longer commits \`backups/\`; \`restore.py\` pulls latest from Storage (\`--list\`/\`--local\`). One new secret \`SUPABASE_SERVICE_KEY\`. *(2026-06-29)*`

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: backlog — DB backup moved to Supabase Storage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Storage REST client (requests, derived URL, one secret) → Task 1. ✓
- `backup_to_storage`/`restore_from_storage` reusing dump/load + zip → Task 2. ✓
- Pre-run, non-fatal backup in scan; old post-scan local backup removed → Task 3. ✓
- `restore.py` Storage-default + `--list`/`--local`/object-name → Task 4. ✓
- `scan.yml` secret + drop `backups/` commit; untrack `backups/` + gitignore; `.env.example` + CLAUDE.md → Task 5. ✓
- Full suite + backlog → Task 6. ✓
- Out of scope (retention/pruning, migrating old CSVs) → not in plan. ✓

**Placeholder scan:** Task 3 Step 1 intentionally defers to the existing smoke harness for wiring the assertion (the smoke test's mock style isn't reproduced here) — the implementer must adapt it; all other steps have concrete code.

**Type consistency:** `storage_backup.upload(name, data, bucket=)` / `download(name, bucket=)` / `list_objects(bucket=)` (Task 1) are called with those exact signatures in `backup_to_storage`/`restore_from_storage` (Task 2), which `scan.py` (Task 3) and `restore.py` (Task 4) consume by name. Object name format `backup_<UTC>.zip` (no colons) consistent between Task 2 and the tests. `DEFAULT_BUCKET="db-backups"` defined in Task 1, referenced in Task 2.
