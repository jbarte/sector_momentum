import pandas as pd
from dashboard.build import _build_theme_leaderboard_rows


def _scores():
    return pd.DataFrame([
        {"theme": "Space", "level_score": 1.0, "change_score": 0.5, "data_score": 0.8,
         "sentiment_score": None, "composite": 0.8, "rank": 2.0},
        {"theme": "Semiconductors", "level_score": 1.5, "change_score": 0.9, "data_score": 1.2,
         "sentiment_score": None, "composite": 1.2, "rank": 1.0},
    ])


def _signals():
    return pd.DataFrame([
        {"theme": "Space", "signal_name": "rs_ratio", "raw_value": 101.0, "z_value": 0.4},
        {"theme": "Semiconductors", "signal_name": "rs_ratio", "raw_value": 104.0, "z_value": 1.1},
    ])


def test_theme_rows_sorted_by_rank_with_breakdown():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Semiconductors": "SOXX"}}
    rows = _build_theme_leaderboard_rows(_scores(), _signals(), cfg, weights={})
    assert [r["theme"] for r in rows] == ["Semiconductors", "Space"]   # rank 1 first
    assert rows[0]["rank"] == 1
    assert "SOXX" in rows[0]["breakdown_html"]      # theme ETF surfaced in breakdown
    assert rows[0]["sector_id"] == "THEME-Semiconductors"


def test_theme_rows_empty_input():
    assert _build_theme_leaderboard_rows(pd.DataFrame(), pd.DataFrame(), {}, {}) == []
