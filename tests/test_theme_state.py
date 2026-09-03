import pandas as pd
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


# ---------------------------------------------------------------------------
# get_rrg_history(end_scan_id=...) — the empty-RRG-for-guests fix
# ---------------------------------------------------------------------------
#
# Found 2026-08-23: dashboard/build.py fetched the newest 6 scans, then
# filtered them down to `scan_id <= lb_scan_id` for gated guests. LAG_DAYS=7
# against daily scans puts lb_scan_id ~7 scans behind the newest, so 6 < 7
# meant the filter discarded every row every time — the RRG tab rendered
# empty for every guest, as a steady state rather than an edge case.
# end_scan_id anchors the window at the scan actually being rendered instead.


def test_rrg_history_end_scan_id_anchors_the_window(monkeypatch):
    """`end_scan_id` must reach the SQL as the window's endpoint, not just as
    an extra unused parameter — asserted against the query text and the
    param tuple, the same way test_theme_rrg_history_reads_shared_table
    above pins the SELECT."""
    seen = _capture(monkeypatch)
    state.get_rrg_history(_FakeConn(), n_scans=6, end_scan_id=163)
    assert "scan_id <= %s" in seen["q"], (
        "end_scan_id is not being applied as an upper bound in the SQL"
    )
    assert 163 in seen["p"], "end_scan_id (163) never reached the query params"
    assert 6 in seen["p"], "n_scans (6) missing from the query params"


def test_rrg_history_without_end_scan_id_is_unchanged(monkeypatch):
    """The default (no end_scan_id) path — used by every non-gated build —
    must keep querying the true newest n_scans, not silently pick up a
    scan_id <= bound with no value to compare against."""
    seen = _capture(monkeypatch)
    state.get_rrg_history(_FakeConn(), n_scans=6)
    assert "scan_id <= %s" not in seen["q"]
    assert "order by scan_id desc limit %s" in seen["q"]
    assert seen["p"][0] == 6


def test_theme_rrg_history_forwards_end_scan_id(monkeypatch):
    """The THEME-cohort wrapper must pass end_scan_id through, or the fix
    above is unreachable from the one call site that actually needs it
    (dashboard/build.py uses get_rrg_history directly today, but the wrapper
    exists for exactly this shape of call and must not silently drop the
    argument)."""
    seen = _capture(monkeypatch)
    state.get_theme_rrg_history(_FakeConn(), n_scans=6, end_scan_id=163)
    assert 163 in seen["p"]
    assert ["THEME"] in list(seen["p"]), "not filtered to the THEME cohort"


def test_recent_scan_filter_end_scan_id_param_order():
    """Direct unit test on the helper itself: params must be
    (end_scan_id, n_scans), matching the SQL's `<= %s ... LIMIT %s` order.
    Swapped params would silently cap the scan COUNT at end_scan_id's value
    and the scan ID at n_scans — wrong in a way no query-text assertion
    catches, since both are just %s placeholders."""
    condition, params = state._recent_scan_filter(6, end_scan_id=163)
    assert params == (163, 6)
    assert "scan_id <= %s" in condition
    assert "LIMIT %s" in condition
    # <= appears before LIMIT, matching the (end_scan_id, n_scans) param order
    assert condition.index("<=") < condition.index("LIMIT")


def test_recent_scan_filter_end_scan_id_none_is_the_old_behavior():
    condition, params = state._recent_scan_filter(6, end_scan_id=None)
    assert params == (6,)
    assert "scan_id <=" not in condition
