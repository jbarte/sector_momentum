"""Tests for the StockTwits bull/bear sentiment loader."""
import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.data.stocktwits import fetch_stocktwits


@pytest.fixture
def us_sectors():
    return {"Technology": "XLK", "Energy": "XLE"}


def _make_response(bulls: int, bears: int) -> dict:
    messages = []
    for _ in range(bulls):
        messages.append({"entities": {"sentiment": {"basic": "Bullish"}}})
    for _ in range(bears):
        messages.append({"entities": {"sentiment": {"basic": "Bearish"}}})
    messages.append({"entities": {}})  # no sentiment tag
    return {"messages": messages}


def test_counts_bull_and_bear(us_sectors, tmp_path):
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = _make_response(bulls=10, bears=4)

    with patch("src.data.stocktwits.requests.get", return_value=mock_resp):
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))

    assert result is not None
    assert result["Technology"]["bull"] == 10
    assert result["Technology"]["bear"] == 4


def test_returns_none_on_error(us_sectors, tmp_path):
    with patch("src.data.stocktwits.requests.get", side_effect=Exception("network")):
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache(us_sectors, tmp_path):
    cached = {"Technology": {"bull": 7, "bear": 2}, "Energy": {"bull": 3, "bear": 5}}
    cache_file = tmp_path / f"stocktwits_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.stocktwits.requests.get") as mock_get:
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))
        mock_get.assert_not_called()

    assert result == cached
