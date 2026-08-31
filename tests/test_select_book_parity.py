"""Parity: rescore.js selectBook() must match strategy._select() exactly.

The dashboard has only ever known the BAND rule (rank -> Enter/Hold/Exit) and
never the BOOK rule (how many slots are free). That gap is why the page
rendered a green "Enter" on a board with zero free slots, which is what
prompted this work. selectBook closes it -- and this test is what stops the
two implementations drifting apart again, the same job
tests/test_rescore_parity.py does for the badge rule.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.backtest.strategy import _select
from src.horizons import default_horizon

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"
_H = default_horizon()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _js_select_book(ranked, held, unbuyable):
    """Run selectBook under Node and return its parsed result."""
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      const out = R.selectBook(
        {json.dumps(ranked)}, {json.dumps(sorted(held))},
        {{top_n: {_H.top_n}, buffer_frac: {_H.buffer_frac}}},
        {json.dumps(sorted(unbuyable))});
      console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


def _py_picks(ranked, held, unbuyable):
    """The Python reference: _select, then simulate()'s unbuyable drop."""
    picks = _select(ranked, set(held), _H.top_n, _H.buffer_frac)
    return [sk for sk in picks if sk not in unbuyable]


# Ranks are 1-based in the UI but 0-based inside _select; these fixtures are
# plain ordered lists so the two never have to agree about that separately.
_RANKED = [f"R{i}" for i in range(1, 19)]   # R1 best .. R18 worst


@pytest.mark.parametrize("held,unbuyable,label", [
    (set(),                                 set(),      "empty book"),
    ({"R1", "R3", "R5", "R8"},              set(),      "correct book of 4"),
    ({"R1", "R3", "R4", "R5", "R8"},        set(),      "OVER-held 5"),
    ({"R1", "R2", "R3", "R4", "R5", "R8"},  set(),      "OVER-held 6"),
    ({"R1", "R8"},                          set(),      "UNDER-held 2"),
    ({"R1", "R3", "R5", "R12"},             set(),      "one past the sell line"),
    ({"R1", "R3"},                          {"R2"},     "unbuyable wins a freed slot"),
    ({"R1", "R3", "R5", "R8"},              {"R2"},     "unbuyable, no free slot"),
    ({"R1", "GONE"},                        set(),      "held name absent from this scan"),
])
def test_select_book_matches_python(held, unbuyable, label):
    js = _js_select_book(_RANKED, held, unbuyable)
    assert js["picks"] == _py_picks(_RANKED, held, unbuyable), label


def test_over_held_book_is_not_trimmed():
    """The asymmetry the over-held warning exists for: under-holding refills
    at the next rebalance, over-holding is never trimmed -- `free` goes
    negative so nothing is added AND nothing is removed. simulate() always
    starts empty and so never over-holds, meaning an over-held book is a state
    the backtest has never priced."""
    js = _js_select_book(_RANKED, {"R1", "R3", "R4", "R5", "R8"}, set())
    assert len(js["picks"]) == 5, "over-held book was silently trimmed"
    assert js["overHeld"] == 1
    assert js["freeSlots"] == 0
    assert js["surplus"] == ["R8"], "surplus must name the WORST-ranked holding"


def test_under_held_book_refills_from_the_top():
    js = _js_select_book(_RANKED, {"R1", "R8"}, set())
    assert js["freeSlots"] == 2
    assert js["buys"] == ["R2", "R3"]
    assert len(js["picks"]) == 4


def test_unbuyable_slot_is_left_empty_not_passed_down():
    """simulate()'s documented rule: substituting rank N+1 measured WORSE in
    all three presets on both CAGR and Sharpe, while skipping measured better."""
    js = _js_select_book(_RANKED, {"R1", "R3"}, {"R2"})
    assert "R2" not in js["picks"], "unbuyable name was booked"
    assert js["blocked"] == ["R2"], "blocked must name it so the panel can explain"
    assert "R5" not in js["picks"], "slot was passed down instead of left empty"


def test_holding_past_the_sell_line_is_sold():
    js = _js_select_book(_RANKED, {"R1", "R3", "R5", "R12"}, set())
    assert js["sells"] == ["R12"]
    assert len(js["buys"]) == 1
