import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.data.constituents import fetch_sp500_constituents, _GICS_TO_SECTOR


def _fake_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": ["AAPL", "MSFT", "BRK.B", "JPM", "XOM"],
            "GICS Sector": [
                "Information Technology",
                "Information Technology",
                "Financials",
                "Financials",
                "Energy",
            ],
        }
    )


def _mock_requests_get():
    """Return a mock requests.Response whose .text is non-empty and .raise_for_status() is a no-op."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.text = "<html>stub</html>"
    mock_resp.raise_for_status.return_value = None
    mock_get = MagicMock(return_value=mock_resp)
    return mock_get


def test_maps_gics_sector_and_normalizes_tickers(tmp_path):
    with patch("src.data.constituents.requests.get", _mock_requests_get()), \
         patch("src.data.constituents.pd.read_html", return_value=[_fake_table()]):
        result = fetch_sp500_constituents(cache_dir=str(tmp_path))
    assert result is not None
    # "Information Technology" → our "Technology"
    assert set(result["Technology"]) == {"AAPL", "MSFT"}
    assert set(result["Financials"]) == {"BRK-B", "JPM"}   # BRK.B → BRK-B
    assert result["Energy"] == ["XOM"]


def test_writes_then_reads_cache_without_rescrape(tmp_path):
    with patch("src.data.constituents.requests.get", _mock_requests_get()), \
         patch("src.data.constituents.pd.read_html", return_value=[_fake_table()]) as m:
        fetch_sp500_constituents(cache_dir=str(tmp_path))
        assert m.call_count == 1
    # Second call within TTL must NOT scrape again — neither requests.get nor read_html called.
    with patch("src.data.constituents.requests.get", side_effect=AssertionError("should not fetch")), \
         patch("src.data.constituents.pd.read_html", side_effect=AssertionError("should not scrape")) as m2:
        cached = fetch_sp500_constituents(cache_dir=str(tmp_path))
        m2.assert_not_called()
    assert cached["Technology"]


def test_scrape_failure_returns_none(tmp_path):
    with patch("src.data.constituents.requests.get", side_effect=Exception("network down")):
        assert fetch_sp500_constituents(cache_dir=str(tmp_path)) is None


def test_information_technology_is_the_only_nonidentity_mapping():
    # Guard: if Wikipedia renames a GICS sector, this fails loudly.
    for gics, ours in _GICS_TO_SECTOR.items():
        if gics != "Information Technology":
            assert gics == ours
    assert _GICS_TO_SECTOR["Information Technology"] == "Technology"
