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


# ---------------------------------------------------------------------------
# `since` — bounding the evaluation window without starving the warm-up
# ---------------------------------------------------------------------------
# The sweep used to pass its --start straight to fetch_prices, so evaluation
# began on the first fetched bar with no history behind it. Signals that need a
# trailing window return NaN there (compute_ma_structure needs 200 bars for
# above_200dma), so the opening months of every windowed run scored on a
# degraded signal set. On a 2008 start that is the whole crash, and it was
# enough to reverse which horizon preset looked best. Fetch full history,
# then bound the calendar with `since`.


def test_rebalance_dates_since_drops_earlier_periods():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    full = replay.rebalance_dates(idx, "M")
    bounded = replay.rebalance_dates(idx, "M", since="2020-07-01")
    assert bounded == [d for d in full if d >= pd.Timestamp("2020-07-01")]
    assert len(bounded) == 6


def test_rebalance_dates_since_accepts_timestamp_and_none():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    full = replay.rebalance_dates(idx, "M")
    assert replay.rebalance_dates(idx, "M", since=None) == full
    assert (replay.rebalance_dates(idx, "M", since=pd.Timestamp("2020-07-01"))
            == replay.rebalance_dates(idx, "M", since="2020-07-01"))


def test_rebalance_dates_since_preserves_multi_period_parity():
    """Bounding must happen AFTER the period grouping, not by slicing the index.

    `2M` takes every second period end counting from the start of the index, so
    slicing the index first silently flips which months are review months —
    the UI and the backtest would then disagree about the same preset.
    """
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    full = replay.rebalance_dates(idx, "2M")
    # Cut mid-parity: slicing at a January would preserve the month set by luck
    # and prove nothing, since the index also starts in January.
    cutoff = pd.Timestamp("2021-02-01")
    bounded = replay.rebalance_dates(idx, "2M", since=cutoff)
    assert bounded == [d for d in full if d >= cutoff]
    assert [d.month for d in bounded] == [3, 5, 7, 9, 11]
    # The naive fix (slice, then group) shifts onto the other parity.
    sliced = replay.rebalance_dates(idx[idx >= cutoff], "2M")
    assert [d.month for d in sliced] == [2, 4, 6, 8, 10, 12]
    assert bounded != sliced


def test_rebalance_dates_since_after_last_date_is_empty():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    assert replay.rebalance_dates(idx, "M", since="2021-06-01") == []
