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
