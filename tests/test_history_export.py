import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.build import build_scan_index, _generate_scan_reports
from src.cohorts import cohorts


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


def _mixed_cohort_scan():
    """One scan with US, EU and THEME rows — mirrors what all_scores_df now
    carries after regions=None widened it (cohort-unification PR 4/5)."""
    rows = []
    entries = [
        ("US", "Technology", 1.0, 1.0),
        ("US", "Energy", 0.5, 2.0),
        ("EU", "Financials", 0.8, 3.0),
        ("THEME", "Defense", 0.9, 4.0),
        ("THEME", "Quantum Computing", 0.6, 5.0),
    ]
    for region, sector, comp, rank in entries:
        rows.append(dict(scan_id=1, run_at="2026-06-01T06:00:00", region=region,
                         gics_sector=sector, level_score=comp, change_score=comp,
                         data_score=comp, sentiment_score=0.0, composite=comp, rank=rank))
    return pd.DataFrame(rows)


def test_scan_index_counts_only_sector_rows_from_mixed_cohort_frame():
    """Regression test for the PR5 cohort leak: all_scores_df is widened to
    include THEME rows (regions=None), but build_scan_index is a sector-only
    surface (Global Constraint 2). build_scan_index performs no cohort
    filtering of its own — it counts every row of whatever frame it's
    handed — so dashboard/build.py MUST filter all_scores_df down to
    sector regions (via cohorts(_universe), no themes_cfg) before calling
    it. This pins both halves of that contract: the unfiltered call leaks
    THEME rows into sector_count, and the filtered call (mirroring what
    build.py now does) does not.
    """
    mixed = _mixed_cohort_scan()

    # Handing build_scan_index the raw mixed-cohort frame reproduces the
    # PR5 bug: sector_count includes the 2 THEME rows (5, not 3).
    leaked_idx = build_scan_index(mixed)
    assert leaked_idx[0]["sector_count"] == 5

    # The fix: scope to the configured cohorts before calling. Readers default
    # to the THEME cohort now, but the retired US/EU rows are still in the table,
    # so an unscoped frame still has to be filtered.
    themes_cfg = {"benchmark": "ACWI", "themes": {"Space": {"ticker": "UFO"}}}
    cohort_regions = tuple(c.region for c in cohorts(themes_cfg))
    scoped = mixed[mixed["region"].isin(cohort_regions)]

    idx = build_scan_index(scoped)
    assert len(idx) == 1
    assert idx[0]["top_region"] == "THEME"
    assert idx[0]["sector_count"] < leaked_idx[0]["sector_count"]


def test_generate_reports_one_file_per_scan(tmp_path):
    themes_cfg = {"benchmark": "ACWI", "themes": {
        "Technology": {"ticker": "XLK"}, "Energy": {"ticker": "XLE"},
    }}
    written = _generate_scan_reports(_two_scans(), tmp_path, cohorts(themes_cfg))
    assert sorted(written) == [1, 2]
    assert (tmp_path / "report_1.md").exists()
    assert (tmp_path / "report_2.md").exists()
    # scan 2 report includes the rankings header and is non-empty
    txt = (tmp_path / "report_2.md").read_text()
    assert "Sector Momentum Report" in txt and "## Rankings" in txt
