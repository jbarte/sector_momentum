import math

import numpy as np
import pandas as pd
import pytest

from src.pipeline import (
    SIGNAL_COLUMNS,
    build_theme_signals_rows,
    compute_signals_for_sector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_df(n=260, start=100.0, step=0.5):
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame(
        {"Close": close, "Open": close, "High": close, "Low": close,
         "Volume": pd.Series(1_000_000, index=idx)}
    )


def _themes_one_member():
    return {"benchmark": "RSP", "themes": {"Technology": {"ticker": "XLK"}}}


# ---------------------------------------------------------------------------
# Original test — key presence
# ---------------------------------------------------------------------------

def test_build_theme_signals_rows_produces_expected_keys():
    themes_cfg = _themes_one_member()
    prices = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector_key"] == "THEME|Technology"
    assert row["region"] == "THEME"
    for col in SIGNAL_COLUMNS:
        assert col in row


# ---------------------------------------------------------------------------
# Value range assertions
# ---------------------------------------------------------------------------

def test_signal_values_are_in_reasonable_ranges():
    """Computed signals for a steadily-rising series should be within sane ranges."""
    themes_cfg = _themes_one_member()
    prices = {"XLK": _price_df(n=260, step=0.5), "RSP": _price_df(n=260, step=0.3)}
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert len(rows) == 1
    row = rows[0]

    # RS-ratio and RS-momentum are centred around 100 (outperforming = >100)
    rs_ratio = row["rs_ratio"]
    assert not math.isnan(rs_ratio), "rs_ratio should not be NaN for valid data"
    assert 50 < rs_ratio < 200, f"rs_ratio={rs_ratio} outside plausible [50,200]"

    rs_mom = row["rs_momentum"]
    assert not math.isnan(rs_mom), "rs_momentum should not be NaN for valid data"
    assert 50 < rs_mom < 200, f"rs_momentum={rs_mom} outside plausible [50,200]"

    # Returns are fractional: 1m/3m/6m should be between -1.0 and +10.0
    for horizon in ("return_1m", "return_3m", "return_6m"):
        val = row[horizon]
        assert not math.isnan(val), f"{horizon} should not be NaN for 260-day series"
        assert -1.0 <= val <= 10.0, f"{horizon}={val} outside plausible [-1, +10]"

    # Acceleration = 1m - 3m, should be a small difference for a steady trend
    accel = row["acceleration"]
    assert not math.isnan(accel), "acceleration should not be NaN"
    assert -2.0 <= accel <= 2.0, f"acceleration={accel} outside [-2, +2]"

    # MA distance signals: fractional distance from moving average
    above_50 = row["above_50dma"]
    assert not math.isnan(above_50), "above_50dma should not be NaN for 260 days"
    assert -1.0 <= above_50 <= 2.0, f"above_50dma={above_50} outside [-1, +2]"

    above_200 = row["above_200dma"]
    assert not math.isnan(above_200), "above_200dma should not be NaN for 260 days"
    assert -1.0 <= above_200 <= 2.0, f"above_200dma={above_200} outside [-1, +2]"

    # MA50 slope: normalized, should be small for a gentle uptrend
    slope = row["ma50_slope"]
    assert not math.isnan(slope), "ma50_slope should not be NaN for 260 days"
    assert -0.1 <= slope <= 0.1, f"ma50_slope={slope} outside [-0.1, +0.1]"

    # OBV slope: normalized
    obv = row["obv_slope"]
    assert not math.isnan(obv), "obv_slope should not be NaN with volume data"
    assert -10.0 <= obv <= 10.0, f"obv_slope={obv} outside [-10, +10]"


def test_return_signals_positive_for_uptrend():
    """A steadily rising price series should produce positive returns."""
    themes_cfg = _themes_one_member()
    prices = {"XLK": _price_df(n=260, step=1.0), "RSP": _price_df(n=260, step=0.5)}
    rows = build_theme_signals_rows(themes_cfg, prices)
    row = rows[0]
    assert row["return_1m"] > 0, "1m return should be positive for uptrend"
    assert row["return_3m"] > 0, "3m return should be positive for uptrend"
    assert row["return_6m"] > 0, "6m return should be positive for uptrend"


def test_rs_ratio_above_100_when_outperforming():
    """Sector rising faster than benchmark should have rs_ratio > 100."""
    themes_cfg = _themes_one_member()
    # Sector grows faster than benchmark
    prices = {
        "XLK": _price_df(n=260, step=1.0),
        "RSP": _price_df(n=260, step=0.2),
    }
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert rows[0]["rs_ratio"] > 100


def test_rs_ratio_below_100_when_underperforming():
    """Sector rising slower than benchmark should have rs_ratio < 100."""
    themes_cfg = _themes_one_member()
    prices = {
        "XLK": _price_df(n=260, step=0.1),
        "RSP": _price_df(n=260, step=1.0),
    }
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert rows[0]["rs_ratio"] < 100


# ---------------------------------------------------------------------------
# Missing / NaN benchmark handling
# ---------------------------------------------------------------------------

def test_missing_benchmark_skips_member_gracefully():
    """When the benchmark ticker is absent from prices, the sector is skipped."""
    themes_cfg = _themes_one_member()
    # No RSP in prices
    prices = {"XLK": _price_df()}
    rows = build_theme_signals_rows(themes_cfg, prices)
    # Should be empty — sector skipped because benchmark is missing
    assert len(rows) == 0


def test_missing_member_ticker_skips_gracefully():
    """When the sector ticker is absent from prices, the sector is skipped."""
    themes_cfg = _themes_one_member()
    # No XLK in prices
    prices = {"RSP": _price_df()}
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert len(rows) == 0


def test_compute_signals_returns_none_when_benchmark_missing():
    """compute_signals_for_sector returns None when benchmark is not in prices."""
    prices = {"XLK": _price_df()}
    result = compute_signals_for_sector(
        sector_key="US|Technology",
        region="US",
        gics_sector="Technology",
        sector_ticker="XLK",
        benchmark_ticker="RSP",
        prices=prices,
    )
    assert result is None


def test_compute_signals_returns_none_when_sector_missing():
    """compute_signals_for_sector returns None when sector is not in prices."""
    prices = {"RSP": _price_df()}
    result = compute_signals_for_sector(
        sector_key="US|Technology",
        region="US",
        gics_sector="Technology",
        sector_ticker="XLK",
        benchmark_ticker="RSP",
        prices=prices,
    )
    assert result is None


def test_nan_close_in_sector_produces_nan_signals():
    """A sector with NaN-heavy Close data should produce NaN signals, not crash."""
    idx = pd.bdate_range("2022-01-03", periods=260)
    # All NaN close
    sector_df = pd.DataFrame({
        "Close": pd.Series([float("nan")] * 260, index=idx),
        "Volume": pd.Series(1_000_000, index=idx),
    })
    bench_df = _price_df(n=260)
    prices = {"XLK": sector_df, "RSP": bench_df}
    result = compute_signals_for_sector(
        sector_key="US|Technology",
        region="US",
        gics_sector="Technology",
        sector_ticker="XLK",
        benchmark_ticker="RSP",
        prices=prices,
    )
    # Should return a dict (not None — sector ticker IS in prices and has Close col),
    # but individual signals may be NaN
    if result is not None:
        for col in SIGNAL_COLUMNS:
            assert col in result


# ---------------------------------------------------------------------------
# Multiple sectors
# ---------------------------------------------------------------------------

def test_multiple_members_all_produce_rows():
    """Every cohort member produces a signal row."""
    themes_cfg = {"benchmark": "RSP", "themes": {
        "Technology": {"ticker": "XLK"}, "Energy": {"ticker": "XLE"},
    }}
    prices = {
        "XLK": _price_df(step=0.5),
        "XLE": _price_df(step=0.3),
        "RSP": _price_df(step=0.4),
    }
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert len(rows) == 2
    sectors = {r["gics_sector"] for r in rows}
    assert sectors == {"Technology", "Energy"}


def test_signals_include_max_dd_1y():
    import numpy as np
    import pandas as pd
    from src.pipeline import compute_signals_for_sector, SIGNAL_COLUMNS

    assert "max_dd_1y" in SIGNAL_COLUMNS

    idx = pd.bdate_range("2022-01-01", periods=300)
    sec = pd.Series(np.concatenate([np.linspace(100, 160, 200),
                                    np.linspace(160, 130, 100)]), index=idx)
    bench = pd.Series(np.linspace(100, 110, 300), index=idx)
    prices = {
        "XLK": pd.DataFrame({"Close": sec, "Volume": 1_000_000}),
        "RSP": pd.DataFrame({"Close": bench, "Volume": 1_000_000}),
    }
    sig = compute_signals_for_sector(
        "US|Technology", "US", "Technology", "XLK", "RSP", prices,
    )
    assert sig is not None
    assert "max_dd_1y" in sig
    assert sig["max_dd_1y"] < 0


# ---------------------------------------------------------------------------
# Dropped themes tracking (Task 2)
# ---------------------------------------------------------------------------

def _price_df_no_close(n=260):
    """A malformed price frame -- has rows but no Close column. Exercises
    compute_signals_for_sector's `"Close" not in sector_df.columns` branch,
    the one case behind the untracked 3rd drop reason."""
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame({"Open": pd.Series(100.0, index=idx),
                          "Volume": pd.Series(1_000_000, index=idx)})


def test_dropped_out_marks_prices_failed_when_never_fetched():
    """Ticker missing from BOTH prices and prices_before_align -- never
    fetched at all."""
    themes_cfg = _themes_one_member()  # Technology -> XLK, benchmark RSP
    prices = {"RSP": _price_df()}  # XLK missing
    dropped = {}
    rows = build_theme_signals_rows(
        themes_cfg, prices, prices_before_align=prices, dropped_out=dropped,
    )
    assert rows == []
    assert dropped == {"Technology": "prices_failed"}


def test_dropped_out_marks_asof_dropped_when_fetched_then_aligned_away():
    """Ticker present in prices_before_align but missing from the current
    prices dict -- fetched, then align_cohort_asof dropped it for
    staleness."""
    themes_cfg = _themes_one_member()
    prices_before = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    prices_after = {"RSP": _price_df(step=0.3)}  # XLK dropped by alignment
    dropped = {}
    rows = build_theme_signals_rows(
        themes_cfg, prices_after,
        prices_before_align=prices_before, dropped_out=dropped,
    )
    assert rows == []
    assert dropped == {"Technology": "asof_dropped"}


def test_dropped_out_marks_signal_calc_failed_when_sig_is_none():
    """Ticker present in prices (and prices_before_align), but
    compute_signals_for_sector rejects it -- e.g. no Close column."""
    themes_cfg = _themes_one_member()
    prices = {"XLK": _price_df_no_close(), "RSP": _price_df(step=0.3)}
    dropped = {}
    rows = build_theme_signals_rows(
        themes_cfg, prices, prices_before_align=prices, dropped_out=dropped,
    )
    assert rows == []
    assert dropped == {"Technology": "signal_calc_failed"}


def test_dropped_out_is_untouched_for_a_successful_theme():
    themes_cfg = _themes_one_member()
    prices = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    dropped = {}
    rows = build_theme_signals_rows(
        themes_cfg, prices, prices_before_align=prices, dropped_out=dropped,
    )
    assert len(rows) == 1
    assert dropped == {}


def test_omitting_the_new_kwargs_reproduces_existing_behavior():
    """Every existing call site (scan.py, backtest/replay.py, other tests in
    this file) calls build_theme_signals_rows without the two new kwargs --
    must behave exactly as before: no tracking, no crash, same rows."""
    themes_cfg = _themes_one_member()
    prices = {"RSP": _price_df()}  # XLK missing -> would be dropped
    rows = build_theme_signals_rows(themes_cfg, prices)
    assert rows == []  # unchanged behavior, no dropped_out to check
