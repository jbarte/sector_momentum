"""Theme Google Trends sentiment: symbol map, entity overrides, persistence,
and dashboard-row reuse. Mirrors the sector sentiment tests."""
import pandas as pd

from src.data.trends_symbols import build_theme_symbol_map, load_theme_entities
from src.state import save_theme_scan
from dashboard.build import _build_sentiment_signal_rows


# ---------------------------------------------------------------------------
# build_theme_symbol_map
# ---------------------------------------------------------------------------

def test_build_theme_symbol_map_uses_trends_terms():
    cfg = {
        "themes": {"Uranium & Nuclear": "URA", "Semiconductors": "SOXX"},
        "trends": {"Uranium & Nuclear": "uranium", "Semiconductors": "semiconductor"},
    }
    m = build_theme_symbol_map(cfg)
    assert m == {
        "THEME|Uranium & Nuclear": ["uranium"],
        "THEME|Semiconductors": ["semiconductor"],
    }


def test_build_theme_symbol_map_falls_back_to_theme_name():
    cfg = {"themes": {"Biotech": "XBI"}, "trends": {}}
    m = build_theme_symbol_map(cfg)
    assert m == {"THEME|Biotech": ["Biotech"]}


def test_build_theme_symbol_map_empty_when_no_themes():
    assert build_theme_symbol_map({}) == {}


# ---------------------------------------------------------------------------
# load_theme_entities
# ---------------------------------------------------------------------------

def test_load_theme_entities_keeps_nonempty_mids():
    cfg = {"trends_entities": {"space exploration": "/m/abc", "uranium": ""}}
    assert load_theme_entities(cfg) == {"space exploration": "/m/abc"}


def test_load_theme_entities_missing_section_is_empty():
    assert load_theme_entities({"themes": {"Biotech": "XBI"}}) == {}


# ---------------------------------------------------------------------------
# save_theme_scan — theme_sentiment_signals persistence
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self):
        self.executemany_calls = []

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
         "data_score": 0.8, "sentiment_score": 1.2, "composite": 0.8, "rank": 1.0},
    ])


def test_save_theme_scan_persists_sentiment_signals():
    conn = _FakeConn()
    sent_df = pd.DataFrame([
        {"theme": "Space", "signal_name": "momentum", "value": 1.2, "text_value": None},
        {"theme": "Space", "signal_name": "attention_level", "value": 44.0, "text_value": None},
        {"theme": "Space", "signal_name": "rising_queries", "value": None,
         "text_value": '[{"query": "spacex", "growth": "Breakout"}]'},
    ])
    save_theme_scan(conn, 9, _scores_df(), pd.DataFrame(), sentiment_signals_df=sent_df)

    calls = conn._cur.executemany_calls
    sent_call = next(c for c in calls if "theme_sentiment_signals" in c[0])
    rows = sent_call[1]
    # (scan_id, theme, signal_name, value, text_value)
    assert rows[0] == (9, "Space", "momentum", 1.2, None)
    assert rows[1] == (9, "Space", "attention_level", 44.0, None)
    assert rows[2][:4] == (9, "Space", "rising_queries", None)
    assert rows[2][4] == '[{"query": "spacex", "growth": "Breakout"}]'


def test_save_theme_scan_omits_sentiment_when_absent():
    conn = _FakeConn()
    save_theme_scan(conn, 9, _scores_df(), pd.DataFrame())
    assert not any("theme_sentiment_signals" in c[0] for c in conn._cur.executemany_calls)


# ---------------------------------------------------------------------------
# Dashboard row builder reuse on THEME-aliased rows
# ---------------------------------------------------------------------------

def test_build_sentiment_signal_rows_on_theme_aliased_frame():
    df = pd.DataFrame([
        {"region": "THEME", "gics_sector": "Uranium & Nuclear", "signal_name": "momentum", "value": 0.9},
        {"region": "THEME", "gics_sector": "Uranium & Nuclear", "signal_name": "attention_level", "value": 72.5},
        {"region": "THEME", "gics_sector": "Uranium & Nuclear", "signal_name": "seasonal_ratio", "value": 1.4},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["sector"] == "Uranium & Nuclear"
    assert rows[0]["region"] == "THEME"
    assert rows[0]["attention"] == "72.5"
    assert rows[0]["seasonal_ratio"] == "1.40x"
