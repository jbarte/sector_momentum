"""Parity: rescore.js latestRowMeta() must match the server's leaderboard rules.

Signed-in readers get their leaderboard rebuilt client-side from
`v_recent_scores` (the last 6 raw scans), and latestRowMeta() computes each
row's rank delta and Trend from those rows. It compared against the previous
RAW scan and fitted the slope over the last 5 RAW scans -- the exact bug
dashboard/rows.py:distinct_scan_ids() fixed on the server. The cron runs 7 days
a week against a 5-day market, so Saturday, Sunday and Monday replay Friday's
close: every signed-in delta read "—" on those three days, and the Trend was
diluted toward flat, while guests on the same page saw the correct values.

The Python side here is the real server code (`_build_leaderboard_rows`,
`_compute_rank_trajectories`, `_compute_setup` with build.py's universe size),
not a reference re-implementation, so this test fails if either side moves.

Each fixture is a list of scans, oldest first; each scan maps theme ->
(rank, composite). Both sides receive the same rows, in the shape each
actually gets: Python a history DataFrame, JS v_recent_scores rows.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from dashboard.rows import (
    MAX_DUPLICATE_RUN,
    TRAJECTORY_WORDS,
    _build_leaderboard_rows,
    _compute_rank_trajectories,
    _compute_setup,
)
from src.horizons import default_horizon

_H = default_horizon()
_RESCORE_JS = Path(__file__).parent.parent / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _records(scans):
    return [
        {"scan_id": i + 1, "run_at": f"2026-09-{1 + i:02d}T06:00:00+00:00",
         "region": "THEME", "gics_sector": name, "rank": rank, "composite": comp,
         "level_score": 0.0, "change_score": 0.0,
         "data_score": 0.0, "sentiment_score": 0.0}
        for i, scan in enumerate(scans)
        for name, (rank, comp) in scan.items()
    ]


def _py_meta(scans):
    df = pd.DataFrame(_records(scans))
    rows, _ = _build_leaderboard_rows(df)
    trajectories = _compute_rank_trajectories(df)
    out = {}
    for row in rows:
        key = f"{row['region']}|{row['sector']}"
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        # Same universe as build.py: the latest scan's row count.
        _compute_setup(row, _H, universe_size=len(rows))
        out[key] = {
            "delta_rank": row["delta_rank"], "arrow": row["arrow"],
            "arrow_class": row["arrow_class"],
            "trajectory_label": traj["label"], "trajectory_state": traj["state"],
            "trajectory_word": TRAJECTORY_WORDS.get(traj["state"], "flat"),
            "setup": row["setup"],
        }
    return out


def _js_meta(scans):
    # NaN is not JSON; v_recent_scores sends a missing value as null.
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            for r in _records(scans)]
    horizon = {"top_n": _H.top_n, "buffer_frac": _H.buffer_frac}
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      console.log(JSON.stringify(R.latestRowMeta({json.dumps(rows)}, {json.dumps(horizon)})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


def _board(ranks, comp=0.5):
    """Themes T1..Tn at the given ranks, composites derived so they differ."""
    return {f"T{i}": (r, comp - r / 10) for i, r in enumerate(ranks, start=1)}


_WED = _board([1, 2, 3, 4])
_THU = _board([2, 1, 3, 4])
_FRI = _board([4, 1, 2, 3])        # the close Sat/Sun/Mon all replay
_TUE = _board([3, 1, 4, 2])

# 18 themes ranked 1..18, and two more that dropped out of the universe before
# the latest scan. exit_rank(18) != exit_rank(20) for the default horizon, so
# counting keys across the whole window instead of the latest scan's rows moves
# the exit band -- see test_universe_fixture_moves_the_exit_band.
_UNIVERSE_18 = _board(range(1, 19))
_UNIVERSE_20 = {**_board(range(1, 19), comp=0.4), "Gone1": (19, -9.0), "Gone2": (20, -9.1)}

_FIXTURES = {
    "all distinct": [_WED, _THU, _FRI, _TUE],
    # The bug: v_recent_scores on a Monday is Wed..Mon, and Sat/Sun/Mon are
    # byte-identical replays of Friday. Delta must be Fri vs Thu, not "—".
    "saturday": [_WED, _THU, _FRI, _FRI],
    "sunday": [_WED, _THU, _FRI, _FRI, _FRI],
    "monday": [_WED, _WED, _THU, _FRI, _FRI, _FRI],
    "tuesday after a weekend": [_THU, _FRI, _FRI, _FRI, _TUE],
    # Trend over the last 5 DISTINCT scans. T1 distinct 7,5,3,1 is a slope of
    # -2 (surging); the raw last 5, 5,3,1,1,1, dilute it to -1 (rising).
    "trend not diluted by replays": [
        _board([7, 1]), _board([5, 2]), _board([3, 4]), _board([1, 5]),
        _board([1, 5]), _board([1, 5])],
    "single scan": [_WED],
    "every scan identical": [_WED] * 6,
    # A composite-only change is a real observation even with ranks unchanged:
    # the latest scan is distinct, so the delta is against Wed ("—"), not Thu.
    "composite moved, ranks did not": [_THU, _WED, _board([1, 2, 3, 4], comp=0.7)],
    # MAX_DUPLICATE_RUN counts replays across the window: at the limit the
    # delta still shows, one past it the pipeline is presumed stuck.
    "duplicates at the guard limit": [_WED] + [_THU] * (MAX_DUPLICATE_RUN + 1),
    "duplicates past the guard limit": [_WED] + [_THU] * (MAX_DUPLICATE_RUN + 2),
    # Themes entering or leaving: a new theme has no previous rank.
    "theme added in the latest scan": [_WED, {**_THU, "New": (5, -1.0)}],
    "theme missing from the previous scan": [_WED, {"T1": (1, 0.3)}, _THU],
    "missing ranks": [_board([1, float("nan"), 3]), _board([2, 1, float("nan")])],
    # Raw OLS slopes of +/-0.29999999999999993: flat unrounded, up/down once
    # rounded to 3 dp as _compute_rank_trajectories does.
    "slope rounds onto +0.3": [_board([r]) for r in (1, 5, 2, 8, 1)],
    "slope rounds onto -0.3": [_board([r]) for r in (1, 8, 2, 5, 1)],
    "universe is the latest scan": [_UNIVERSE_20, _UNIVERSE_20, _UNIVERSE_18],
}


@pytest.mark.parametrize("label", sorted(_FIXTURES))
def test_js_matches_python(label):
    scans = _FIXTURES[label]
    py, js = _py_meta(scans), _js_meta(scans)
    assert js == py, f"{label}:\n python={py}\n     js={js}"


def test_weekend_fixtures_show_a_real_delta():
    """Guards the parametrize against passing vacuously: if the server itself
    read "—" on a weekend (i.e. both sides were raw-scan), parity would hold
    and prove nothing. Friday moved T1 from 2nd to 4th."""
    for label in ("saturday", "sunday", "monday"):
        assert _py_meta(_FIXTURES[label])["THEME|T1"]["delta_rank"] == "-2.0", label


def test_trend_fixture_is_surging_not_rising():
    meta = _py_meta(_FIXTURES["trend not diluted by replays"])
    assert meta["THEME|T1"]["trajectory_state"] == "strong_up"


def test_rounding_fixtures_leave_flat():
    for label, state in (("slope rounds onto +0.3", "down"),
                         ("slope rounds onto -0.3", "up")):
        assert _py_meta(_FIXTURES[label])["THEME|T1"]["trajectory_state"] == state


def test_guard_fixtures_straddle_the_limit():
    assert _py_meta(_FIXTURES["duplicates at the guard limit"])["THEME|T1"]["delta_rank"] != "—"
    assert _py_meta(_FIXTURES["duplicates past the guard limit"])["THEME|T1"]["delta_rank"] == "—"


def test_universe_fixture_moves_the_exit_band():
    """The fixture only discriminates if 18 and 20 themes give different exit
    ranks; if the default horizon ever changes so they don't, re-pick sizes."""
    assert _H.exit_rank(18) != _H.exit_rank(20)
    rank = _H.exit_rank(18) + 1          # exit on 18 themes, hold on 20
    assert _py_meta(_FIXTURES["universe is the latest scan"])[f"THEME|T{rank}"]["setup"] == "exit"


def test_empty_rows():
    assert _js_meta([]) == {}
