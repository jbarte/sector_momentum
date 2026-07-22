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
    _expected_latest_close,
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


@patch("src.data.prices.date")
def test_cache_fresh_with_todays_close(mock_date, tmp_path):
    """Cache whose last date is today's expected close is fresh."""
    mock_date.today.return_value = date(2026, 7, 22)  # Wednesday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-22")])
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "today.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is True


@patch("src.data.prices.date")
def test_cache_fresh_with_yesterdays_close(mock_date, tmp_path):
    """Cache from yesterday (weekday) is fresh — within 1-day grace."""
    mock_date.today.return_value = date(2026, 7, 22)  # Wednesday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-21")])  # Tuesday
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "yesterday.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is True


@patch("src.data.prices.date")
def test_cache_fresh_friday_on_monday(mock_date, tmp_path):
    """Friday's close is fresh on Monday — weekend bridge."""
    mock_date.today.return_value = date(2026, 7, 20)  # Monday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-17")])  # Friday
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "friday.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is True


@patch("src.data.prices.date")
def test_cache_stale_thursday_on_monday(mock_date, tmp_path):
    """Thursday's close is stale on Monday — too old even with weekend bridge."""
    mock_date.today.return_value = date(2026, 7, 20)  # Monday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-16")])  # Thursday
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "thursday.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is False


@patch("src.data.prices.date")
def test_cache_stale_friday_on_tuesday_after_holiday(mock_date, tmp_path):
    """Friday's close is stale on Tuesday (Monday was holiday) — harmless refetch."""
    mock_date.today.return_value = date(2026, 7, 21)  # Tuesday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-17")])  # Friday
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "holiday.parquet")
    df.to_parquet(path)
    assert _cache_is_fresh(path) is False


@patch("src.data.prices.date")
def test_cache_stale_two_days_old_on_weekday(mock_date, tmp_path):
    """A cache 2+ trading days old on a normal weekday is stale."""
    mock_date.today.return_value = date(2026, 7, 23)  # Thursday
    mock_date.side_effect = lambda *a, **k: date(*a, **k)
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-21")])  # Tuesday
    df = pd.DataFrame({"Close": [100.0], "Open": [99.5], "High": [100.5], "Low": [99.0], "Volume": [1_000_000]}, index=idx)
    path = str(tmp_path / "twodays.parquet")
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
# _expected_latest_close
# ---------------------------------------------------------------------------

def test_expected_latest_close_monday():
    assert _expected_latest_close(date(2026, 7, 20)) == date(2026, 7, 20)  # Monday

def test_expected_latest_close_tuesday():
    assert _expected_latest_close(date(2026, 7, 21)) == date(2026, 7, 21)  # Tuesday

def test_expected_latest_close_wednesday():
    assert _expected_latest_close(date(2026, 7, 22)) == date(2026, 7, 22)  # Wednesday

def test_expected_latest_close_thursday():
    assert _expected_latest_close(date(2026, 7, 23)) == date(2026, 7, 23)  # Thursday

def test_expected_latest_close_friday():
    assert _expected_latest_close(date(2026, 7, 24)) == date(2026, 7, 24)  # Friday

def test_expected_latest_close_saturday():
    assert _expected_latest_close(date(2026, 7, 25)) == date(2026, 7, 24)  # Saturday → Friday

def test_expected_latest_close_sunday():
    assert _expected_latest_close(date(2026, 7, 26)) == date(2026, 7, 24)  # Sunday → Friday


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
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert source == "stooq"
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
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert source == "yfinance"
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
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert source == "yfinance"
    assert result is not None
    mock_yf.assert_called_once()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_returns_none_when_both_fail(mock_stooq, mock_yf):
    """When both sources fail, returns (None, None)."""
    mock_stooq.side_effect = Exception("stooq down")
    mock_yf.side_effect = Exception("yfinance down")
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert source is None
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
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert result is not None
    assert not result["Close"].isna().all()
    mock_yf.assert_called_once()


