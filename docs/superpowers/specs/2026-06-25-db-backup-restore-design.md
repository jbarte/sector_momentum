# Database backup + restore — design

**Date:** 2026-06-25
**Status:** Approved design
**Branch:** `feature/db-backup-restore`

## Goal

Write a full backup of the database on every scan, committed to the repo, and
provide a restore command — so the scan history can be recovered if the DB is
wiped or corrupted (as happened on 2026-06-25).

## Why

The history that powers ΔRank, trajectory, and emerging depends on many past
scans; losing it is expensive and, on Supabase's free tier, not self-serve
recoverable. A per-run, repo-committed backup makes recovery a one-command
restore instead of a from-scratch rebuild, and is offsite-independent (survives
even if the Supabase project itself is lost). Pairs with the hardened test wipe
guard (`fix/harden-db-wipe-guard`): that prevents the accident, this makes any
future one recoverable.

## Architecture

A new isolated module **`src/backup.py`** owns all backup/restore logic. `scan.py`
calls the backup after a successful save; a standalone **`restore.py`** at the
repo root uses the module for recovery. No change to scoring, the dashboard, or
the DB schema.

The module is split into pure (no DB) and DB-touching functions so the bulk of
the logic is unit-testable without a live database:

- `dump_tables(conn) -> dict[str, pd.DataFrame]` — read every row from `scans`,
  `scores`, `signals` (DB read).
- `write_backup(tables: dict[str, pd.DataFrame], backup_dir="backups") -> Path` —
  write `scans.csv`, `scores.csv`, `signals.csv`, and `manifest.json`. **Pure.**
- `read_backup(backup_dir="backups") -> dict[str, pd.DataFrame]` — read them back.
  **Pure.**
- `load_tables(conn, tables: dict[str, pd.DataFrame], *, force=False) -> dict[str, int]`
  — insert rows into the DB (DB write); returns per-table inserted counts.
- `backup_database(conn, backup_dir="backups") -> Path` — convenience wrapper:
  `write_backup(dump_tables(conn), backup_dir)`.
- `restore_database(conn, backup_dir="backups", *, force=False) -> dict[str, int]`
  — convenience wrapper: `load_tables(conn, read_backup(backup_dir), force=force)`.

## Format & layout

**CSV per table**, one overwriting set in `backups/` at the repo root:

```
backups/scans.csv      # scan_id, run_at, config_hash
backups/scores.csv     # scan_id, region, gics_sector, level_score, change_score,
                       #   data_score, sentiment_score, composite, rank
backups/signals.csv    # scan_id, region, gics_sector, signal_name, raw_value, z_value
backups/manifest.json  # {generated_at, row_counts:{scans,scores,signals}, max_scan_id}
```

CSV is chosen because it is git-diffable (each day's commit shows exactly what
the scan added), pandas-native (`to_csv`/`read_csv`), round-trips floats and NaN,
and needs no new dependency. Columns are written in the schema order above, with
`index=False`.

**Retention = git.** A single overwriting set of files is committed each run, so
`git log backups/` *is* the rolling backup history. Restoring an older state is
`git checkout <commit> -- backups/` then restore. No timestamped folders, no
pruning logic — the data is KB-sized (a full dump today is 1 scan / 22 scores /
242 signals).

`backups/` is **not** in `.gitignore` (verified), so committing it needs no
gitignore change.

## Backup flow (every scan)

In `scan.py:run()`, immediately after `save_scan(...)` succeeds in the
non-dry-run branch, call `backup_database(conn)`. It is **non-fatal**: wrapped in
try/except that logs a warning on failure but never fails the scan, report, or
dashboard build. A new `--no-backup` CLI flag (mirroring `--no-dashboard`) skips
it. Because the call lives inside `scan.py`, both local `/scan` runs and the CI
scan get backups through one code path — no duplication.

Ordering in `run()`: save_scan → **backup** → report → dashboard. (Backup right
after the save so it captures exactly the just-persisted state, and before the
slower report/dashboard steps.)

## Restore flow (`python restore.py [backup_dir] [--force]`)

`restore.py` connects via `init_db()`, then calls `restore_database(conn,
backup_dir, force=force)`:

1. `read_backup` loads the three CSVs; if any file is missing or lacks its
   expected columns, abort with a clear error before touching the DB.
2. **Empty-guard:** if any target table already has rows and `--force` is not
   set, abort with a message (prevents clobbering/duplicating live data — the
   normal case is restoring into an empty DB). `--force` first `DELETE`s all rows
   from the three tables, then loads.
3. Insert in FK-safe order: `scans` first, then `signals` and `scores`. Insert
   `scan_id` **explicitly** to preserve identity.
4. Reset the serial sequence so future scans don't collide:
   `SELECT setval(pg_get_serial_sequence('scans','scan_id'), (SELECT MAX(scan_id) FROM scans))`.
5. Wrap 2–4 in a single transaction (all-or-nothing).
6. Print restored per-table row counts.

## CI change

`scan.yml` already runs `scan.py` (backup now happens inside it, independent of
`--no-dashboard`) and commits results. The only edit: the commit step's
`git add docs/` becomes `git add docs/ backups/`, so the day's backup is
committed and pushed with the dashboard.

## Error handling

- **Backup:** non-fatal — log a warning and continue; the scan still succeeds.
- **Restore:** validate all three CSVs (presence + expected columns) before any
  DB write; refuse on a non-empty DB unless `--force`; perform the load in one
  transaction so a mid-load failure leaves the DB unchanged.

## Testing

- `write_backup` → `read_backup` **round-trip**: sample DataFrames (including a
  NaN `sentiment_score` and NaN signal values) survive the CSV round-trip with
  values and columns intact. Pure, no DB.
- `manifest.json` contents: row counts and `max_scan_id` match the input.
- Restore **empty-guard**: `load_tables(conn, tables, force=False)` against a fake
  connection reporting existing rows raises/aborts; with `force=True` it proceeds.
- Restore **column validation**: `read_backup` on a dir with a malformed CSV
  (missing column) raises before any DB write.
- The sequence-reset statement is emitted during a restore (assert via the fake
  connection's recorded SQL).
- Any genuinely DB-touching integration test reuses the hardened
  `TEST_DATABASE_URL`-gated fixture (skipped by default), so the suite never
  needs a live DB and can never wipe production.

## Out of scope

- Encryption of backups.
- External / offsite object storage (Supabase's own paid backups remain the
  offsite complement).
- Automated restore-on-detection, partial/point-in-time restore, or per-scan
  delta files (each backup is a full snapshot).
- Backing up anything beyond the three tables.
