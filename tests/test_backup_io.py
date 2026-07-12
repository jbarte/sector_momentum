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
    sentiment_signals = pd.DataFrame({
        "scan_id": [1], "region": ["US"], "gics_sector": ["Technology"],
        "signal_name": ["trend_momentum"], "value": [0.5], "text_value": [np.nan]})
    theme_scores = pd.DataFrame({
        "scan_id": [1], "theme": ["AI"], "level_score": [0.6], "change_score": [0.3],
        "data_score": [0.45], "sentiment_score": [np.nan], "composite": [0.45], "rank": [1.0]})
    theme_signals = pd.DataFrame({
        "scan_id": [1], "theme": ["AI"], "signal_name": ["rs_ratio"],
        "raw_value": [102.0], "z_value": [0.7]})
    return {"scans": scans, "scores": scores, "signals": signals,
            "sentiment_signals": sentiment_signals, "theme_scores": theme_scores,
            "theme_signals": theme_signals}


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
    assert manifest["row_counts"]["scans"] == 2
    assert manifest["row_counts"]["scores"] == 2
    assert manifest["row_counts"]["signals"] == 2
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


def test_read_backup_old_format_without_theme_tables(tmp_path):
    """Pre-theme backups lack the 3 new CSVs — read_backup returns empty DFs."""
    tables = _sample()
    write_backup(tables, tmp_path)
    for name in ("sentiment_signals", "theme_scores", "theme_signals"):
        (tmp_path / f"{name}.csv").unlink()
    back = read_backup(tmp_path)
    for name in ("sentiment_signals", "theme_scores", "theme_signals"):
        assert name in back
        assert len(back[name]) == 0
        assert list(back[name].columns) == list(_COLUMNS[name])


def test_columns_cover_all_ddl_tables():
    """_COLUMNS must have an entry for every CREATE TABLE in state.py DDL."""
    import re
    src = (Path(__file__).parent.parent / "src" / "state.py").read_text()
    ddl_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", src))
    backup_tables = set(_COLUMNS.keys())
    missing = ddl_tables - backup_tables
    assert not missing, f"backup._COLUMNS is missing tables defined in DDL: {sorted(missing)}"
