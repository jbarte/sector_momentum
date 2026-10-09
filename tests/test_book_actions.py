"""book_actions: what a review asks of the reader's book.

It is the Python reference for the OUTPUTS of rescore.js selectBook() and the
iOS app's selectBook; strategy._select stays the reference for WHO is kept.
Published as the `book` section of band-fixture.json (dashboard/band_fixture.py).
Spec: notes/specs/2026-10-08-ios-holdings-review-design.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.strategy import _select, book_actions
from src.horizons import horizons

RANKED = [f"R{i}" for i in range(1, 19)]          # R1 best .. R18 worst
N = len(RANKED)


@pytest.fixture(params=horizons(), ids=lambda h: h.key)
def h(request):
    return request.param


def _run(h, held, unbuyable=()):
    return book_actions(RANKED, held, h.top_n, h.buffer_frac, unbuyable)


def test_an_empty_book_fills_the_top_slots(h):
    out = _run(h, [])
    assert out["buys"] == RANKED[:h.top_n]
    assert out["picks"] == RANKED[:h.top_n]
    assert out["sells"] == [] and out["blocked"] == [] and out["surplus"] == []
    assert out["free_slots"] == h.top_n and out["over_held"] == 0


def test_a_healthy_book_changes_nothing(h):
    out = _run(h, RANKED[:h.top_n])
    assert out["buys"] == [] and out["sells"] == []
    assert out["picks"] == RANKED[:h.top_n]
    assert out["free_slots"] == 0 and out["over_held"] == 0


def test_a_holding_inside_the_hold_range_is_not_displaced_by_new_leaders(h):
    """The "can't hold 8" case: a full book of names ranked below the buy
    band but inside the exit rank keeps every slot, even with better names
    available. A book therefore never grows past top_n by itself."""
    exit_rank = h.exit_rank(N)
    held = RANKED[h.top_n:exit_rank][:h.top_n]
    assert len(held) == h.top_n                      # the band is wide enough
    out = _run(h, held)
    assert out["buys"] == [] and out["sells"] == []
    assert out["picks"] == held
    assert out["free_slots"] == 0


def test_the_exit_rank_itself_is_kept_and_one_past_it_is_sold(h):
    exit_rank = h.exit_rank(N)
    base = RANKED[:h.top_n - 1]
    on = _run(h, base + [f"R{exit_rank}"])
    assert on["sells"] == [] and on["buys"] == []
    past = _run(h, base + [f"R{exit_rank + 1}"])
    assert past["sells"] == [f"R{exit_rank + 1}"]
    assert past["buys"] == [f"R{h.top_n}"]
    assert past["free_slots"] == 1


def test_an_unbuyable_theme_wins_the_slot_and_the_slot_stays_empty(h):
    """simulate()'s rule: skipping measured better than substituting rank N+1.
    The unbuyable name is `blocked`, not a buy, and nothing is passed down."""
    out = _run(h, ["R1", "R3"], ["R2"])
    assert out["blocked"] == ["R2"]
    assert "R2" not in out["picks"] and "R2" not in out["buys"]
    assert out["buys"] == [f"R{i}" for i in range(4, h.top_n + 1)]
    assert out["picks"] == ["R1", "R3"] + out["buys"]
    assert out["free_slots"] == h.top_n - 2


def test_an_unbuyable_theme_the_reader_already_holds_is_blocked_not_sold(h):
    out = _run(h, RANKED[:h.top_n], ["R2"])
    assert out["blocked"] == ["R2"]
    assert out["sells"] == [] and out["buys"] == []
    assert "R2" not in out["picks"]


def test_an_over_held_book_is_never_trimmed_and_the_surplus_is_worst_first(h):
    one = _run(h, RANKED[:h.top_n + 1])
    assert one["over_held"] == 1 and one["free_slots"] == 0
    assert one["surplus"] == [f"R{h.top_n + 1}"]
    assert one["sells"] == [] and one["buys"] == []
    assert len(one["picks"]) == h.top_n + 1
    two = _run(h, RANKED[:h.top_n + 2])
    assert two["over_held"] == 2
    assert two["surplus"] == [f"R{h.top_n + 2}", f"R{h.top_n + 1}"]


def test_a_held_theme_missing_from_the_scan_is_sold(h):
    out = _run(h, ["GONE", "R1"])
    assert out["sells"] == ["GONE"]
    assert out["buys"] == RANKED[1:h.top_n]


def test_who_is_kept_is_selects_answer(h):
    for held in ([], RANKED[:h.top_n], ["R1", "R3"], RANKED[:h.top_n + 2],
                 ["R1", "R3", "R5", f"R{h.exit_rank(N) + 1}"], ["GONE", "R1"]):
        for unbuyable in ([], ["R2"]):
            out = _run(h, held, unbuyable)
            kept = set(_select(RANKED, set(held), h.top_n, h.buffer_frac))
            assert set(out["picks"]) | set(out["blocked"]) == kept


def test_input_order_and_duplicates_do_not_change_the_answer(h):
    held = ["R1", "R3", "R12", "R14"]
    assert _run(h, held) == _run(h, list(reversed(held)))
    assert _run(h, held) == _run(h, held + held)


def test_sells_are_rank_ordered_and_unranked_names_come_last_by_name(h):
    out = _run(h, ["ZED", "R17", "GONE", "R16"])
    assert out["sells"] == ["R16", "R17", "GONE", "ZED"]
