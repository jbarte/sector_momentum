"""Unit tests for the cohort helper.

A cohort is one cross-sectional scoring universe. These pin the two properties
every consumer relies on: sector cohorts come out US-then-EU, and themes are
absent unless explicitly asked for.
"""
from __future__ import annotations

import dataclasses

import pytest

from src import state
from src.cohorts import Cohort, cohorts, instrument_map, THEME_REGION

_UNIVERSE = {
    "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
    "eu_sectors": {"Technology": "EXV3.DE", "Banks": "EXV1.DE"},
    "us_benchmark": "RSP",
    "eu_benchmark": "EXSA.DE",
}

_THEMES = {
    "benchmark": "ACWI",
    "themes": {
        "Space": {"ticker": "UFO", "gdelt_keywords": ["space launch"]},
        "Biotech": {"ticker": "XBI"},
    },
}


def test_sector_cohorts_only_by_default():
    """No themes_cfg means no THEME_REGION cohort, not just US-then-EU
    ordering (that's pinned separately by test_us_precedes_eu)."""
    result = cohorts(_UNIVERSE)
    assert THEME_REGION not in [c.region for c in result]


def test_us_precedes_eu():
    """Every consumer renders US before EU; the build diff depends on it."""
    assert [c.region for c in cohorts(_UNIVERSE)] == ["US", "EU"]


def test_themes_included_only_when_config_supplied():
    assert [c.region for c in cohorts(_UNIVERSE, _THEMES)] == ["US", "EU", THEME_REGION]


def test_benchmarks_come_from_config():
    us, eu = cohorts(_UNIVERSE)
    assert us.benchmark == "RSP"
    assert eu.benchmark == "EXSA.DE"
    theme = cohorts(_UNIVERSE, _THEMES)[2]
    assert theme.benchmark == "ACWI"


def test_instruments_are_keyed_by_sector_key():
    us = cohorts(_UNIVERSE)[0]
    assert us.instruments == {"US|Technology": "XLK", "US|Energy": "XLE"}


def test_theme_instruments_read_the_ticker_field():
    theme = cohorts(_UNIVERSE, _THEMES)[2]
    assert theme.instruments == {"THEME|Space": "UFO", "THEME|Biotech": "XBI"}


def test_theme_entry_may_be_a_bare_string():
    """themes.yaml entries are dicts today, but src/pipeline.py tolerates a
    bare ticker string — stay consistent with it."""
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO"}}
    assert cohorts(_UNIVERSE, cfg)[2].instruments == {"THEME|Space": "UFO"}


def test_empty_cohort_is_omitted():
    """Spec 2 retires the sector cohorts by deleting them from universe.yaml —
    every consumer must follow with no code change."""
    result = cohorts({"us_sectors": {}, "eu_sectors": {}}, _THEMES)
    assert [c.region for c in result] == [THEME_REGION]


def test_missing_keys_do_not_raise():
    assert cohorts({}) == []


def test_instrument_map_flattens_every_cohort():
    flat = instrument_map(cohorts(_UNIVERSE, _THEMES))
    assert flat["US|Technology"] == "XLK"
    assert flat["EU|Banks"] == "EXV1.DE"
    assert flat["THEME|Space"] == "UFO"
    assert len(flat) == 6


def test_labels_are_human_readable():
    assert [c.label for c in cohorts(_UNIVERSE, _THEMES)] == [
        "US Sectors", "EU Sectors", "Themes"]


def test_theme_region_matches_state():
    """src/state.py owns the value written to scores.region. A drift between
    the two would silently split the cohort in half."""
    assert THEME_REGION == state.THEME_REGION


def test_cohort_is_immutable():
    c = cohorts(_UNIVERSE)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.region = "XX"
    # Immutability is shallow: `frozen=True` blocks reassigning the
    # `instruments` field itself, but the dict it points to is still a
    # regular mutable dict (c.instruments["X"] = "Y" would succeed).
