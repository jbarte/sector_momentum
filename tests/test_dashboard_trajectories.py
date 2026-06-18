"""Tests for rank trajectory computation in dashboard/build.py."""
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import _compute_rank_trajectories


def _make_history(ranks_us_tech, ranks_us_fin=None):
    """Build a minimal history_df with given rank sequences for US|Technology."""
    rows = []
    for i, r in enumerate(ranks_us_tech):
        rows.append({
            "scan_id": i + 1,
            "run_at": f"2026-06-{10 + i:02d}T12:00:00",
            "region": "US",
            "gics_sector": "Technology",
            "rank": r,
        })
    if ranks_us_fin:
        for i, r in enumerate(ranks_us_fin):
            rows.append({
                "scan_id": i + 1,
                "run_at": f"2026-06-{10 + i:02d}T12:00:00",
                "region": "US",
                "gics_sector": "Financials",
                "rank": r,
            })
    df = pd.DataFrame(rows)
    # Add required columns with dummy values
    for col in ["level_score", "change_score", "data_score", "sentiment_score", "composite"]:
        df[col] = 0.0
    return df


def test_strong_up_trajectory():
    # Rank 8→6→4→2→1: improving fast (slope ≈ -1.75)
    df = _make_history([8, 6, 4, 2, 1])
    result = _compute_rank_trajectories(df)
    assert "US|Technology" in result
    assert result["US|Technology"]["label"] == "↑↑"
    assert result["US|Technology"]["state"] == "strong_up"
    assert result["US|Technology"]["slope"] < -1.5


def test_up_trajectory():
    # Rank 5→4→4→3→3: gentle improvement (slope ≈ -0.5)
    df = _make_history([5, 4, 4, 3, 3])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["label"] == "↑"
    assert result["US|Technology"]["state"] == "up"


def test_flat_trajectory():
    # Rank 3→3→3→3→3: no change
    df = _make_history([3, 3, 3, 3, 3])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["label"] == "→"
    assert result["US|Technology"]["state"] == "flat"
    assert result["US|Technology"]["slope"] == pytest.approx(0.0)


def test_down_trajectory():
    # Rank 3→3→4→4→5: gentle decline
    df = _make_history([3, 3, 4, 4, 5])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["label"] == "↓"
    assert result["US|Technology"]["state"] == "down"


def test_strong_down_trajectory():
    # Rank 1→2→4→6→8: deteriorating fast (slope ≈ +1.75)
    df = _make_history([1, 2, 4, 6, 8])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["label"] == "↓↓"
    assert result["US|Technology"]["state"] == "strong_down"
    assert result["US|Technology"]["slope"] > 1.5


def test_single_scan_returns_flat():
    # Only one scan — can't compute slope
    df = _make_history([3])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["label"] == "→"


def test_empty_history_returns_empty_dict():
    df = pd.DataFrame(columns=["scan_id", "run_at", "region", "gics_sector",
                                "rank", "level_score", "change_score",
                                "data_score", "sentiment_score", "composite"])
    result = _compute_rank_trajectories(df)
    assert result == {}


def test_uses_last_five_scans_only():
    # 8 scans total, first 3 are bad (rank 15→14→13), last 5 are good
    ranks = [15, 14, 13, 5, 4, 3, 2, 1]
    df = _make_history(ranks)
    result = _compute_rank_trajectories(df)
    # Should show up because it uses only the last 5 scans
    # (last 5: [5,4,3,2,1] = slope ≈ -1.0, which is between -1.5 and -0.3)
    assert result["US|Technology"]["state"] == "up"


def test_multiple_sectors_independent():
    df = _make_history([1, 1, 1, 1, 1], ranks_us_fin=[5, 4, 3, 2, 1])
    result = _compute_rank_trajectories(df)
    assert result["US|Technology"]["state"] == "flat"
    assert result["US|Financials"]["state"] == "up"
