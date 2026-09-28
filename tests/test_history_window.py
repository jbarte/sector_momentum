"""The signed-in window must be the window Python reads.

Guests get the baked board, built from get_scan_history(n_scans=HISTORY_SCANS).
Signed-in readers (web rescore.js, the iOS app) re-derive the same rules from
v_recent_scores. With a SMALLER window the clients' rules were right but their
input was short: the cron runs daily against a five-day market, so Sat/Sun/Mon
replay Friday's close, and the last 6 raw scans held only 4 distinct ones
Mon-Thu -- a 4-point Trend where Python fits 5, and a stuck-pipeline guard
(MAX_DUPLICATE_RUN) no client could ever reach.
Spec: sector_momentum-notes/specs/2026-09-28-signed-in-history-window-design.md.
"""
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.rows import HISTORY_SCANS, _TRAJECTORY_SCANS, distinct_scan_ids

ROOT = Path(__file__).parent.parent
SQL = ROOT / "scripts" / "content_gating_migration.sql"


def _recent_scores_view_sql() -> str:
    """The v_recent_scores statement alone: from its CREATE to the first ';'.
    Comments above it (which may mention other numbers) are excluded."""
    text = SQL.read_text()
    start = text.lower().find("create or replace view public.v_recent_scores")
    assert start != -1, "v_recent_scores definition not found in the migration file"
    end = text.find(";", start)
    assert end != -1, "v_recent_scores definition has no terminating ';'"
    return text[start:end]


def test_view_window_is_pythons_window():
    """The live view is applied by hand from this file (ARCHITECTURE.md: the
    view is managed Supabase-side), so this file is the record to pin."""
    limits = re.findall(r"\blimit\s+(\d+)\b", _recent_scores_view_sql(), re.IGNORECASE)
    assert len(limits) == 1, f"expected exactly one limit in v_recent_scores, got {limits}"
    assert int(limits[0]) == HISTORY_SCANS


def test_view_keeps_its_ten_columns_in_order():
    """create or replace view refuses a changed column list; the web and the
    iOS app both decode exactly these."""
    sql = _recent_scores_view_sql()
    select = sql[sql.lower().index("select"):sql.lower().index("from public.scores")]
    cols = [c.strip().split(".")[-1] for c in select[len("select"):].split(",")]
    assert cols == ["scan_id", "run_at", "region", "gics_sector", "level_score",
                    "change_score", "data_score", "sentiment_score", "composite", "rank"]


def test_build_reads_the_same_window():
    src = (ROOT / "dashboard" / "build.py").read_text()
    assert "get_scan_history(conn, n_scans=HISTORY_SCANS)" in src


def _close_for(scan_day: date) -> date:
    """The 06:00 UTC cron runs before the US close, so a scan scores the
    previous trading day: Sat, Sun and Mon all score Friday."""
    d = scan_day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _distinct_in_window(last_scan_day: date, n_scans: int) -> int:
    """Distinct scans among the n_scans daily scans ending on last_scan_day.
    One theme whose composite encodes the close date, so scans that replay the
    same close fingerprint identically -- as they do in production."""
    days = [last_scan_day - timedelta(days=i) for i in range(n_scans)][::-1]
    df = pd.DataFrame([
        {"scan_id": i + 1, "region": "THEME", "gics_sector": "Alpha",
         "rank": 1.0, "composite": _close_for(d).toordinal() / 1e6}
        for i, d in enumerate(days)
    ])
    return len(distinct_scan_ids(df))


# 2026-09-28 is a Monday; the next six days cover every weekday.
_WEEK = {date(2026, 9, 28) + timedelta(days=i): name
         for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}


def test_the_old_window_of_six_was_short_mon_to_thu():
    """Records the problem: the spec's table, reproduced from the cron rule."""
    got = {name: _distinct_in_window(day, 6) for day, name in _WEEK.items()}
    assert got == {"Mon": 4, "Tue": 4, "Wed": 4, "Thu": 4, "Fri": 5, "Sat": 6, "Sun": 5}


def test_history_scans_always_covers_the_trend_and_the_delta():
    """_TRAJECTORY_SCANS distinct scans for the Trend, plus one more before
    them for the rank delta's previous distinct scan -- on every weekday."""
    for day, name in _WEEK.items():
        assert _distinct_in_window(day, HISTORY_SCANS) >= _TRAJECTORY_SCANS + 1, name
