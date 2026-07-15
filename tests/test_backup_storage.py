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
        "sentiment_signals": pd.DataFrame([{"scan_id": 1, "region": "US", "gics_sector": "Energy",
                                            "signal_name": "trend_momentum", "value": 0.5}]),
        "theme_scores": pd.DataFrame([{"scan_id": 1, "theme": "AI", "level_score": 0.6,
                                       "change_score": 0.3, "data_score": 0.45,
                                       "sentiment_score": 0.0, "composite": 0.45, "rank": 1.0}]),
        "theme_signals": pd.DataFrame([{"scan_id": 1, "theme": "AI", "signal_name": "rs_ratio",
                                        "raw_value": 102.0, "z_value": 0.7}]),
        "theme_sentiment_signals": pd.DataFrame([{"scan_id": 1, "theme": "AI",
                                                  "signal_name": "momentum", "value": 0.9,
                                                  "text_value": None}]),
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
    assert {"scans.csv", "scores.csv", "signals.csv", "sentiment_signals.csv",
            "theme_scores.csv", "theme_signals.csv", "manifest.json"} <= names


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
    assert {"scans", "scores", "signals"} <= set(loaded["tables"])
    assert loaded["force"] is True


def test_restore_from_storage_empty_bucket_raises(monkeypatch):
    monkeypatch.setattr(bk.storage_backup, "list_objects", lambda bucket="db-backups": [])
    with pytest.raises(RuntimeError):
        bk.restore_from_storage(conn=object())
