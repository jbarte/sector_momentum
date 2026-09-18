"""Parity: rescore.js todaysRead() must match dashboard/digest.py todays_read().

The "Today's read" cell was baked from the gated scan and never updated when a
signed-in reader's leaderboard went live, so the headline named a leader from
the lagged scan above a table showing today's (2026-09-18: "AgTech & Food
Innovation leads the board" over a live table led by Shipping). The fix
recomputes the cell's facts client-side from the live rows, which makes
todaysRead() a second implementation of todays_read() -- and this test is what
stops the two drifting apart, the same job test_rescore_parity.py does for the
badge rule.

Each fixture is written once and translated into the row shape each side
actually receives: Python gets dashboard/rows.py leaderboard rows (`sector`,
`rank`, `_raw_change`); JS gets v_recent_scores rows (`gics_sector`, `rank`,
`change_score`), which is what auth.js hands the page on upgrade.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dashboard.digest import DRIFT_EPS, todays_read

_RESCORE_JS = Path(__file__).parent.parent / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _js_todays_read(rows):
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      console.log(JSON.stringify(R.todaysRead({json.dumps(rows)})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


def _both(fixture):
    """fixture: list of (name, rank, change). Returns (python, js) results."""
    py_rows = [{"sector": n, "rank": r, "_raw_change": c} for n, r, c in fixture]
    js_rows = [{"gics_sector": n, "rank": r, "change_score": c} for n, r, c in fixture]
    return todays_read(py_rows), _js_todays_read(js_rows)


def _board(changes):
    """A board ranked 1..N with the given change scores, in rank order."""
    return [(f"T{i}", i, c) for i, c in enumerate(changes, start=1)]


_FIXTURES = {
    # The live board from the bug report, reduced to what the cell reads.
    "falling, 18 themes": _board([0.9, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2,
                                  -0.3, -0.4, -0.5, -0.6, -0.7, -0.8, -0.9, -1.0, -1.1]),
    "rising": _board([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
    "flat, inside the dead band": _board([1.0, 1.0, 0.02, 0.04]),
    # The band is strict on both sides: exactly +/-DRIFT_EPS is still flat.
    "exactly +DRIFT_EPS is flat": _board([0.0, DRIFT_EPS, DRIFT_EPS]),
    "exactly -DRIFT_EPS is flat": _board([0.0, -DRIFT_EPS, -DRIFT_EPS]),
    "just past +DRIFT_EPS": _board([0.0, 0.06, 0.06]),
    # Odd count: the middle row belongs to the bottom half.
    "odd count of 3": _board([-5.0, 0.3, 0.3]),
    # One row: max(1, ...) keeps the leader out of its own bottom half.
    "single theme": _board([0.9]),
    "two themes": _board([-0.9, 0.9]),
    # Missing change values are skipped, not treated as zero.
    "missing changes skipped": _board([0.0, None, 0.4, None]),
    "every change missing": _board([0.0, None, None]),
    # Rows without a usable rank never lead and never count.
    "unranked rows dropped": [("A", None, -9.0), ("B", 1, 0.0), ("C", 2, 0.3)],
    # Input order must not matter: the leader is found by rank.
    "shuffled input": [("C", 3, 0.2), ("A", 1, 0.0), ("D", 4, 0.2), ("B", 2, 0.2)],
    # Tied ranks: both sorts are stable, so the first row listed leads.
    "tie at the top": [("B", 1.5, 0.0), ("A", 1.5, 0.0), ("C", 3, -0.4)],
}


@pytest.mark.parametrize("label", sorted(_FIXTURES))
def test_js_matches_python(label):
    py, js = _both(_FIXTURES[label])
    assert js == py, f"{label}: python={py} js={js}"


def test_fixtures_exercise_every_drift_state():
    """Guards the parametrize above against passing vacuously: if every
    fixture landed on one state, a JS port that hardcoded that state would
    still pass."""
    states = {_both(f)[0]["drift"] for f in _FIXTURES.values()}
    assert states == {"rising", "falling", "flat"}


def test_empty_and_leaderless_boards_have_nothing_to_say():
    """No ranked rows, or a leader with no name: Python returns None, which the
    template reads as "don't render the cell". JS must return null, not a
    half-filled object the page would then write into the headline."""
    assert _both([]) == (None, None)
    assert _both([("", 1, 0.0), ("B", 2, 0.0)]) == (None, None)