@patch("src.data.prices._fetch_yfinance")
@patch("src.data.prices._fetch_stooq")
def test_fetch_single_returns_none_when_both_return_empty(mock_stooq, mock_yf):
    mock_stooq.return_value = pd.DataFrame()
    mock_yf.return_value = pd.DataFrame()
    source, result = _fetch_single("XLK", "2026-01-01", "2026-06-01")
    assert source is None
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
    mock_fetch.return_value = ("stooq", _make_price_df(n=5))

    result = fetch_prices(["XLK"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result
    mock_fetch.assert_called_once()


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_writes_cache_after_fetch(mock_fresh, mock_fetch, tmp_path):
    """After a successful fetch, the result is cached to disk."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = ("stooq", _make_price_df(n=5))

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
    mock_fetch.return_value = (None, None)

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

    def fresh_side_effect(path, start=None):
        return "XLK" in path

    mock_fresh.side_effect = fresh_side_effect
    fetched_df = _make_price_df(n=3)
    mock_fetch.return_value = ("yfinance", fetched_df)

    result = fetch_prices(["XLK", "XLF"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result
    assert "XLF" in result
    assert len(result["XLK"]) == 5   # from cache
    assert len(result["XLF"]) == 3   # freshly fetched


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_populates_stats_out(mock_fresh, mock_fetch, tmp_path):
    """When stats_out dict is provided, it is populated with source counts."""
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir)
    cached_df = _make_price_df(n=5)
    cached_df.to_parquet(os.path.join(cache_dir, "XLK_prices.parquet"))

    def fresh_side_effect(path, start=None):
        return "XLK" in path

    mock_fresh.side_effect = fresh_side_effect
    mock_fetch.return_value = ("stooq", _make_price_df(n=3))

    stats: dict[str, int] = {}
    result = fetch_prices(
        ["XLK", "XLF"], "2026-01-01", "2026-06-01",
        cache_dir=cache_dir, stats_out=stats,
    )

    assert "XLK" in result
    assert "XLF" in result
    assert stats == {"cache": 1, "stooq": 1, "yfinance": 0}


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_fetch_prices_works_without_stats_out(mock_fresh, mock_fetch, tmp_path):
    """Omitting stats_out does not break fetch_prices (backward compat)."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = ("stooq", _make_price_df(n=5))

    result = fetch_prices(["XLK"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert "XLK" in result


def test_cache_is_fresh_false_when_start_not_covered(tmp_path):
    """Cache covers only the last 30 days; a 2-year lookback request is not covered."""
    df = _make_price_df(n=20, start_date=str(date.today() - timedelta(days=30)))
    path = str(tmp_path / "short.parquet")
    df.to_parquet(path)
    long_start = str(date.today() - timedelta(days=730))
    assert _cache_is_fresh(path, start=long_start) is False


def test_cache_is_fresh_true_when_start_covered(tmp_path):
    """Cache's earliest date is on/before the requested start (within tolerance)."""
    start_date = date.today() - timedelta(days=30)
    idx = pd.bdate_range(str(start_date), str(date.today()))
    n = len(idx)
    df = _make_price_df(n=n, start_date=str(start_date))
    path = str(tmp_path / "covered.parquet")
    df.to_parquet(path)
    earliest = df.index.min().date()
    recent_start = str(earliest + timedelta(days=5))
    assert _cache_is_fresh(path, start=recent_start) is True


# ---------------------------------------------------------------------------
# _stooq_symbol mapping
# ---------------------------------------------------------------------------

from src.data.prices import _stooq_symbol


def test_stooq_symbol_us_ticker():
    assert _stooq_symbol("XLK") == "xlk.us"
    assert _stooq_symbol("RSP") == "rsp.us"


def test_stooq_symbol_eu_ticker():
    assert _stooq_symbol("EXV3.DE") == "exv3.de"
    assert _stooq_symbol("EXSA.DE") == "exsa.de"


# ---------------------------------------------------------------------------
# _fetch_stooq — CSV parsing
# ---------------------------------------------------------------------------

from src.data.prices import _fetch_stooq


@patch("src.data.prices._requests.get")
def test_fetch_stooq_parses_csv(mock_get):
    csv_text = "Date,Open,High,Low,Close,Volume\n2026-06-01,100,105,99,103,1000\n2026-06-02,103,107,102,106,1200\n"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = csv_text
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    df = _fetch_stooq("XLK", "2026-06-01", "2026-06-02")
    assert len(df) == 2
    assert "Close" in df.columns
    assert df["Close"].iloc[0] == 103


@patch("src.data.prices._requests.get")
def test_fetch_stooq_raises_on_bad_status(mock_get):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
    mock_get.return_value = mock_resp

    with pytest.raises(Exception, match="404"):
        _fetch_stooq("BAD", "2026-06-01", "2026-06-02")


@patch("src.data.prices._requests.get")
def test_fetch_stooq_raises_on_header_only(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Date,Open,High,Low,Close,Volume\n"
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    with pytest.raises(ValueError, match="no data"):
        _fetch_stooq("UNKNOWN", "2026-06-01", "2026-06-02")


# ---------------------------------------------------------------------------
# Source stats warning
# ---------------------------------------------------------------------------

import logging


@patch("src.data.prices._fetch_single")
@patch("src.data.prices._cache_is_fresh")
def test_source_stats_warning_when_stooq_fails(mock_fresh, mock_fetch, tmp_path, caplog):
    """When stooq goes 0-for-N, a warning is logged."""
    cache_dir = str(tmp_path / "cache")
    mock_fresh.return_value = False
    mock_fetch.return_value = ("yfinance", _make_price_df(n=5))

    with caplog.at_level(logging.WARNING, logger="src.data.prices"):
        fetch_prices(["XLK", "XLF"], "2026-01-01", "2026-06-01", cache_dir=cache_dir)

    assert any("stooq: 0/" in r.message for r in caplog.records)
