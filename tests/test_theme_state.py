import pandas as pd
from src.state import save_theme_scan
from src import state


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


def test_save_theme_scan_no_longer_writes_legacy_tables():
    """The dual-write scaffolding is removed — the shared tables are the only
    destination. (Superseded test_save_theme_scan_still_writes_legacy_theme_tables,
    which pinned the temporary dual-write during cohort-unification PR 1.)"""
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df(),
                    sentiment_signals_df=_sentiment_df())
    sql = [c[0].lower() for c in conn._cur.executemany_calls]
    assert not any("into theme_scores" in s for s in sql)
    assert not any("into theme_signals" in s for s in sql)
    assert not any("into theme_sentiment_signals" in s for s in sql)
    # and the shared writes are still there
    assert any("into scores" in s for s in sql)
    assert any("into signals" in s for s in sql)
    assert any("into sentiment_signals" in s for s in sql)


def test_save_theme_scan_empty_frames_write_nothing_anywhere():
    conn = _FakeConn()
    save_theme_scan(conn, 7, pd.DataFrame(), pd.DataFrame())
    assert conn._cur.executemany_calls == []


def _capture(monkeypatch) -> dict:
    """Stub _read_sql and record the SQL + params it was handed."""
    seen: dict = {}

    def _fake(conn, q, p=None):
        seen["q"] = " ".join(q.lower().split())
        seen["p"] = p
        return pd.DataFrame()

    monkeypatch.setattr(state, "_read_sql", _fake)
    return seen


def test_theme_scan_history_reads_shared_table(monkeypatch):
    seen = _capture(monkeypatch)
    state.get_theme_scan_history(_FakeConn())
    assert "from theme_scores" not in seen["q"], "still reading the legacy table"
    assert "from scores" in seen["q"]
    assert ["THEME"] in list(seen["p"]), "not filtered to the THEME cohort"


def test_theme_rrg_history_reads_shared_table(monkeypatch):
    seen = _capture(monkeypatch)
    state.get_theme_rrg_history(_FakeConn(), n_scans=6)
    assert "from theme_signals" not in seen["q"], "still reading the legacy table"
    assert "from signals" in seen["q"]
    assert ["THEME"] in list(seen["p"]), "not filtered to the THEME cohort"


def test_theme_history_selects_the_contracted_columns(monkeypatch):
    """The dashboard indexes this frame by name, so the SELECT list is the API.

    Asserted against the SQL, not against a stubbed frame: a stub that returns
    the expected columns would pass no matter what the query selected.
    """
    seen = _capture(monkeypatch)
    state.get_theme_scan_history(_FakeConn())
    for col in ("scan_id", "run_at", "region", "gics_sector", "level_score",
                "change_score", "data_score", "sentiment_score", "composite", "rank"):
        assert col in seen["q"], f"{col} missing from the theme history SELECT"


def test_theme_signals_reads_shared_table_and_keeps_theme_column(monkeypatch):
    """dashboard/rows.py filters this frame with signals_df["theme"] — the
    column name is a contract, and the shared table calls it gics_sector."""
    seen = _capture(monkeypatch)
    state.get_theme_signals_for_latest_scan(_FakeConn())
    assert "from theme_signals" not in seen["q"], "still reading the legacy table"
    assert "from signals" in seen["q"]
    assert "as theme" in seen["q"], "gics_sector must be aliased back to theme"
    assert ["THEME"] in list(seen["p"]), "not filtered to the THEME cohort"


def test_dead_theme_scores_reader_is_gone():
    """get_theme_scores_for_latest_scan had zero callers; it was removed rather
    than ported to a table that PR 3 retires."""
    assert not hasattr(state, "get_theme_scores_for_latest_scan")


def test_theme_signals_query_is_deterministically_ordered(monkeypatch):
    """_latest_scan_query had no ORDER BY. That was invisible while it only
    ever read the small, theme-only theme_signals table in isolation — but
    this reader now shares the much larger `signals` table with 25 sectors,
    where an unordered query genuinely returned rows in a different order than
    a production baseline captured before this PR (caught 2026-08-02 by a
    byte-for-byte equivalence check against that baseline). The fix belongs in
    the shared helper, not a per-reader special case."""
    seen = _capture(monkeypatch)
    state.get_theme_signals_for_latest_scan(_FakeConn())
    assert "order by t.region, t.gics_sector, t.signal_name" in seen["q"]
