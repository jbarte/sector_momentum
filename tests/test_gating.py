"""Tests for dashboard.gating — leaderboard lag logic."""
from __future__ import annotations

import pandas as pd

from dashboard.gating import _pick_lagged_scan, apply_leaderboard_lag

NOW = pd.Timestamp("2026-07-21T12:00:00Z")


def _history(days_ago_by_scan: dict[int, int]) -> pd.DataFrame:
    """Build a minimal history_df: one row per scan_id, run_at = NOW - days_ago."""
    rows = []
    for scan_id, days_ago in days_ago_by_scan.items():
        run_at = (NOW - pd.Timedelta(days=days_ago)).isoformat()
        rows.append({"scan_id": scan_id, "run_at": run_at,
                     "region": "US", "gics_sector": "Tech", "rank": 1.0,
                     "composite": 0.5})
    return pd.DataFrame(rows)


def test_pick_newest_scan_at_least_lag_old():
    # scans at 0, 5, 8, 14 days old; lag_days=7 -> newest with age>=7 is the 8-day one
    hist = _history({1: 14, 2: 8, 3: 5, 4: 0})
    assert _pick_lagged_scan(hist, lag_days=7, now=NOW) == 2


def test_pick_fallback_oldest_when_none_old_enough():
    # all scans younger than 7 days -> fall back to the oldest (scan 1, 5 days)
    hist = _history({1: 5, 2: 3, 3: 1})
    assert _pick_lagged_scan(hist, lag_days=7, now=NOW) == 1


def test_pick_none_on_empty():
    assert _pick_lagged_scan(pd.DataFrame(), lag_days=7, now=NOW) is None


def test_apply_lag_filters_history_and_returns_date():
    hist = _history({1: 14, 2: 8, 3: 5, 4: 0})
    lag_df, scan_id, banner_date = apply_leaderboard_lag(
        hist, lag_active=True, lag_days=7, now=NOW)
    assert scan_id == 2
    assert set(lag_df["scan_id"].unique()) == {1, 2}   # <= lagged id
    assert banner_date == "2026-07-13"                  # NOW - 8 days


def test_apply_lag_inactive_returns_latest_and_no_banner():
    hist = _history({1: 14, 2: 8, 3: 5, 4: 0})
    lag_df, scan_id, banner_date = apply_leaderboard_lag(
        hist, lag_active=False, lag_days=7, now=NOW)
    assert scan_id == 4                                 # max scan_id
    assert banner_date is None
    assert len(lag_df) == len(hist)                     # unfiltered


def test_apply_lag_empty_history():
    lag_df, scan_id, banner_date = apply_leaderboard_lag(
        pd.DataFrame(), lag_active=True, now=NOW)
    assert scan_id is None
    assert banner_date is None
    assert lag_df.empty
