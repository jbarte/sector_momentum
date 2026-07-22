"""End-to-end backup→restore drill against a disposable Postgres database.

Exercises the real-DB paths that unit tests (test_backup_io/db/storage, which use
fake connections) never touch: dump_tables' SELECTs, load_tables' FK-safe DELETEs,
executemany inserts, NaN→NULL conversion, and the scan_id sequence reset.

Gated on TEST_DATABASE_URL exactly like test_state_smoke.py — skips unless a
dedicated, disposable test DB is configured, and never touches production
(identity-aware guard + _assert_disposable teardown backstop).
"""
import io
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backup import (
    _ARCHIVE_MEMBERS,
    _COLUMNS,
    _DELETE_ORDER,
    dump_tables,
    load_tables,
    read_backup,
    write_backup,
)
from src.state import init_db
from tests.test_state_smoke import _assert_disposable, _same_database

_test_db = os.environ.get("TEST_DATABASE_URL")
_prod_db = os.environ.get("DATABASE_URL", "")
skipif_no_db = pytest.mark.skipif(
    not _test_db or _same_database(_test_db, _prod_db),
    reason="TEST_DATABASE_URL not set (or resolves to the same database as "
           "DATABASE_URL); this test wipes all rows and must never run against "
           "production",
)


@pytest.fixture
def db_conn(monkeypatch):
    """Connection to the disposable TEST database; wipes all seven tables on
    teardown (FK-safe order). Never touches production (see test_state_smoke)."""
    prod_url = os.environ.get("DATABASE_URL", "")
    test_url = os.environ["TEST_DATABASE_URL"]
    if _same_database(test_url, prod_url):
        pytest.skip("TEST_DATABASE_URL resolves to the production database")
    monkeypatch.setenv("DATABASE_URL", test_url)
    conn = init_db()
    try:
        yield conn
    finally:
        try:
            _assert_disposable(conn, prod_url)
            with conn:
                with conn.cursor() as cur:
                    for name in _DELETE_ORDER:
                        cur.execute(f"DELETE FROM {name}")
        finally:
            conn.close()


def _seed_tables():
    """A small known fixture covering all seven tables, including a NaN
    (→ SQL NULL) in scores.sentiment_score and a text_value in a theme
    sentiment row."""
    return {
        "scans": pd.DataFrame({
            "scan_id": [1],
            "run_at": ["2026-06-01T06:00:00+00:00"],
            "config_hash": ["deadbeef"],
        }),
        "scores": pd.DataFrame({
            "scan_id": [1, 1],
            "region": ["US", "US"],
            "gics_sector": ["Technology", "Energy"],
            "level_score": [0.5, -0.2],
            "change_score": [0.4, -0.3],
            "data_score": [0.45, -0.25],
            "sentiment_score": [np.nan, 0.1],  # NaN → NULL round-trip
            "composite": [0.45, -0.25],
            "rank": [1.0, 2.0],
        }),
        "signals": pd.DataFrame({
            "scan_id": [1],
            "region": ["US"],
            "gics_sector": ["Technology"],
            "signal_name": ["rs_ratio"],
            "raw_value": [103.4],
            "z_value": [0.8],
        }),
        "sentiment_signals": pd.DataFrame({
            "scan_id": [1],
            "region": ["US"],
            "gics_sector": ["Technology"],
            "signal_name": ["news_polarity"],
            "value": [0.3],
            "text_value": [None],
        }),
        "theme_scores": pd.DataFrame({
            "scan_id": [1],
            "theme": ["Semiconductors"],
            "level_score": [0.6],
            "change_score": [0.5],
            "data_score": [0.55],
            "sentiment_score": [np.nan],
            "composite": [0.55],
            "rank": [1.0],
        }),
        "theme_signals": pd.DataFrame({
            "scan_id": [1],
            "theme": ["Semiconductors"],
            "signal_name": ["rs_ratio"],
            "raw_value": [101.2],
            "z_value": [0.4],
        }),
        "theme_sentiment_signals": pd.DataFrame({
            "scan_id": [1],
            "theme": ["Semiconductors"],
            "signal_name": ["news_polarity"],
            "value": [0.2],
            "text_value": ["ai chip demand"],  # non-null text_value round-trip
        }),
    }


@skipif_no_db
def test_backup_restore_roundtrip_preserves_all_tables(db_conn, tmp_path):
    # 1. Seed the disposable DB with a known fixture (force clears any leftovers).
    load_tables(db_conn, _seed_tables(), force=True)

    # 2. Dump the seeded DB.
    before = dump_tables(db_conn)

    # 3. Backup to a temp dir, then zip + unzip (mirrors the Storage path minus I/O).
    backup_dir = tmp_path / "backup"
    write_backup(before, backup_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in _ARCHIVE_MEMBERS:
            zf.write(backup_dir / member, arcname=member)
    unzipped = tmp_path / "unzipped"
    unzipped.mkdir()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        zf.extractall(unzipped)

    # 4. Read the backup back and restore into the (force-cleared) DB.
    restored = read_backup(unzipped)
    load_tables(db_conn, restored, force=True)

    # 5. Re-dump and assert row-for-row equality per table. dump_tables applies a
    #    deterministic ORDER BY, so both dumps are already sorted identically.
    after = dump_tables(db_conn)
    for name in _COLUMNS:
        pdt.assert_frame_equal(
            before[name].reset_index(drop=True),
            after[name].reset_index(drop=True),
            check_dtype=False,  # CSV round-trip may widen int→float; values matter
            obj=name,
        )


@skipif_no_db
def test_restore_resets_scan_id_sequence(db_conn):
    """After a restore, the scan_id sequence continues past the restored max,
    so the next save_scan does not collide."""
    from src.state import save_scan

    load_tables(db_conn, _seed_tables(), force=True)  # max scan_id = 1
    signals_df = pd.DataFrame(
        [{"region": "US", "gics_sector": "Technology",
          "signal_name": "rs_ratio", "raw_value": 1.0, "z_value": 0.0}]
    )
    scores_df = pd.DataFrame({
        "region": ["US"], "gics_sector": ["Technology"],
        "level_score": [0.1], "change_score": [0.1], "data_score": [0.1],
        "sentiment_score": [float("nan")], "composite": [0.1], "rank": [1.0],
    })
    from datetime import datetime, timezone
    new_id = save_scan(
        conn=db_conn,
        run_at=datetime.now(timezone.utc),
        region_sector_signals=signals_df,
        scores_df=scores_df,
    )
    assert new_id > 1, "sequence should advance past the restored max scan_id"
