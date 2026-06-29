"""Tests for the sentiment signal calculator."""
import math

import pandas as pd

from src.signals.sentiment import compute_sentiment_score

US_SECTORS = {"Technology": "XLK", "Energy": "XLE", "Financials": "XLF"}
SECTOR_KEYS = [
    "US|Technology", "US|Energy", "US|Financials",
    "EU|Technology", "EU|Energy", "EU|Financials",
]


def _reddit(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: {"7d": 10, "30d": 30} for s in sectors}


def _trends(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: pd.Series(range(13), dtype=float) for s in sectors}


def test_returns_series_indexed_by_sector_keys():
    result = compute_sentiment_score(_reddit(), _trends(), SECTOR_KEYS)
    assert isinstance(result, pd.Series)
    assert set(result.index) == set(SECTOR_KEYS)


def test_all_none_sources_returns_zero():
    result = compute_sentiment_score(None, None, SECTOR_KEYS)
    assert (result == 0.0).all()


def test_eu_sectors_get_score_from_shared_signals():
    result = compute_sentiment_score(_reddit(), _trends(), SECTOR_KEYS)
    # EU sectors share the same sector-level Reddit + Trends signal as US.
    assert not math.isnan(result["EU|Technology"])


def test_partial_coverage_sectors_get_neutral():
    # Only Technology has Reddit data, no Trends.
    result = compute_sentiment_score(
        {"Technology": {"7d": 10, "30d": 30}},
        None,
        SECTOR_KEYS,
    )
    # Energy and Financials have no data → 0.0 neutral
    assert result["US|Energy"] == 0.0
    assert result["US|Financials"] == 0.0


def test_scores_are_finite():
    result = compute_sentiment_score(_reddit(), _trends(), SECTOR_KEYS)
    assert all(math.isfinite(v) for v in result.values)
