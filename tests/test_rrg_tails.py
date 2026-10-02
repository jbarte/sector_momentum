"""Rotation-chart (RRG) tails are drawn from distinct scans, not raw scan ids.

The daily cron replays Friday's close on Sat/Sun/Mon, so a tail built from the
last 6 RAW scans stacked repeated points on one spot and covered only ~4 real
days -- the same duplicate-scan problem dashboard/rows.py:distinct_scan_ids
fixed for the leaderboard (and the 2026-09-28 history-window fix for the
signed-in view). Each theme's tail now keeps its last RRG_TAIL_SCANS DISTINCT
points, fetched from a HISTORY_SCANS-wide window so there are always enough.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.figures import RRG_TAIL_SCANS, _build_rrg_figure
from dashboard.rows import HISTORY_SCANS

ROOT = Path(__file__).parent.parent


def _rrg(points_by_scan) -> pd.DataFrame:
    """points_by_scan: [(scan_id, {theme: (rs_ratio, rs_momentum)}), ...]."""
    return pd.DataFrame([
        {"scan_id": sid, "run_at": f"2026-01-{sid:02d}T06:00:00+00:00",
         "region": "THEME", "gics_sector": theme,
         "rs_ratio": x, "rs_momentum": y}
        for sid, pts in points_by_scan for theme, (x, y) in pts.items()
    ])


def _week_with_replays(n_scans: int) -> pd.DataFrame:
    """n_scans daily scans for two themes where every 7-day block replays one
    reading three times (Sat/Sun/Mon repeating Friday's close)."""
    rows, value = [], 0
    for sid in range(1, n_scans + 1):
        if sid % 7 not in (2, 3):   # two of every seven scans repeat the previous one
            value += 1
        rows.append((sid, {"Alpha": (100 + value * 0.1, 100 - value * 0.05),
                           "Bravo": (99 - value * 0.1, 101 + value * 0.05)}))
    return _rrg(rows)


def _tail_points(fig_json: str, theme_x_now: float) -> int:
    """Points in the tail trace that ends at the theme's current position."""
    data = json.loads(fig_json)["data"]
    tails = [t for t in data if t.get("mode") == "lines+markers"]
    match = [t for t in tails if t["x"] and abs(t["x"][-1] - theme_x_now) < 1e-9]
    assert len(match) == 1, "expected exactly one tail ending at the theme"
    return len(match[0]["x"])


def test_tail_skips_replayed_scans_and_keeps_six_distinct_points():
    df = _week_with_replays(HISTORY_SCANS)
    alpha = df[df["gics_sector"] == "Alpha"]
    assert len(set(zip(alpha["rs_ratio"], alpha["rs_momentum"]))) > RRG_TAIL_SCANS
    latest_x = df[df["scan_id"] == df["scan_id"].max()]
    alpha_now = latest_x[latest_x["gics_sector"] == "Alpha"]["rs_ratio"].iloc[0]

    points = _tail_points(_build_rrg_figure(df), alpha_now)

    assert points == RRG_TAIL_SCANS


def test_tail_points_are_all_different():
    df = _week_with_replays(HISTORY_SCANS)
    data = json.loads(_build_rrg_figure(df))["data"]
    for t in (t for t in data if t.get("mode") == "lines+markers"):
        pts = list(zip(t["x"], t["y"]))
        assert len(pts) == len(set(pts)), "a tail repeats a point"


def test_one_themes_missing_reading_does_not_bring_back_the_others_replays():
    """Found in review: deduplicating whole scans across themes broke when one
    theme lacked a reading on a replay day (its row is dropped as NaN), which
    made every replayed scan look distinct and stacked the other themes'
    points again. Deduplicating per theme is immune to that."""
    df = _week_with_replays(HISTORY_SCANS)
    replay_scan = 16                                  # 16 % 7 == 2: a replay
    df.loc[(df["scan_id"] == replay_scan) & (df["gics_sector"] == "Bravo"),
           ["rs_ratio", "rs_momentum"]] = float("nan")
    data = json.loads(_build_rrg_figure(df))["data"]
    tails = [t for t in data if t.get("mode") == "lines+markers"]
    assert tails
    for t in tails:
        pts = list(zip(t["x"], t["y"]))
        assert len(pts) == len(set(pts)), "a tail repeats a point"
        assert len(pts) == RRG_TAIL_SCANS


def test_build_fetches_the_rrg_window_as_wide_as_the_history_window():
    """Both fetches -- the live one and the guest one anchored at lb_scan_id --
    must be HISTORY_SCANS wide, or a weekend leaves fewer than
    RRG_TAIL_SCANS distinct scans to draw."""
    src = (ROOT / "dashboard" / "build.py").read_text()
    assert "get_rrg_history(conn, n_scans=HISTORY_SCANS)" in src
    assert "get_rrg_history(conn, n_scans=HISTORY_SCANS, end_scan_id=lb_scan_id)" in src
    assert "get_rrg_history(conn, n_scans=6" not in src
