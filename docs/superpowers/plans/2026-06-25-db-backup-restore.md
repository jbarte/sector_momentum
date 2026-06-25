# Database Backup + Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write a full CSV backup of the database (scans/scores/signals) on every scan, committed to the repo, plus a `restore.py` command to load a backup back into the DB.

**Architecture:** A new isolated module `src/backup.py` splits into pure functions (`write_backup`/`read_backup`, no DB) and DB-touching functions (`dump_tables`/`load_tables`) with thin wrappers (`backup_database`/`restore_database`). `scan.py` calls `backup_database` non-fatally after a successful save; a root `restore.py` CLI uses `restore_database`. CI commits `backups/` alongside `docs/`.

**Tech Stack:** Python 3.11 (pandas, psycopg2), pytest. No new dependencies.

## Global Constraints

- Backups are **CSV per table** in `backups/` at the repo root: `scans.csv`, `scores.csv`, `signals.csv`, plus `manifest.json`. One overwriting set (git history = the rolling backups). `backups/` is NOT gitignored — do not add it.
- Column order is the schema order, written with `index=False`:
  - `scans`: `scan_id, run_at, config_hash`
  - `scores`: `scan_id, region, gics_sector, level_score, change_score, data_score, sentiment_score, composite, rank`
  - `signals`: `scan_id, region, gics_sector, signal_name, raw_value, z_value`
- Backup on scan is **non-fatal** (warn and continue; never fail the scan). New `--no-backup` flag mirrors `--no-dashboard`.
- Restore **refuses on a non-empty DB unless `--force`**; with `--force` it deletes all rows first. Inserts in FK-safe order (`scans` → `signals` → `scores`), preserves `scan_id`s explicitly, resets the serial sequence, all in one transaction. NaN → SQL NULL.
- No changes to scoring, the dashboard, or the DB schema.
- Use `.venv/bin/python` / `.venv/bin/pytest`. **Never run the full suite without first confirming `tests/test_state_smoke.py` SKIPs** — run only the named test files per task. DB-touching tests use fakes, not a live connection.

---

### Task 1: `src/backup.py` — pure CSV serialization

**Files:**
- Create: `src/backup.py`
- Test: `tests/test_backup_io.py`

**Interfaces:**
- Produces:
  - `_COLUMNS: dict[str, tuple[str, ...]]` — schema column order per table.
  - `write_backup(tables: dict[str, pd.DataFrame], backup_dir="backups") -> Path` — writes the 3 CSVs + `manifest.json`; returns the dir Path.
  - `read_backup(backup_dir="backups") -> dict[str, pd.DataFrame]` — reads them back; raises `FileNotFoundError` if a CSV is missing, `ValueError` if a CSV lacks an expected column.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backup_io.py`:

```python
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.backup import write_backup, read_backup, _COLUMNS


def _sample():
    scans = pd.DataFrame({"scan_id": [1, 2], "run_at": ["2026-06-01", "2026-06-02"],
                          "config_hash": ["abc", "def"]})
    scores = pd.DataFrame({
        "scan_id": [1, 1], "region": ["US", "EU"], "gics_sector": ["Technology", "Energy"],
        "level_score": [0.5, 0.3], "change_score": [0.4, 0.1], "data_score": [0.45, 0.2],
        "sentiment_score": [np.nan, 0.2], "composite": [0.45, 0.2], "rank": [1.0, 2.0]})
    signals = pd.DataFrame({
        "scan_id": [1, 1], "region": ["US", "EU"], "gics_sector": ["Technology", "Energy"],
        "signal_name": ["rs_ratio", "rs_ratio"], "raw_value": [103.4, np.nan], "z_value": [0.8, -0.2]})
    return {"scans": scans, "scores": scores, "signals": signals}


def test_roundtrip_preserves_values_including_nan(tmp_path):
    tables = _sample()
    write_backup(tables, tmp_path)
    back = read_backup(tmp_path)
    for name in _COLUMNS:
        pd.testing.assert_frame_equal(
            back[name].reset_index(drop=True),
            tables[name][list(_COLUMNS[name])].reset_index(drop=True),
            check_dtype=False,
        )
    # NaN survived as NaN (becomes NULL on restore)
    assert pd.isna(back["scores"]["sentiment_score"].iloc[0])
    assert pd.isna(back["signals"]["raw_value"].iloc[1])


