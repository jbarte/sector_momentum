"""The Tiingo fallback: insurance against a Yahoo outage, nothing more.

yfinance is tried first for every ticker; Tiingo is asked only for a ticker
yfinance could not supply, only when TIINGO_API_KEY is set, and only for a
caller that opts in (`fetch_prices(..., fallback=True)`). These tests pin the
contract around that: the result is shaped like yfinance's, `end` stays
EXCLUSIVE, the token never leaves a header, nothing about a failure leaks the
request URL (this repo's CI logs are public), a rate limit pauses further calls
across fetch_prices() calls, a rejected token stops them for good, and with no
key (or no opt-in) the behaviour is byte-for-byte yfinance-only.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import math
import time

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data import prices as prices_mod
from src.data.prices import _fetch_single, _fetch_tiingo, fetch_prices

KEY = "tiingo-test-token-9f3a"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _row(day, close=100.0):
    return {"date": f"{day}T00:00:00.000Z", "close": close + 1, "high": close + 3,
            "low": close - 3, "open": close - 1, "volume": 999,
            "adjClose": close, "adjHigh": close + 2, "adjLow": close - 2,
            "adjOpen": close - 0.5, "adjVolume": 1000, "divCash": 0.0, "splitFactor": 1.0}


def _yf_frame(n=3):
    idx = pd.date_range("2026-01-02", periods=n, freq="B")
    return pd.DataFrame({"Close": 1.0, "Open": 1.0, "High": 1.0, "Low": 1.0, "Volume": 1}, index=idx)


@pytest.fixture(autouse=True)
def _fresh_pause(monkeypatch):
    """The pause is module state by design (it must span fetch_prices calls)."""
    monkeypatch.setattr(prices_mod, "_tiingo_off_until", 0.0)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", KEY)


# --- _fetch_tiingo: shape, `end`, secrecy ------------------------------------

def test_result_is_shaped_like_yfinance_and_uses_the_adjusted_fields(with_key):
    rows = [_row("2026-01-05", 100), _row("2026-01-02", 98)]      # out of order on purpose
    with patch.object(prices_mod.requests, "get", return_value=_Resp(200, rows)):
        df = _fetch_tiingo("BOTZ", "2026-01-01", "2026-02-01", KEY)
    assert list(df.columns) == ["Close", "Open", "High", "Low", "Volume"]
    assert df.index.is_monotonic_increasing
    assert df.index.tz is None
    assert list(df.index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]
    assert df["Close"].tolist() == [98, 100]          # adjClose, not close
    assert df["Open"].iloc[0] == 97.5                 # adjOpen
    assert df["Volume"].iloc[0] == 1000               # adjVolume


def test_end_is_exclusive_even_if_tiingo_treats_its_end_date_as_inclusive(with_key):
    """The request asks for the day before `end`, and a bar dated on `end` is
    dropped even when the server returns it anyway."""
    rows = [_row("2026-01-30"), _row("2026-01-31"), _row("2026-02-01")]
    with patch.object(prices_mod.requests, "get", return_value=_Resp(200, rows)) as get:
        df = _fetch_tiingo("BOTZ", "2026-01-01", "2026-02-01", KEY)
    assert get.call_args.kwargs["params"]["endDate"] == "2026-01-31"
    assert df.index.max() == pd.Timestamp("2026-01-31")
    assert pd.Timestamp("2026-02-01") not in df.index


def test_the_token_is_sent_in_a_header_never_in_the_url(with_key):
    with patch.object(prices_mod.requests, "get", return_value=_Resp(200, [_row("2026-01-02")])) as get:
        _fetch_tiingo("BRK.B", "2026-01-01", "2026-02-01", KEY)
    call = get.call_args
    assert call.kwargs["headers"]["Authorization"] == f"Token {KEY}"
    assert KEY not in call.args[0]
    assert KEY not in str(call.kwargs["params"])
    assert call.args[0].endswith("/BRK-B/prices")      # Tiingo spells class shares with a dash


@pytest.mark.parametrize("response", [
    _Resp(200, []), _Resp(200, {"detail": "Not found"}), _Resp(404, {"detail": "x"}),
    _Resp(200, [{"date": "2026-01-02T00:00:00.000Z"}]),                       # no adjClose
    _Resp(200, [{"date": "2026-01-02T00:00:00.000Z", "adjClose": None}]),     # all-NaN close
])
def test_unusable_answers_are_none_not_a_crash(with_key, response):
    with patch.object(prices_mod.requests, "get", return_value=response):
        assert _fetch_tiingo("BOTZ", "2026-01-01", "2026-02-01", KEY) is None


# --- _fetch_single: when the fallback is used ---------------------------------

def test_yfinance_success_never_touches_tiingo(with_key):
    with patch.object(prices_mod, "_fetch_yfinance", return_value=_yf_frame()), \
         patch.object(prices_mod.requests, "get") as get:
        source, df = _fetch_single("BOTZ", "2026-01-01", "2026-02-01", fallback=True)
    assert source == "yfinance" and df is not None
    get.assert_not_called()


@pytest.mark.parametrize("yf", [
    {"return_value": pd.DataFrame()},
    {"side_effect": RuntimeError("Yahoo down")},
])
def test_yfinance_failure_falls_back_to_tiingo(with_key, yf):
    with patch.object(prices_mod, "_fetch_yfinance", **yf), \
         patch.object(prices_mod.requests, "get", return_value=_Resp(200, [_row("2026-01-02")])):
        source, df = _fetch_single("BOTZ", "2026-01-01", "2026-02-01", fallback=True)
    assert source == "tiingo"
    assert df["Close"].iloc[0] == 100


def test_with_no_key_a_yfinance_failure_is_just_a_failure(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with patch.object(prices_mod, "_fetch_yfinance", return_value=pd.DataFrame()), \
         patch.object(prices_mod.requests, "get") as get:
        assert _fetch_single("BOTZ", "2026-01-01", "2026-02-01", fallback=True) == (None, None)
    get.assert_not_called()


def test_a_failing_request_logs_its_type_but_never_the_url_or_the_token(with_key, caplog):
    """requests puts the full request URL in its exception text."""
    boom = requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: "
                                    f"/tiingo/daily/BOTZ/prices?token={KEY}")
    with patch.object(prices_mod, "_fetch_yfinance", return_value=pd.DataFrame()), \
         patch.object(prices_mod.requests, "get", side_effect=boom), \
         caplog.at_level(logging.DEBUG):
        assert _fetch_single("BOTZ", "2026-01-01", "2026-02-01", fallback=True) == (None, None)
    text = caplog.text
    assert "ConnectionError" in text
    assert KEY not in text and "api.tiingo.com" not in text and "/tiingo/daily" not in text


# --- fetch_prices: rate limit, counts, cache ----------------------------------

def _run(tmp_path, tickers, get):
    stats: dict = {}
    with patch.object(prices_mod, "_fetch_yfinance", return_value=pd.DataFrame()), \
         patch.object(prices_mod.requests, "get", get):
        out = fetch_prices(tickers, "2026-01-01", "2026-02-01",
                           cache_dir=str(tmp_path), stats_out=stats, fallback=True)
    return out, stats


def test_fallback_results_are_counted_and_cached(with_key, tmp_path):
    out, stats = _run(tmp_path, ["BOTZ", "SOXX"],
                      lambda *a, **k: _Resp(200, [_row("2026-01-02")]))
    assert set(out) == {"BOTZ", "SOXX"}
    assert stats["tiingo"] == 2 and stats["yfinance"] == 0
    assert (tmp_path / "BOTZ_prices.parquet").exists()


def test_a_rate_limit_pauses_further_calls(with_key, tmp_path):
    calls = []

    def get(*a, **k):
        calls.append(a[0])
        return _Resp(429, None)

    out, stats = _run(tmp_path, ["BOTZ", "SOXX", "URA"], get)
    assert out == {} and stats["tiingo"] == 0
    assert len(calls) == 1, "kept asking Tiingo after it said 429"


def test_the_pause_spans_fetch_prices_calls_and_ends_after_an_hour(with_key, tmp_path):
    """One job calls fetch_prices several times (scan, then the dashboard
    builders); a pause cleared per call would ask once more each time."""
    _run(tmp_path, ["BOTZ"], lambda *a, **k: _Resp(429, None))
    calls = []

    def get(*a, **k):
        calls.append(1)
        return _Resp(200, [_row("2026-01-02")])

    out, _ = _run(tmp_path, ["BOTZ"], get)                 # a second fetch_prices call
    assert out == {} and calls == [], "asked Tiingo again inside the pause"
    later = time.monotonic() + prices_mod._RATE_LIMIT_PAUSE_S + 1
    with patch.object(prices_mod.time, "monotonic", return_value=later):
        out, stats = _run(tmp_path, ["BOTZ"], get)
    assert "BOTZ" in out and stats["tiingo"] == 1


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_stops_the_fallback_for_good(with_key, tmp_path, status, caplog):
    calls = []

    def get(*a, **k):
        calls.append(1)
        return _Resp(status, None)

    with caplog.at_level(logging.WARNING):
        out, _ = _run(tmp_path, ["BOTZ", "SOXX"], get)
    assert out == {} and len(calls) == 1
    assert prices_mod._tiingo_off_until == math.inf
    assert "rejected TIINGO_API_KEY" in caplog.text and KEY not in caplog.text


def test_a_caller_that_does_not_opt_in_never_reaches_tiingo(with_key, tmp_path):
    """The UCITS monitor's exchange-suffixed tickers and the dashboard builders
    call fetch_prices without fallback=True: Tiingo is US-only and 50 an hour."""
    with patch.object(prices_mod, "_fetch_yfinance", return_value=pd.DataFrame()), \
         patch.object(prices_mod.requests, "get") as get:
        out = fetch_prices(["BOTZ"], "2026-01-01", "2026-02-01", cache_dir=str(tmp_path))
    assert out == {}
    get.assert_not_called()


def test_a_short_series_is_still_used_but_the_lag_is_logged(with_key, tmp_path, caplog):
    """Tiingo's newest bar can lag Yahoo's: say so, let alignment cope."""
    with caplog.at_level(logging.WARNING):
        out, _ = _run(tmp_path, ["BOTZ"], lambda *a, **k: _Resp(200, [_row("2026-01-02")]))
    assert "BOTZ" in out
    assert "newest bar for BOTZ is 2026-01-02, expected 2026-01-30" in caplog.text


def test_other_http_errors_do_not_stop_the_run(with_key, tmp_path):
    responses = iter([_Resp(500, None), _Resp(200, [_row("2026-01-02")])])
    out, stats = _run(tmp_path, ["BOTZ", "SOXX"], lambda *a, **k: next(responses))
    assert set(out) == {"SOXX"} and stats["tiingo"] == 1
