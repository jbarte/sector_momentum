import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.build import build_scan_index, _generate_scan_reports


def _two_scans():
    rows = []
    for sid, run_at, comps in [
        (1, "2026-06-01T06:00:00", [0.5, 0.2]),
        (2, "2026-06-02T06:00:00", [0.7, 0.1]),
    ]:
        for i, (sector, comp) in enumerate(zip(["Technology", "Energy"], comps)):
            rows.append(dict(scan_id=sid, run_at=run_at, region="US", gics_sector=sector,
                             level_score=comp, change_score=comp, data_score=comp,
                             sentiment_score=0.0, composite=comp, rank=float(i + 1)))
    return pd.DataFrame(rows)


def test_scan_index_newest_first_with_top_sector():
    idx = build_scan_index(_two_scans())
    assert [r["scan_id"] for r in idx] == [2, 1]          # newest first
    assert idx[0]["sector_count"] == 2
    assert idx[0]["top_sector"] == "Technology"           # rank 1
    assert "2026-06-02" in idx[0]["run_at_display"]


def test_scan_index_empty():
    assert build_scan_index(pd.DataFrame()) == []


def test_generate_reports_one_file_per_scan(tmp_path):
    written = _generate_scan_reports(_two_scans(), tmp_path, swedish_tickers_path="config/swedish_tickers.csv")
    assert sorted(written) == [1, 2]
    assert (tmp_path / "report_1.md").exists()
    assert (tmp_path / "report_2.md").exists()
    # scan 2 report includes the rankings header and is non-empty
    txt = (tmp_path / "report_2.md").read_text()
    assert "Sector Momentum Report" in txt and "## Rankings" in txt
