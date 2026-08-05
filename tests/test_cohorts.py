"""Unit tests for the cohort helper.

A cohort is one cross-sectional scoring universe. Since the sector cohorts were
retired there is exactly one — themes — but the list shape is deliberately kept,
so these pin the properties every consumer relies on: the region discriminator
matches what the database actually stores, instruments are keyed `region|name`,
and an unconfigured cohort is omitted rather than yielded empty.
"""
from __future__ import annotations

import dataclasses

import pytest

from src import state
from src.cohorts import Cohort, cohorts, instrument_map, THEME_REGION

_THEMES = {
    "benchmark": "ACWI",
    "themes": {
        "Space": {"ticker": "UFO", "gdelt_keywords": ["space launch"]},
        "Biotech": {"ticker": "XBI"},
    },
}


def test_themes_are_the_configured_cohort():
    assert [c.region for c in cohorts(_THEMES)] == [THEME_REGION]


def test_benchmark_comes_from_config():
    assert cohorts(_THEMES)[0].benchmark == "ACWI"


def test_benchmark_falls_back_when_absent():
    assert cohorts({"themes": {"Space": {"ticker": "UFO"}}})[0].benchmark == "ACWI"


def test_instruments_are_keyed_by_sector_key():
    theme = cohorts(_THEMES)[0]
    assert theme.instruments == {"THEME|Space": "UFO", "THEME|Biotech": "XBI"}


def test_theme_entry_may_be_a_bare_string():
    """src/pipeline.py:build_theme_signals_rows tolerates a bare ticker string;
    this helper must agree or the two disagree about the same config."""
    assert cohorts({"themes": {"Space": "UFO"}})[0].instruments == {"THEME|Space": "UFO"}


def test_empty_cohort_is_omitted():
    assert cohorts({"themes": {}}) == []


def test_missing_keys_do_not_raise():
    assert cohorts({}) == []
    assert cohorts(None) == []


def test_instrument_map_flattens_every_cohort():
    assert instrument_map(cohorts(_THEMES)) == {
        "THEME|Space": "UFO", "THEME|Biotech": "XBI",
    }


def test_instrument_map_of_nothing_is_empty():
    assert instrument_map([]) == {}


def test_labels_are_human_readable():
    assert [c.label for c in cohorts(_THEMES)] == ["Themes"]


def test_theme_region_matches_state():
    """src/state.py owns the value written to scores.region. A drift between
    the two would silently split the cohort in half."""
    assert THEME_REGION == state.THEME_REGION


def test_default_regions_is_the_theme_cohort():
    """Readers default to DEFAULT_REGIONS. If that stops matching the configured
    cohort's region, every dashboard query silently returns nothing — and the
    retired sector rows are still in the table waiting to be selected."""
    assert tuple(state.DEFAULT_REGIONS) == (THEME_REGION,)


def test_cohort_is_immutable():
    c = cohorts(_THEMES)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.region = "XX"
    # Immutability is shallow: `frozen=True` blocks reassigning the
    # `instruments` field itself, but the dict it points to is still a
    # regular mutable dict (c.instruments["X"] = "Y" would succeed).


def test_cohort_is_hashable_despite_dict_field():
    """instruments is excluded from __hash__ so a frozen dataclass carrying a
    dict stays usable as a dict key / set member."""
    assert len({*cohorts(_THEMES), *cohorts(_THEMES)}) == 1


def test_cohort_equality_still_compares_instruments():
    a = Cohort("THEME", "Themes", "ACWI", {"THEME|Space": "UFO"})
    b = Cohort("THEME", "Themes", "ACWI", {"THEME|Space": "XBI"})
    assert a != b
