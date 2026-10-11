"""The review panel's "Book: n / target" line says what the reader HOLDS.

It used to print the book AFTER the review's planned buys (`book.picks.length`),
so a reader holding 1 of 4 themes saw "Book: 4 / 4 — refills at the next
review", and an unbuyable top theme (whose slot stays empty and is never
refilled) still promised a refill. `Rescore.bookLine` is now a pure function so
the rule is testable here and portable to the iOS app, which mirrors it
(etf-momentum-ios ReviewCard) -- the two panels must not disagree.

Runs the real rescore.js under Node, like test_select_book_parity.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_RESCORE_JS = Path(__file__).parent.parent / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _book(over=0, blocked=()):
    """The fields of selectBook()'s result that bookLine reads."""
    return {"overHeld": over, "blocked": list(blocked)}


def _held(n):
    return [f"H{i}" for i in range(n)]


def _line(book, held, target, due):
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      console.log(JSON.stringify(R.bookLine({json.dumps(book)}, {json.dumps(held)}, {target}, {json.dumps(due)})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


def test_a_full_book_just_states_what_is_held():
    assert _line(_book(), _held(4), 4, due=False) == {"text": "Book: 4 / 4", "kind": ""}


def test_free_slots_between_reviews_say_they_fill_at_the_next_review():
    out = _line(_book(), _held(1), 4, due=False)
    assert out == {"text": "Book: 1 / 4 — 3 free slots, they fill at the next review",
                   "kind": "under"}


def test_one_free_slot_is_singular():
    out = _line(_book(), _held(3), 4, due=False)
    assert out["text"] == "Book: 3 / 4 — 1 free slot, it fills at the next review"


def test_an_unbuyable_theme_keeps_its_slot_empty_and_the_line_does_not_promise_a_refill():
    """The reader's real board: 3 held of 4, and the fourth slot belongs to a
    theme they cannot buy in the EU."""
    out = _line(_book(blocked=["Shipping"]), _held(3), 4, due=False)
    assert out == {"text": "Book: 3 / 4 — 1 free slot, but it is not buyable, so it stays empty",
                   "kind": "under"}


def test_several_unbuyable_slots_are_plural():
    out = _line(_book(blocked=["X1", "X2"]), _held(2), 4, due=False)
    assert out["text"] == "Book: 2 / 4 — 2 free slots, but they are not buyable, so they stay empty"


def test_a_mix_of_fillable_and_unbuyable_slots_says_both():
    out = _line(_book(blocked=["X1"]), _held(1), 4, due=False)
    assert out["text"] == ("Book: 1 / 4 — 3 free slots, 2 fill at the next review, "
                           "1 stays empty (not buyable)")


def test_exactly_one_fillable_slot_uses_the_singular_verb():
    out = _line(_book(blocked=["X1"]), _held(2), 4, due=False)
    assert out["text"] == ("Book: 2 / 4 — 2 free slots, 1 fills at the next review, "
                           "1 stays empty (not buyable)")


def test_a_held_unbuyable_theme_occupies_its_slot_so_it_is_not_an_empty_one():
    """selectBook lists an unbuyable theme the reader already holds under
    `blocked` too, but its slot is taken: the free slot is a normal fillable one."""
    out = _line(_book(blocked=["H0"]), _held(3), 4, due=False)
    assert out["text"] == "Book: 3 / 4 — 1 free slot, it fills at the next review"


def test_a_holding_below_the_exit_line_is_still_held_until_the_review_sells_it():
    """4 of 4 held, one of them will be sold at the review: the count says 4
    and does not invent a free slot (review finding: held and free slots came
    from different bases and read "4 / 4 — 1 free slot")."""
    assert _line(_book(), _held(4), 4, due=False) == {"text": "Book: 4 / 4", "kind": ""}


def test_when_a_review_is_due_the_actions_speak_and_the_count_has_no_suffix():
    assert _line(_book(), _held(1), 4, due=True) == {"text": "Book: 1 / 4", "kind": ""}
    assert _line(_book(blocked=["X"]), _held(3), 4, due=True)["text"] == "Book: 3 / 4"


def test_an_over_held_book_is_flagged_whether_or_not_a_review_is_due():
    for due in (False, True):
        out = _line(_book(over=1), _held(5), 4, due=due)
        assert out == {"text": "Book: 5 / 4 — too many; nothing trims this automatically",
                       "kind": "over"}


def test_an_empty_book_between_reviews():
    out = _line(_book(), [], 4, due=False)
    assert out["text"] == "Book: 0 / 4 — 4 free slots, they fill at the next review"


def test_missing_held_keys_read_as_none_held():
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      console.log(JSON.stringify(R.bookLine({{overHeld: 0, blocked: []}}, undefined, 4, false)));
    """
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True).stdout)
    assert out["text"].startswith("Book: 0 / 4")
