import numpy as np
import pandas as pd

from src.backtest import replay


def _ramp(n, start, step, vol=1_000_000):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(vol, index=idx)})


def test_month_end_dates_picks_last_trading_day_per_month():
    idx = pd.bdate_range("2021-01-01", "2021-03-31")
    ends = replay.month_end_dates(idx)
    # Last business days of Jan, Feb, Mar 2021
    assert ends[0] == pd.Timestamp("2021-01-29")
    assert ends[1] == pd.Timestamp("2021-02-26")
    assert ends[2] == pd.Timestamp("2021-03-31")


def test_truncate_prices_drops_future_rows():
    prices = {"XLK": _ramp(300, 100, 0.5)}
    cut = pd.Timestamp("2020-06-01")
    out = replay.truncate_prices(prices, cut)
    assert out["XLK"].index.max() <= cut


def test_score_as_of_returns_region_only_scored_frame():
    universe = {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }
    prices = {
        "XLK": _ramp(300, 100, 0.8),
        "XLE": _ramp(300, 100, 0.1),
        "RSP": _ramp(300, 100, 0.4),
        "EXV3.DE": _ramp(300, 100, 0.5),
        "EXSA.DE": _ramp(300, 100, 0.4),
    }
    scored = replay.score_as_of(universe, prices, pd.Timestamp("2021-01-01"), region="US")
    assert scored is not None
    assert set(scored.index) == {"US|Technology", "US|Energy"}
    assert "composite" in scored.columns
    # Higher-trend XLK should outrank XLE
    assert scored.loc["US|Technology", "composite"] > scored.loc["US|Energy", "composite"]
