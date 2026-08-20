import pandas as pd
from dashboard.build import _compute_rank_trajectories


def _history_two_scans():
    # scan 1 (older): Space rank 1, Semis rank 2. scan 2 (newer): Semis rank 1, Space rank 2.
    return pd.DataFrame([
        {"scan_id": 1, "run_at": "2026-07-06", "region": "THEME", "gics_sector": "Space",
         "level_score": 1.0, "change_score": 0.5, "data_score": 0.8, "sentiment_score": None,
         "composite": 1.0, "rank": 1.0},
        {"scan_id": 1, "run_at": "2026-07-06", "region": "THEME", "gics_sector": "Semiconductors",
         "level_score": 0.4, "change_score": 0.3, "data_score": 0.5, "sentiment_score": None,
         "composite": 0.5, "rank": 2.0},
        {"scan_id": 2, "run_at": "2026-07-07", "region": "THEME", "gics_sector": "Space",
         "level_score": 0.9, "change_score": 0.2, "data_score": 0.6, "sentiment_score": None,
         "composite": 0.6, "rank": 2.0},
        {"scan_id": 2, "run_at": "2026-07-07", "region": "THEME", "gics_sector": "Semiconductors",
         "level_score": 1.5, "change_score": 0.9, "data_score": 1.2, "sentiment_score": None,
         "composite": 1.2, "rank": 1.0},
    ])


def test_compute_rank_trajectories_produces_theme_prefixed_keys():
    # Locks the integration contract: _compute_rank_trajectories keys its output
    # "region|gics_sector", and get_theme_scan_history reads rows whose region
    # column holds "THEME", so the row-builder's lookup key f"THEME|{theme}"
    # must match what this produces.
    traj = _compute_rank_trajectories(_history_two_scans())
    assert "THEME|Semiconductors" in traj
    assert "THEME|Space" in traj
    assert traj["THEME|Semiconductors"]["state"] == "up"   # rank 2 -> 1 over 2 scans (slope -1.0)


def test_one_builder_produces_rows_for_every_cohort():
    """Theme rows now live in `scores` with region='THEME', so the sector row
    builder handles them — same shape, including region and the merged
    Level/Change cell."""
    import pandas as pd
    from dashboard.rows import _build_leaderboard_rows

    df = pd.DataFrame([
        {"scan_id": 2, "run_at": "2026-08-04", "region": "US",
         "gics_sector": "Energy", "level_score": 0.5, "change_score": 0.4,
         "data_score": 0.45, "sentiment_score": 0.1, "composite": 0.45, "rank": 1.0},
        {"scan_id": 2, "run_at": "2026-08-04", "region": "THEME",
         "gics_sector": "Space", "level_score": 0.9, "change_score": 0.2,
         "data_score": 0.8, "sentiment_score": None, "composite": 0.8, "rank": 1.0},
    ])
    rows, _ = _build_leaderboard_rows(df)

    by_region = {r["region"]: r for r in rows}
    assert set(by_region) == {"US", "THEME"}
    assert by_region["THEME"]["sector"] == "Space"
    assert "level_change_bars" in by_region["THEME"]


def test_theme_row_builder_is_gone():
    """Superseded by _build_leaderboard_rows once themes joined the shared table."""
    from dashboard import rows
    assert not hasattr(rows, "_build_theme_leaderboard_rows")
