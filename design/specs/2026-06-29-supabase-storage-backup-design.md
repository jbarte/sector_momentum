# Supabase Storage DB backups (pre-run) — design

**Date:** 2026-06-29
**Status:** Approved (design)
**Replaces:** the git-committed `backups/` CSV dump (`src/backup.py` + `scan.yml` commit).

## Purpose

Move the database backup off git and into **Supabase Storage**, taken **before each scan
run** rather than after, and stop committing `backups/` to the repo. Preserves the key
property of the current git backup — an **off-database** copy that survives a DB wipe (the
incident that motivated backups) — without the repo-commit noise.

## Why Storage (not in-DB, not managed)

- The backups exist to recover from an accidental DB wipe. The current git backup lives
  off the database, so it survives one. **In-DB snapshot tables would be a DR downgrade**
  (a wipe can take them too). **Supabase managed backups** aren't usable on the free tier.
- Supabase **Storage** keeps the off-DB property, is free-tier-friendly (~80 KB/backup vs a
  1 GB bucket → effectively unlimited retention), and removes the git commit.

## Constraints

- **No new dependency** — use `requests` (already in `requirements.txt`) against the
  Supabase Storage REST API. No `supabase` client, no `boto3`.
- **One new secret** — `SUPABASE_SERVICE_KEY` (service-role key; required to write a private
  bucket). The Storage base URL is **derived from `DATABASE_URL`** (parse the project ref
  from `db.<ref>.supabase.co` → `https://<ref>.supabase.co`), with an optional explicit
  `SUPABASE_URL` override.
- **Non-fatal** — a backup failure logs a warning and the scan continues (matches current
  behavior); gated by the existing `--no-backup`.
- **`docs/` is CI-owned** — feature branch is source-only.

## Components

- **`src/storage_backup.py`** (new) — a thin Supabase Storage REST client over `requests`:
  - `_base_url() -> str` — `SUPABASE_URL` if set, else derive `https://<ref>.supabase.co`
    from `DATABASE_URL`'s `db.<ref>.supabase.co` host. Raises if neither resolvable.
  - `_headers() -> dict` — `Authorization: Bearer <SUPABASE_SERVICE_KEY>` (raises if unset).
  - `upload(object_name: str, data: bytes, bucket: str = "db-backups") -> None` — `POST`
    `{base}/storage/v1/object/{bucket}/{object_name}` (raises for non-2xx).
  - `download(object_name: str, bucket: str = "db-backups") -> bytes` — `GET`
    `{base}/storage/v1/object/{bucket}/{object_name}`.
  - `list_objects(bucket: str = "db-backups") -> list[str]` — `POST`
    `{base}/storage/v1/object/list/{bucket}`, returns object names sorted ascending.
- **`src/backup.py`** (extend; keep `dump_tables`/`load_tables`/`write_backup`/`read_backup`):
  - `backup_to_storage(conn, bucket="db-backups") -> str` — `dump_tables` → write the CSVs +
    `manifest.json` into a temp dir → zip to bytes → `upload("backup_<UTC-ISO>.zip", ...)` →
    return the object name. The timestamp is generated inside (UTC, filename-safe).
  - `restore_from_storage(conn, object_name=None, bucket="db-backups", *, force=False) ->
    dict[str,int]` — if `object_name` is None, pick the latest via `list_objects` → `download`
    → unzip to temp → `read_backup` → `load_tables`.
- **`scan.py`** — replace the post-scan `backup_database(conn)` (≈ lines 337–342) with a
  **pre-run** `backup_to_storage(conn)` called right after `init_db()` (≈ line 303) and
  before `compute_deltas`/`save_scan`. Non-fatal (warn on failure); keep `--no-backup`.
- **`restore.py`** — default to restoring the **latest** backup from Storage; add `--list`
  (print available object names) and an optional `[object_name]` positional; keep `--force`;
  add `--local <dir>` to restore from a local CSV dir (the historical git backups).
- **`scan.yml`** — add `SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}` to the
  scan step's env; change `git add docs/ backups/` → `git add docs/`.
- **Repo hygiene** — `git rm -r --cached backups/` + add `backups/` to `.gitignore`. The
  historical git backups remain recoverable from git history.
- **Docs** — `.env.example` gains `SUPABASE_SERVICE_KEY` (with a one-line how-to-get-it);
  CLAUDE.md notes the `db-backups` bucket must exist and the new secret.

## Data flow

```
scan.py run():
  conn = init_db()
  if not --no-backup:
     backup_to_storage(conn)        # dump → zip → upload backup_<UTC>.zip   (pre-run, non-fatal)
  ... compute_deltas → save_scan (new scan row written) ...

restore.py:
  restore_from_storage(conn, name or latest, force) → download → unzip → load_tables
```

## Error handling / edge cases

- Missing `SUPABASE_SERVICE_KEY` or unresolvable base URL → `storage_backup` raises a clear
  error; in `scan.py` this is caught (non-fatal warning) so the daily scan still runs.
- Non-2xx from Storage → raised; surfaced as the non-fatal warning in `scan.py`, or a hard
  error in `restore.py` (restore must not silently no-op).
- `restore_from_storage` with an empty bucket / no objects → raises a clear "no backups
  found" error.
- `load_tables` keeps its existing **refuses-non-empty-DB-unless-force** guard.

## Testing

- `storage_backup` — with `requests` mocked: `upload`/`download`/`list_objects` build the
  correct URL + Bearer header; `_base_url` derives `https://<ref>.supabase.co` from a sample
  `DATABASE_URL` and honors an explicit `SUPABASE_URL`.
- `backup_to_storage` — with storage mocked: produces a `.zip` whose bytes unzip to the 3
  CSVs + `manifest.json` (round-trip via `read_backup`).
- `restore_from_storage` — with storage mocked (latest-selection + download) → `load_tables`
  called with the right tables; empty bucket → raises.
- `scan.py` — backup is invoked before `save_scan` and is non-fatal (a raising backup does
  not abort the scan); `--no-backup` skips it. (Extend `tests/test_scan_smoke.py`.)
- Existing `src/backup.py` table dump/load tests stay green.

## Out of scope

- Retention/pruning of old Storage objects (keep all; tiny).
- Migrating the existing committed `backups/` CSVs into Storage (they remain in git history).
- Encrypting backups at rest beyond Supabase's defaults.
