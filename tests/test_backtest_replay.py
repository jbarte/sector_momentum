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


def test_score_themes_as_of_returns_scored_frame():
    themes_cfg = {
        "benchmark": "ACWI",
        "themes": {"Semiconductors": {"ticker": "SOXX"}, "Space": {"ticker": "UFO"}},
    }
    prices = {
        "SOXX": _ramp(300, 100, 0.8),
        "UFO": _ramp(300, 100, 0.1),
        "ACWI": _ramp(300, 100, 0.4),
    }
    scored = replay.score_themes_as_of(themes_cfg, prices, pd.Timestamp("2021-01-01"))
    assert scored is not None
    assert set(scored.index) == {"THEME|Semiconductors", "THEME|Space"}
    assert "composite" in scored.columns
    # Higher-trend SOXX should outrank UFO
    assert (scored.loc["THEME|Semiconductors", "composite"]
            > scored.loc["THEME|Space", "composite"])