def test_manifest_has_counts_and_max_scan_id(tmp_path):
    write_backup(_sample(), tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["row_counts"] == {"scans": 2, "scores": 2, "signals": 2}
    assert manifest["max_scan_id"] == 2
    assert "generated_at" in manifest


def test_read_backup_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_backup(tmp_path)  # empty dir


def test_read_backup_missing_column_raises(tmp_path):
    write_backup(_sample(), tmp_path)
    (tmp_path / "scores.csv").write_text("scan_id,region\n1,US\n")  # missing columns
    with pytest.raises(ValueError):
        read_backup(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backup_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.backup'`.

- [ ] **Step 3: Implement the pure functions**

Create `src/backup.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backup_io.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backup.py tests/test_backup_io.py
git commit -m "feat: CSV backup serialization (write_backup/read_backup)"
```

---

### Task 2: `src/backup.py` — DB dump/load + wrappers

**Files:**
- Modify: `src/backup.py` (add DB functions)
- Test: `tests/test_backup_db.py`

**Interfaces:**
- Consumes: `_COLUMNS`, `write_backup`, `read_backup` (Task 1).
- Produces:
  - `dump_tables(conn) -> dict[str, pd.DataFrame]` — read all rows from the 3 tables (deterministic ORDER BY).
  - `load_tables(conn, tables, *, force=False) -> dict[str, int]` — empty-guard, FK-safe insert with explicit `scan_id`, sequence reset, single transaction; returns per-table inserted counts.
  - `backup_database(conn, backup_dir="backups") -> Path` = `write_backup(dump_tables(conn), backup_dir)`.
  - `restore_database(conn, backup_dir="backups", *, force=False) -> dict[str, int]` = `load_tables(conn, read_backup(backup_dir), force=force)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backup_db.py`:

```python
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.backup import load_tables, dump_tables


class _FakeCursor:
    def __init__(self, count_result):
        self.count_result = count_result
        self.executed = []          # list of (sql, params)
        self.executemany_calls = [] # list of (sql, rows)
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return (self.count_result,)
    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, count_result=0):
        self._cur = _FakeCursor(count_result)
    def cursor(self):
        return self._cur
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _tables():
    return {
        "scans": pd.DataFrame({"scan_id": [1], "run_at": ["2026-06-01"], "config_hash": ["abc"]}),
        "scores": pd.DataFrame({"scan_id": [1], "region": ["US"], "gics_sector": ["Technology"],
                                "level_score": [0.5], "change_score": [0.4], "data_score": [0.45],
                                "sentiment_score": [np.nan], "composite": [0.45], "rank": [1.0]}),
        "signals": pd.DataFrame({"scan_id": [1], "region": ["US"], "gics_sector": ["Technology"],
                                 "signal_name": ["rs_ratio"], "raw_value": [103.4], "z_value": [0.8]}),
    }


def test_load_refuses_nonempty_db_without_force():
    conn = _FakeConn(count_result=5)  # DB has rows
    with pytest.raises(RuntimeError, match="not empty"):
        load_tables(conn, _tables(), force=False)


def test_load_into_empty_db_inserts_and_resets_sequence():
    conn = _FakeConn(count_result=0)
    counts = load_tables(conn, _tables(), force=False)
    assert counts == {"scans": 1, "signals": 1, "scores": 1}
    cur = conn._cur
    # NaN sentiment_score was converted to None (NULL)
    scores_rows = [rows for (sql, rows) in cur.executemany_calls if "scores" in sql][0]
    assert scores_rows[0][6] is None  # sentiment_score position
    # sequence reset emitted
    assert any("setval" in sql for sql, _ in cur.executed)


def test_load_force_deletes_first():
    conn = _FakeConn(count_result=9)
    load_tables(conn, _tables(), force=True)
    deletes = [sql for sql, _ in conn._cur.executed if sql.strip().startswith("DELETE")]
    assert any("signals" in d for d in deletes)
    assert any("scores" in d for d in deletes)
    assert any("scans" in d for d in deletes)


def test_dump_tables_queries_all_three(monkeypatch):
    seen = []
    def fake_read_sql(sql, conn):
        seen.append(sql)
        return pd.DataFrame()
    monkeypatch.setattr("src.backup.pd.read_sql_query", fake_read_sql)
    dump_tables(object())
    assert any("FROM scans" in s for s in seen)
    assert any("FROM scores" in s for s in seen)
    assert any("FROM signals" in s for s in seen)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backup_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_tables'`.

- [ ] **Step 3: Implement the DB functions**

Append to `src/backup.py`:

```python
def dump_tables(conn) -> dict[str, pd.DataFrame]:
    """Read every row from the three tables into DataFrames (deterministic order)."""
    order = {
        "scans": "ORDER BY scan_id",
        "scores": "ORDER BY scan_id, region, gics_sector",
        "signals": "ORDER BY scan_id, region, gics_sector, signal_name",
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
            for name in ("scans", "scores", "signals"):
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                if cur.fetchone()[0]:
                    non_empty = True
            if non_empty and not force:
                raise RuntimeError(
                    "target database is not empty; pass force=True (restore.py --force) "
                    "to delete existing rows before restoring"
                )
            if force:
                cur.execute("DELETE FROM signals")
                cur.execute("DELETE FROM scores")
                cur.execute("DELETE FROM scans")
            # FK-safe insert order: scans before its children.
            for name in ("scans", "signals", "scores"):
                cols = _COLUMNS[name]
                rows = _rows_with_nulls(tables[name], cols)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backup_db.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backup.py tests/test_backup_db.py
git commit -m "feat: DB dump/load + backup_database/restore_database"
```

---

### Task 3: Wire backup into `scan.py`

**Files:**
- Modify: `scan.py` (add `--no-backup` flag; call `backup_database` after save)
- Test: `tests/test_scan_smoke.py` (add backup-wiring tests)

**Interfaces:**
- Consumes: `backup_database(conn, backup_dir="backups") -> Path` (Task 2).

- [ ] **Step 1: Read the integration points**

Read `scan.py`. Find: (a) the argparse setup that defines `--no-dashboard` (around the `_parse_args` function); (b) in `run()`, the non-dry-run block where `scan_id = save_scan(...)` is called and `logger.info("Saved scan_id=%d", scan_id)` runs, immediately before the report-writing (`build_ranked_table` / `write_report`). Match on this code, not line numbers.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_scan_smoke.py` (follow the file's existing patching style — it already patches `scan.fetch_prices` etc.). These assert the wiring without a real DB or network:

```python
def test_backup_called_after_successful_save(monkeypatch, tmp_path):
    """run() invokes backup_database once after save_scan, by default."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_database", lambda conn, *a, **k: calls.append(conn) or tmp_path)
    _run_minimal_scan(monkeypatch)  # helper that stubs fetch/score/save; see existing smoke tests
    assert len(calls) == 1


def test_no_backup_flag_skips_backup(monkeypatch, tmp_path):
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_database", lambda conn, *a, **k: calls.append(conn) or tmp_path)
    _run_minimal_scan(monkeypatch, extra_argv=["--no-backup"])
    assert calls == []


def test_backup_failure_is_non_fatal(monkeypatch, tmp_path):
    import scan
    def boom(conn, *a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(scan, "backup_database", boom)
    rc = _run_minimal_scan(monkeypatch)
    assert rc in (0, None)  # scan still completes despite backup failure
```

> Implementer note: if `tests/test_scan_smoke.py` has no reusable `_run_minimal_scan` helper, adapt these to the file's existing scan-invocation pattern (the same stubs used by the current breadth/sentiment non-fatal tests), keeping the three assertions: backup called once by default, skipped with `--no-backup`, and a backup exception not aborting the run.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scan_smoke.py -k backup -v`
Expected: FAIL — `scan` has no attribute `backup_database` / flag unknown.

- [ ] **Step 4: Implement the wiring**

In `scan.py`:

1. Add the module-level import alongside the other `src` imports at the top of `run()`'s import block (so it's patchable as `scan.backup_database`):

```python
    from src.backup import backup_database
```

(If `scan.py` imports `src.*` at module top rather than inside `run()`, add it there instead, matching the file's convention.)

2. In `_parse_args`, after the `--no-dashboard` argument, add:

```python
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing the database backup after the scan.",
    )
```

3. In `run()`, immediately after `logger.info("Saved scan_id=%d", scan_id)` (and before the report-writing block), add:

```python
        if not args.no_backup:
            try:
                backup_database(conn)
                logger.info("Database backup written to backups/")
            except Exception as exc:  # non-fatal: a backup failure must not fail the scan
                logger.warning("Database backup failed (%s) — continuing", exc)
```

(Match the indentation of the surrounding non-dry-run block, and confirm the parsed args object is named `args` in `run()`; if it's passed differently, use the local name.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_scan_smoke.py -k backup -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scan.py tests/test_scan_smoke.py
git commit -m "feat: write DB backup after each scan (non-fatal, --no-backup)"
```

---

### Task 4: `restore.py` CLI

**Files:**
- Create: `restore.py`
- Test: `tests/test_restore_cli.py`

**Interfaces:**
- Consumes: `restore_database(conn, backup_dir="backups", *, force=False) -> dict[str, int]` (Task 2); `init_db()` from `src.state`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_restore_cli.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import restore


def test_main_passes_dir_and_force(monkeypatch, capsys):
    captured = {}
    class _Conn:
        def close(self): pass
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    def fake_restore(conn, backup_dir, *, force):
        captured["dir"] = backup_dir
        captured["force"] = force
        return {"scans": 2, "signals": 4, "scores": 6}
    monkeypatch.setattr(restore, "restore_database", fake_restore)
    monkeypatch.setattr(sys, "argv", ["restore.py", "mybackups", "--force"])
    restore.main()
    assert captured == {"dir": "mybackups", "force": True}
    out = capsys.readouterr().out
    assert "scans" in out and "2" in out


def test_main_defaults(monkeypatch):
    captured = {}
    class _Conn:
        def close(self): pass
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    monkeypatch.setattr(restore, "restore_database",
                        lambda conn, backup_dir, *, force: captured.update(dir=backup_dir, force=force) or {})
    monkeypatch.setattr(sys, "argv", ["restore.py"])
    restore.main()
    assert captured == {"dir": "backups", "force": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_restore_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'restore'`.

- [ ] **Step 3: Implement `restore.py`**

Create `restore.py` at the repo root:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_restore_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add restore.py tests/test_restore_cli.py
git commit -m "feat: restore.py CLI to load a backup into the DB"
```

---

### Task 5: CI — commit `backups/` in the scan workflow

**Files:**
- Modify: `.github/workflows/scan.yml`
- Test: `tests/test_scan_workflow.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scan_workflow.py`:

```python
from pathlib import Path

_WF = Path(__file__).parent.parent / ".github" / "workflows" / "scan.yml"


def test_commit_step_stages_backups():
    text = _WF.read_text()
    assert "git add docs/ backups/" in text, "scan workflow must commit the backups/ dir"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scan_workflow.py -v`
Expected: FAIL — current workflow has `git add docs/`.

- [ ] **Step 3: Update the workflow**

In `.github/workflows/scan.yml`, in the "Commit results" step, change:

```yaml
          git add docs/
```

to:

```yaml
          git add docs/ backups/
```

(Leave the rest of the step — `git diff --staged --quiet || git commit ...` and `git push` — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_scan_workflow.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/scan.yml tests/test_scan_workflow.py
git commit -m "ci: commit backups/ alongside docs/ in the scan workflow"
```

---

### Task 6: Final verification + seed a real backup of the live DB

**Files:**
- Create (generated): `backups/scans.csv`, `backups/scores.csv`, `backups/signals.csv`, `backups/manifest.json`

- [ ] **Step 1: Confirm the destructive state test SKIPs, then run the full suite**

Run: `.venv/bin/pytest tests/test_state_smoke.py -v`
Expected: all SKIP. If any RUN, STOP — do not run the full suite.

Run: `.venv/bin/pytest -q`
Expected: all pass (existing + new backup/restore tests); state-smoke tests SKIP.

- [ ] **Step 2: Generate a real backup from the live DB**

This reads the current production DB (scan 110 data) and writes the first real backup. Read-only on the DB.

```bash
.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv(); from src.state import init_db; from src.backup import backup_database; backup_database(init_db())"
```

Expected: creates `backups/scans.csv`, `backups/scores.csv`, `backups/signals.csv`, `backups/manifest.json`.

- [ ] **Step 3: Verify the backup contents**

```bash
cat backups/manifest.json
head -3 backups/scans.csv backups/scores.csv backups/signals.csv
```

Expected: `manifest.json` shows non-zero row counts and the current `max_scan_id`; the CSVs have the schema header rows.

- [ ] **Step 4: Commit the seed backup**

```bash
git add backups/
git commit -m "chore: seed first DB backup from live scan"
```

---

## Self-Review

**1. Spec coverage:**
- `src/backup.py` pure/DB split → Tasks 1, 2. ✓
- CSV-per-table + manifest, schema column order, `backups/` not gitignored → Task 1 + Global Constraints. ✓
- Backup non-fatal in `scan.py` after save, `--no-backup` → Task 3. ✓
- `restore.py` with empty-guard / `--force` / FK order / sequence reset / transaction / NaN→NULL → Tasks 2 (logic) + 4 (CLI). ✓
- CI commits `backups/` → Task 5. ✓
- Testing (round-trip incl. NaN, manifest, empty-guard, column validation, sequence-reset, non-fatal wiring) → Tasks 1–5. ✓
- DB-touching tests use fakes (no live DB) → Tasks 2, 3, 4. ✓
- Seed a real backup of current state → Task 6. ✓

**2. Placeholder scan:** No TBD/TODO. Task 3's test has an explicit implementer note to adapt to the existing smoke-test harness rather than a placeholder — the three required assertions are concrete.

**3. Type consistency:** `_COLUMNS` keys/order, `write_backup`/`read_backup`/`dump_tables`/`load_tables`/`backup_database`/`restore_database` signatures, and the `force` keyword are consistent across Tasks 1–4. `restore_database(conn, backup_dir, *, force)` matches `restore.py`'s call. Insert order (`scans`→`signals`→`scores`) is FK-safe; delete order (`signals`→`scores`→`scans`) is the reverse. ✓
