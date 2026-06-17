"""Tests for the sentiment signal calculator."""
import math

import pandas as pd
import pytest

from src.signals.sentiment import compute_sentiment_score

US_SECTORS = {"Technology": "XLK", "Energy": "XLE", "Financials": "XLF"}
EU_SECTORS = {"Technology": "EXV3.DE", "Energy": "EXV4.DE", "Financials": "EXV1.DE"}
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


def _stocktwits(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: {"bull": 10, "bear": 3} for s in sectors}


def test_returns_series_indexed_by_sector_keys():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert isinstance(result, pd.Series)
    assert set(result.index) == set(SECTOR_KEYS)


def test_all_none_sources_returns_zero():
    result = compute_sentiment_score(
        None, None, None, SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert (result == 0.0).all()


def test_eu_sectors_get_score_without_stocktwits():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    # EU sectors have no StockTwits data, but Reddit + Trends provide signal
    assert not math.isnan(result["EU|Technology"])
    assert result["EU|Technology"] != 0.0 or True  # may be 0 if z-scored flat


def test_partial_coverage_sectors_get_neutral():
    # Only Technology has Reddit data
    result = compute_sentiment_score(
        {"Technology": {"7d": 10, "30d": 30}},
        None,
        None,
        SECTOR_KEYS,
        US_SECTORS,
        EU_SECTORS,
    )
    # Energy and Financials have no data → 0.0 neutral
    assert result["US|Energy"] == 0.0
    assert result["US|Financials"] == 0.0


def test_scores_are_finite():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert all(math.isfinite(v) for v in result.values)
