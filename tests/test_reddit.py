"""Tests for the Reddit public JSON mention loader."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from src.data.reddit import fetch_reddit


@pytest.fixture
def keywords():
    return {
        "Technology": ["semiconductor", "AI", "XLK"],
        "Energy": ["oil", "gas", "XLE"],
    }


def _make_post(days_ago: int) -> dict:
    ts = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())
    return {"data": {"title": "test post", "created_utc": ts}}


def _make_response(posts: list) -> dict:
    return {"data": {"children": posts, "after": None}}


def test_counts_posts_in_7d_and_30d_windows(keywords, tmp_path):
    recent = _make_post(3)   # within 7d
    mid = _make_post(15)     # within 30d but not 7d
    old = _make_post(45)     # outside 30d

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = _make_response([recent, mid, old])

    with patch("src.data.reddit.requests.get", return_value=mock_resp):
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))

    assert result is not None
    assert result["Technology"]["7d"] == 1
    assert result["Technology"]["30d"] == 2


def test_returns_none_on_network_error(keywords, tmp_path):
    with patch("src.data.reddit.requests.get", side_effect=Exception("timeout")):
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache_on_second_call(keywords, tmp_path):
    cached = {"Technology": {"7d": 5, "30d": 10}, "Energy": {"7d": 2, "30d": 8}}
    cache_file = tmp_path / f"reddit_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.reddit.requests.get") as mock_get:
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))
        mock_get.assert_not_called()

    assert result == cached
