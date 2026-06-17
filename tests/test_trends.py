"""Tests for the Google Trends search momentum loader."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.trends import fetch_trends


@pytest.fixture
def keywords():
    return {
        "Technology": ["semiconductor", "AI", "cloud"],
        "Energy": ["oil", "gas", "crude"],
    }


def _mock_pytrends(interest_values: list[float], keyword: str) -> MagicMock:
    pt = MagicMock()
    idx = pd.date_range("2026-01-01", periods=len(interest_values), freq="W")
    pt.interest_over_time.return_value = pd.DataFrame(
        {keyword: interest_values}, index=idx
    )
    return pt


def test_returns_series_per_sector(keywords, tmp_path):
    values = list(range(13))
    mock_pt = _mock_pytrends(values, "semiconductor")

    with patch("src.data.trends.TrendReq", return_value=mock_pt):
        result = fetch_trends(keywords, cache_dir=str(tmp_path))

    assert result is not None
    assert "Technology" in result
    assert isinstance(result["Technology"], pd.Series)
    assert len(result["Technology"]) == 13


def test_returns_none_on_error(keywords, tmp_path):
    with patch("src.data.trends.TrendReq", side_effect=Exception("429")):
        result = fetch_trends(keywords, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache_on_second_call(keywords, tmp_path):
    cached = {"Technology": list(range(13)), "Energy": list(range(13, 26))}
    cache_file = tmp_path / f"trends_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.trends.TrendReq") as mock_cls:
        result = fetch_trends(keywords, cache_dir=str(tmp_path))
        mock_cls.assert_not_called()

    assert result is not None
    assert len(result["Technology"]) == 13
