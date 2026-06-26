# tests/test_backtest_engine.py
import numpy as np
import pandas as pd

from src.backtest import engine


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE", "Health": "XLV"},
        "eu_sectors": {},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def test_run_track_produces_curve_and_metrics():
    n = 600
    prices = {
        "XLK": _ramp(n, 100, 0.9),
        "XLE": _ramp(n, 100, 0.2),
        "XLV": _ramp(n, 100, 0.5),
        "RSP": _ramp(n, 100, 0.4),
    }
    instrument_of = {"US|Technology": "XLK", "US|Energy": "XLE", "US|Health": "XLV"}
    track = engine.run_track(_universe(), prices, "US", "RSP", instrument_of, top_n=2)
    assert track is not None
    assert track["region"] == "US"
    assert len(track["equity_curve"]) > 0
    assert "cagr" in track["metrics"]
    # Strongly-trending instruments held -> positive total return
    assert track["metrics"]["total_return"] > 0


def test_run_all_handles_missing_eu_gracefully():
    n = 400
    prices = {"XLK": _ramp(n, 100, 0.9), "XLE": _ramp(n, 100, 0.2),
              "XLV": _ramp(n, 100, 0.5), "RSP": _ramp(n, 100, 0.4)}
    # No EU tickers in prices at all
    result = engine.run_all(_universe(), prices, top_n=2)
    assert result["US"] is not None
    assert result["EU"] is None
