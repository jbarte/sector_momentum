import pandas as pd
from src.state import save_theme_scan


class _FakeCursor:
    def __init__(self):
        self.executemany_calls = []            # list of (sql, rows)

    def execute(self, sql, params=None):
        pass

    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self._cur = _FakeCursor()

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scores_df():
    return pd.DataFrame([
        {"region": "THEME", "gics_sector": "Space", "level_score": 1.0, "change_score": 0.5,
         "data_score": 0.8, "sentiment_score": None, "composite": 0.8, "rank": 1.0},
        {"region": "THEME", "gics_sector": "Semiconductors", "level_score": -0.5, "change_score": 0.2,
         "data_score": -0.1, "sentiment_score": None, "composite": -0.1, "rank": 2.0},
    ])


def _signals_df():
    return pd.DataFrame([
        {"region": "THEME", "gics_sector": "Space", "signal_name": "rs_ratio",
         "raw_value": 101.2, "z_value": 1.3},
    ])


def test_save_theme_scan_shapes_rows_with_theme_from_gics_sector():
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df())
    calls = conn._cur.executemany_calls
    score_call = next(c for c in calls if "theme_scores" in c[0])
    sig_call = next(c for c in calls if "theme_signals" in c[0])
    # scores: (scan_id, theme, level, change, data, sentiment, composite, rank)
    assert score_call[1][0][0] == 7                       # scan_id
    assert score_call[1][0][1] == "Space"                 # theme == gics_sector
    assert score_call[1][0][6] == 0.8                     # composite
    # signals: (scan_id, theme, signal_name, raw_value, z_value)
    assert sig_call[1][0] == (7, "Space", "rs_ratio", 101.2, 1.3)


def test_save_theme_scan_empty_frames_no_insert():
    conn = _FakeConn()
    save_theme_scan(conn, 7, pd.DataFrame(), pd.DataFrame())
    assert conn._cur.executemany_calls == []              # nothing inserted
