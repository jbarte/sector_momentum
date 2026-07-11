"""Unit tests for src/data/prices.py — cache logic, stooq→yfinance fallback, edge cases."""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.prices import (
    _cache_is_fresh,
    _cache_path,
    _fetch_single,
    _normalize_columns,
    _sanitize_ticker,
    fetch_prices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_df(n=60, start_date="2026-06-01"):
    """Create a minimal OHLCV DataFrame resembling real price data."""
    idx = pd.bdate_range(start_date, periods=n)
    close = pd.Series(100.0 + 0.5 * np.arange(n), index=idx)
    return pd.DataFrame({
        "Close": close,
        "Open": close - 0.1,
        "High": close + 0.5,
        "Low": close - 0.5,
        "Volume": pd.Series(1_000_000, index=idx),
    })


# ---------------------------------------------------------------------------
# _sanitize_ticker
# ---------------------------------------------------------------------------

def test_sanitize_ticker_replaces_dots_and_slashes():
    assert _sanitize_ticker("EXSA.DE") == "EXSA_DE"
    assert _sanitize_ticker("BRK/B") == "BRK_B"
    assert _sanitize_ticker("SPY") == "SPY"


# ---------------------------------------------------------------------------
# _cache_path
# ---------------------------------------------------------------------------

def test_cache_path_structure():
    path = _cache_path("XLK", "/tmp/cache")
    assert path == "/tmp/cache/XLK_prices.parquet"


def test_cache_path_sanitizes_ticker():
    path = _cache_path("EXSA.DE", "/tmp/cache")
    assert path == "/tmp/cache/EXSA_DE_prices.parquet"


# ---------------------------------------------------------------------------
# _cache_is_fresh
# ---------------------------------------------------------------------------

def test_cache_is_fresh_returns_false_for_missing_file():
    assert _cache_is_fresh("/nonexistent/path/foo.parquet") is False


def test_cache_is_fresh_returns_true_for_current_data(tmp_path):
    """A cache file whose last date >= last trading day is considered fresh."""
    df = _make_price_df(n=10, start_date=str(date.today() - timedelta(days=14)))
    path = str(tmp_path / "test.parquet")
    df.to_parquet(path)
    # The DataFrame extends to recent business days; should be fresh
    # unless today is a weekend Monday and last 10 bdays don't reach yesterday.
    # Use a mock to make this deterministic.
    with patch("src.data.prices._last_trading_day", return_value=df.index[-1].date()):
        assert _cache_is_fresh(path) is True


def test_cache_is_fresh_returns_false_for_stale_data(tmp_path):
    """A cache file whose last date is well before the last trading day is stale."""
    df = _make_price_df(n=5, start_date="2020-01-02")
    path = str(tmp_path / "stale.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is False


def test_cache_is_fresh_handles_empty_parquet(tmp_path):
    """An empty parquet file should not be considered fresh."""
    path = str(tmp_path / "empty.parquet")
    pd.DataFrame(columns=["Close"]).to_parquet(path)
    assert _cache_is_fresh(path) is False


def test_cache_is_fresh_handles_corrupted_file(tmp_path):
    """A corrupted (non-parquet) file should return False, not raise."""
    path = str(tmp_path / "bad.parquet")
    Path(path).write_text("not a parquet file")
    assert _cache_is_fresh(path) is False


# ---------------------------------------------------------------------------
# _normalize_columns
# ---------------------------------------------------------------------------

def test_normalize_columns_title_cases():
    df = pd.DataFrame({"close": [1, 2], "open": [1, 2], "volume": [100, 200]})
    result = _normalize_columns(df)
    assert "Close" in result.columns
    assert "Open" in result.columns
    assert "Volume" in result.columns


def test_normalize_columns_handles_multiindex():
    """yfinance sometimes returns MultiIndex columns with ticker as one level."""
    arrays = [["XLK", "XLK", "XLK"], ["Close", "Open", "Volume"]]
    tuples = list(zip(*arrays))
    idx = pd.MultiIndex.from_tuples(tuples)
    df = pd.DataFrame([[1, 2, 100], [3, 4, 200]], columns=idx)
    result = _normalize_columns(df)
    assert "Close" in result.columns
    assert "Open" in result.columns


def test_normalize_columns_keeps_only_ohlcv():
    df = pd.DataFrame({
        "Close": [1, 2],
        "Open": [1, 2],
        "Adj Close": [1, 2],
        "Extra": [0, 0],
    })
    result = _normalize_columns(df)
    assert "Close" in result.columns
    assert "Extra" not in result.columns
    assert "Adj Close" not in result.columns


# ---------------------------------------------------------------------------
# _fetch_single — stooq→yfinance fallback
# ---------------------------------------------------------------------------

@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_returns_stooq_on_success(mock_stooq, mock_yf):
    """When stooq succeeds, yfinance is never called."""
    df = _make_price_df(n=5)
    mock_stooq.return_value = df
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is not None
    assert "Close" in result.columns
    assert len(result) == 5
    mock_yf.assert_not_called()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_falls_back_to_yfinance_on_stooq_failure(mock_stooq, mock_yf):
    """When stooq raises, yfinance is tried next."""
    mock_stooq.side_effect = Exception("stooq down")
    mock_yf.return_value = _make_price_df(n=5)
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is not None
    assert "Close" in result.columns
    mock_stooq.assert_called_once()
    mock_yf.assert_called_once()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_falls_back_on_empty_stooq(mock_stooq, mock_yf):
    """When stooq returns an empty DataFrame, yfinance is tried."""
    mock_stooq.return_value = pd.DataFrame()
    mock_yf.return_value = _make_price_df(n=5)
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is not None
    mock_yf.assert_called_once()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_returns_none_when_both_fail(mock_stooq, mock_yf):
    """When both sources fail, returns None (soft failure)."""
    mock_stooq.side_effect = Exception("stooq down")
    mock_yf.side_effect = Exception("yfinance down")
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is None


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_rejects_all_nan_close(mock_stooq, mock_yf):
    """A DataFrame with all-NaN Close is rejected, and fallback is tried."""
    bad_df = pd.DataFrame({
        "Close": [float("nan")] * 5,
        "Open": [1.0] * 5,
        "Volume": [100] * 5,
    }, index=pd.bdate_range("2026-01-01", periods=5))
    good_df = _make_price_df(n=5)
    mock_stooq.return_value = bad_df
    mock_yf.return_value = good_df
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is not None
    assert not result["Close"].isna().all()
    mock_yf.assert_called_once()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_returns_none_when_both_return_empty(mock_stooq, mock_yf):
    mock_stooq.return_value = pd.DataFrame()
    mock_yf.return_value = pd.DataFrame()
    result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is None


# ---------------------------------------------------------------------------
# fetch_prices — cache integration
# ---------------------------------------------------------------------------

@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_uses_cache_when_fresh(mock_fresh, mock_fetch, tmp_path):
    """When cache is fresh, no live fetch happens — cached data is returned."""
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir)
    df = _make_price_df(n=5)
    cache_file = os.path.join(cache_dir, "XLK_prices.parquet")
    df.to_parquet(cache_file)

    mock_fresh.return_value = True
    result = fetch_prices(["XLK"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result
    assert len(result["XLK"]) == 5
    mock_fetch.assert_not_called()


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_fetches_when_cache_stale(mock_fresh, mock_fetch, tmp_path):
    """When cache is stale, a live fetch is performed."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = _make_price_df(n=5)

    result = fetch_prices(["XLK"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result
    mock_fetch.assert_called_once()


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_writes_cache_after_fetch(mock_fresh, mock_fetch, tmp_path):
    """After a successful fetch, the result is cached to disk."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = _make_price_df(n=5)

    fetch_prices(["XLK"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    cache_file = os.path.join(cache_dir, "XLK_prices.parquet")
    assert os.path.exists(cache_file)
    cached = pd.read_parquet(cache_file)
    assert len(cached) == 5


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_omits_failed_tickers(mock_fresh, mock_fetch, tmp_path):
    """Tickers that fail both sources are silently omitted."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = None

    result = fetch_prices(["XLK", "BAD"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" not in result
    assert "BAD" not in result
    assert result == {}


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_handles_mix_of_cached_and_fresh(mock_fresh, mock_fetch, tmp_path):
    """One ticker is cached, another needs fetching — both returned."""
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir)
    cached_df = _make_price_df(n=5)
    cached_df.to_parquet(os.path.join(cache_dir, "XLK_prices.parquet"))

    def fresh_side_effect(path):
        return "XLK" in path

    mock_fresh.side_effect = fresh_side_effect
    fetched_df = _make_price_df(n=3)
    mock_fetch.return_value = fetched_df

    result = fetch_prices(["XLK", "XLF"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result
    assert "XLF" in result
    assert len(result["XLK"]) == 5   # from cache
    assert len(result["XLF"]) == 3   # freshly fetched
