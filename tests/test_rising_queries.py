"""Tests for fetch_rising_queries."""
import math
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.trends_symbols import fetch_rising_queries


def _mock_client(rising_df=None):
    """Return a mock pytrends client with configurable related_queries output."""
    client = MagicMock()
    if rising_df is None:
        rising_df = pd.DataFrame({
            "query": ["nvidia stock", "ai etf", "semiconductor etf", "tech stocks", "apple stock price"],
            "value": [2400, 1800, 900, 500, "Breakout"],
        })

    def _rq():
        term = client.build_payload.call_args[0][0][0]
        return {term: {"top": pd.DataFrame(), "rising": rising_df}}

    client.related_queries = _rq
    return client


def test_fetch_rising_queries_basic():
    symbol_map = {"US|Technology": ["XLK", "VGT"], "US|Energy": ["XLE"]}
    client = _mock_client()
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    assert "US|Technology" in result
    assert "US|Energy" in result
    assert len(result["US|Technology"]) <= 5
    assert result["US|Technology"][0]["query"] == "nvidia stock"
    assert result["US|Technology"][-1]["growth"] == "Breakout"


def test_fetch_rising_queries_empty_results():
    symbol_map = {"US|Technology": ["XLK"]}
    client = _mock_client(rising_df=pd.DataFrame(columns=["query", "value"]))
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    assert result.get("US|Technology", []) == []


def test_fetch_rising_queries_fail_open():
    symbol_map = {"US|Technology": ["XLK"]}
    client = MagicMock()
    client.build_payload.side_effect = Exception("429 Too Many Requests")
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]},
    )
    assert result.get("US|Technology", []) == []


def test_fetch_rising_queries_cache_hit():
    symbol_map = {"US|Technology": ["XLK"]}
    cached_data = [{"query": "cached query", "growth": "100%"}]
    cache = {"rising_US": {"XLK": cached_data}}
    client = MagicMock()
    result = fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        region_geos={"US": ["US"]}, cache=cache,
    )
    assert result["US|Technology"] == cached_data
    client.build_payload.assert_not_called()


def test_fetch_rising_queries_uses_entity_mid():
    symbol_map = {"US|Technology": ["XLK"]}
    entities = {"XLK": "/m/0xyz_tech"}
    client = _mock_client()
    fetch_rising_queries(
        symbol_map, client=client, sleep_s=0, max_retries=1,
        entities=entities, region_geos={"US": ["US"]},
    )
    call_args = client.build_payload.call_args[0][0]
    assert call_args == ["/m/0xyz_tech"]
