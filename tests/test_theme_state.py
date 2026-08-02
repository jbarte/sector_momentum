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


def _sentiment_df():
    return pd.DataFrame([
        {"theme": "Space", "signal_name": "news_polarity",
         "value": 0.42, "text_value": None},
    ])


def test_save_theme_scan_dual_writes_scores_to_shared_table():
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df())
    calls = conn._cur.executemany_calls
    shared = next(c for c in calls if "into scores" in c[0].lower())
    # (scan_id, region, gics_sector, level, change, data, sentiment, composite, rank)
    assert shared[1][0][0] == 7
    assert shared[1][0][1] == "THEME"
    assert shared[1][0][2] == "Space"
    assert shared[1][0][7] == 0.8            # composite


def test_save_theme_scan_dual_writes_signals_to_shared_table():
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df())
    shared = next(c for c in conn._cur.executemany_calls
                  if "into signals" in c[0].lower())
    assert shared[1][0] == (7, "THEME", "Space", "rs_ratio", 101.2, 1.3)


def test_save_theme_scan_dual_writes_sentiment_mapping_theme_to_gics_sector():
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df(),
                    sentiment_signals_df=_sentiment_df())
    shared = next(c for c in conn._cur.executemany_calls
                  if "into sentiment_signals" in c[0].lower())
    # (scan_id, region, gics_sector, signal_name, value, text_value)
    assert shared[1][0] == (7, "THEME", "Space", "news_polarity", 0.42, None)


def test_save_theme_scan_still_writes_legacy_theme_tables():
    """Dual-write, not migration: the theme_* inserts must remain until PR 3."""
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df())
    sql = [c[0].lower() for c in conn._cur.executemany_calls]
    assert any("into theme_scores" in s for s in sql)
    assert any("into theme_signals" in s for s in sql)


def test_save_theme_scan_empty_frames_write_nothing_anywhere():
    conn = _FakeConn()
    save_theme_scan(conn, 7, pd.DataFrame(), pd.DataFrame())
    assert conn._cur.executemany_calls == []
