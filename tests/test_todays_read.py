"""The derived facts behind the summary strip's "Today's Read" cell.

Copy lives in the template; this function returns only facts, so a wrong
sentence can only ever come from a wrong fact — which these tests pin.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.digest import DRIFT_EPS, todays_read


def _row(rank, sector, change):
    return {"rank": rank, "sector": sector, "_raw_change": change}


def test_names_the_rank_one_theme():
    rows = [_row(1, "Cybersecurity", 0.5), _row(2, "Uranium", 0.1)]
    assert todays_read(rows)["lead_theme"] == "Cybersecurity"


def test_lead_theme_is_by_rank_not_by_list_order():
    """build.py hands over rows in whatever order the merge produced; rank is
    the only thing that decides which theme leads."""
    rows = [_row(3, "Space", 0.1), _row(1, "Biotech", 0.2), _row(2, "Defense", 0.0)]
    assert todays_read(rows)["lead_theme"] == "Biotech"


def test_drift_falling_when_bottom_half_mean_change_is_negative():
    rows = [_row(1, "A", 1.0), _row(2, "B", 1.0), _row(3, "C", -0.5), _row(4, "D", -0.5)]
    assert todays_read(rows)["drift"] == "falling"


def test_drift_rising_when_bottom_half_mean_change_is_positive():
    rows = [_row(1, "A", -1.0), _row(2, "B", -1.0), _row(3, "C", 0.5), _row(4, "D", 0.5)]
    assert todays_read(rows)["drift"] == "rising"


def test_drift_is_flat_inside_the_epsilon_band():
    """Without a dead band the cell would flip between "picking up" and
    "sliding" on noise, scan to scan, which reads as the board changing its
    mind rather than as the market moving."""
    rows = [_row(1, "A", 1.0), _row(2, "B", 1.0),
            _row(3, "C", DRIFT_EPS / 2), _row(4, "D", DRIFT_EPS / 2)]
    assert todays_read(rows)["drift"] == "flat"


def test_bottom_half_is_the_lower_ranked_half():
    """18 themes -> bottom half is ranks 10-18. The top half's change scores
    must not influence drift at all."""
    rows = [_row(i, f"T{i}", 5.0) for i in range(1, 10)]
    rows += [_row(i, f"T{i}", -1.0) for i in range(10, 19)]
    assert todays_read(rows)["drift"] == "falling"


def test_odd_row_count_puts_the_middle_row_in_the_bottom_half():
    rows = [_row(1, "A", 5.0), _row(2, "B", -1.0), _row(3, "C", -1.0)]
    assert todays_read(rows)["drift"] == "falling"


def test_returns_none_for_no_rows():
    """A build with no scan data must render no cell at all, not an empty
    sentence."""
    assert todays_read([]) is None


def test_single_row_has_no_bottom_half_and_reads_flat():
    assert todays_read([_row(1, "Only", 0.9)]) == {"lead_theme": "Only", "drift": "flat"}


def test_missing_change_scores_are_ignored_not_counted_as_zero():
    """A None change is absent data. Counting it as 0.0 would drag the mean
    toward flat and silently mute a real move."""
    rows = [_row(1, "A", 1.0), _row(2, "B", None), _row(3, "C", -1.0), _row(4, "D", None)]
    assert todays_read(rows)["drift"] == "falling"


def test_bottom_half_entirely_missing_change_reads_flat():
    rows = [_row(1, "A", 1.0), _row(2, "B", 1.0), _row(3, "C", None), _row(4, "D", None)]
    assert todays_read(rows)["drift"] == "flat"


def test_rows_with_no_usable_rank_are_skipped():
    """rows.py writes the string "—" for a missing rank."""
    rows = [_row("—", "Broken", 1.0), _row(1, "Real", 1.0), _row(2, "Other", -1.0)]
    assert todays_read(rows)["lead_theme"] == "Real"


def test_returns_none_when_no_row_has_a_usable_rank():
    assert todays_read([_row("—", "Broken", 1.0)]) is None


def test_returns_none_when_the_lead_row_has_no_theme_name():
    assert todays_read([_row(1, "", 1.0)]) is None


@pytest.mark.parametrize("drift", ["rising", "falling", "flat"])
def test_drift_is_always_one_of_the_three_template_keys(drift):
    """The template has exactly three i18n keys; any fourth value would render
    an empty second clause."""
    rows = [_row(1, "A", 1.0), _row(2, "B", {"rising": 1.0, "falling": -1.0, "flat": 0.0}[drift])]
    assert todays_read(rows)["drift"] in {"rising", "falling", "flat"}
