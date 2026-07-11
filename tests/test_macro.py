"""Unit tests for src/data/macro.py — stub module for Phase 1."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.macro import fetch_fred, FRED_SERIES


# ---------------------------------------------------------------------------
# FRED_SERIES metadata
# ---------------------------------------------------------------------------

def test_fred_series_contains_expected_keys():
    """The FRED_SERIES dict should list the planned macro series."""
    assert "DGS10" in FRED_SERIES
    assert "DGS2" in FRED_SERIES
    assert "T10Y2Y" in FRED_SERIES
    assert "DTWEXBGS" in FRED_SERIES


def test_fred_series_values_are_descriptions():
    """Each series should have a human-readable description string."""
    for key, desc in FRED_SERIES.items():
        assert isinstance(desc, str)
        assert len(desc) > 5, f"Description for {key} is too short"


# ---------------------------------------------------------------------------
# fetch_fred (Phase 1 stub)
# ---------------------------------------------------------------------------

def test_fetch_fred_returns_empty_dict():
    """Phase 1 stub always returns an empty dict."""
    result = fetch_fred()
    assert result == {}
    assert isinstance(result, dict)


def test_fetch_fred_ignores_series_ids_argument():
    """Passing series_ids has no effect in the stub."""
    result = fetch_fred(series_ids=["DGS10", "DGS2"])
    assert result == {}


def test_fetch_fred_ignores_api_key_argument():
    """Passing an api_key has no effect in the stub."""
    result = fetch_fred(api_key="fake-key-123")
    assert result == {}


def test_fetch_fred_ignores_date_range_arguments():
    """Passing start/end has no effect in the stub."""
    result = fetch_fred(start="2020-01-01", end="2026-01-01")
    assert result == {}


def test_fetch_fred_with_all_arguments():
    """Passing all arguments together has no effect in the stub."""
    result = fetch_fred(
        series_ids=["DGS10"],
        api_key="key",
        start="2020-01-01",
        end="2026-01-01",
    )
    assert result == {}


def test_fetch_fred_does_not_raise():
    """The stub should never raise, regardless of input."""
    # None args
    result = fetch_fred(series_ids=None, api_key=None, start=None, end=None)
    assert result == {}
