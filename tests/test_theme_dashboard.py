import pandas as pd
from dashboard.build import _build_theme_leaderboard_rows


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


def _signals():
    return pd.DataFrame([
        {"theme": "Space", "signal_name": "rs_ratio", "raw_value": 101.0, "z_value": 0.4},
        {"theme": "Semiconductors", "signal_name": "rs_ratio", "raw_value": 104.0, "z_value": 1.1},
    ])


def test_theme_rows_deltas_and_trajectory():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Semiconductors": "SOXX"}}
    traj = {"THEME|Semiconductors": {"label": "↑↑", "state": "strong_up"},
            "THEME|Space": {"label": "↓", "state": "down"}}
    rows = _build_theme_leaderboard_rows(_history_two_scans(), _signals(), cfg, weights={}, trajectories=traj)
    assert [r["theme"] for r in rows] == ["Semiconductors", "Space"]   # sorted by latest rank
    semis = rows[0]
    assert semis["rank"] == 1
    assert semis["arrow"] == "▲"                       # 2 -> 1, improved
    assert semis["delta_rank"] == "+1.0"
    assert semis["emerging"] is True                    # rank up AND composite up (0.5 -> 1.2)
    assert semis["trajectory_label"] == "↑↑"
    assert semis["trajectory_state"] == "strong_up"
    space = rows[1]
    assert space["arrow"] == "▼"                         # 1 -> 2, dropped
    assert "SOXX" in semis["breakdown_html"]            # breakdown still rendered


def test_theme_rows_single_scan_no_delta():
    hist = _history_two_scans()
    hist = hist[hist["scan_id"] == 2]                    # only the latest scan
    rows = _build_theme_leaderboard_rows(hist, _signals(), {}, {}, {})
    assert all(r["delta_rank"] == "—" and r["arrow"] == "" for r in rows)
    assert all(r["trajectory_label"] == "→" for r in rows)   # default flat when no traj passed


def test_theme_rows_empty_history():
    assert _build_theme_leaderboard_rows(pd.DataFrame(), pd.DataFrame(), {}, {}, {}) == []
