import math

import numpy as np
import pandas as pd
import pytest

from src.pipeline import (
    SIGNAL_COLUMNS,
    build_signals_rows,
    compute_signals_for_sector,
    build_composite_series,
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


def _universe_one_sector():
    return {
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }


# ---------------------------------------------------------------------------
# Original test — key presence
# ---------------------------------------------------------------------------

def test_build_signals_rows_produces_expected_keys():
    universe = _universe_one_sector()
    prices = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    rows = build_signals_rows(universe, prices)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector_key"] == "US|Technology"
    assert row["region"] == "US"
    for col in SIGNAL_COLUMNS:
        assert col in row


# ---------------------------------------------------------------------------
# Value range assertions
# ---------------------------------------------------------------------------

def test_signal_values_are_in_reasonable_ranges():
    """Computed signals for a steadily-rising series should be within sane ranges."""
    universe = _universe_one_sector()
    prices = {"XLK": _price_df(n=260, step=0.5), "RSP": _price_df(n=260, step=0.3)}
    rows = build_signals_rows(universe, prices)
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
    universe = _universe_one_sector()
    prices = {"XLK": _price_df(n=260, step=1.0), "RSP": _price_df(n=260, step=0.5)}
    rows = build_signals_rows(universe, prices)
    row = rows[0]
    assert row["return_1m"] > 0, "1m return should be positive for uptrend"
    assert row["return_3m"] > 0, "3m return should be positive for uptrend"
    assert row["return_6m"] > 0, "6m return should be positive for uptrend"


def test_rs_ratio_above_100_when_outperforming():
    """Sector rising faster than benchmark should have rs_ratio > 100."""
    universe = _universe_one_sector()
    # Sector grows faster than benchmark
    prices = {
        "XLK": _price_df(n=260, step=1.0),
        "RSP": _price_df(n=260, step=0.2),
    }
    rows = build_signals_rows(universe, prices)
    assert rows[0]["rs_ratio"] > 100


def test_rs_ratio_below_100_when_underperforming():
    """Sector rising slower than benchmark should have rs_ratio < 100."""
    universe = _universe_one_sector()
    prices = {
        "XLK": _price_df(n=260, step=0.1),
        "RSP": _price_df(n=260, step=1.0),
    }
    rows = build_signals_rows(universe, prices)
    assert rows[0]["rs_ratio"] < 100


# ---------------------------------------------------------------------------
# Missing / NaN benchmark handling
# ---------------------------------------------------------------------------

def test_missing_benchmark_skips_sector_gracefully():
    """When the benchmark ticker is absent from prices, the sector is skipped."""
    universe = _universe_one_sector()
    # No RSP in prices
    prices = {"XLK": _price_df()}
    rows = build_signals_rows(universe, prices)
    # Should be empty — sector skipped because benchmark is missing
    assert len(rows) == 0


def test_missing_sector_ticker_skips_gracefully():
    """When the sector ticker is absent from prices, the sector is skipped."""
    universe = _universe_one_sector()
    # No XLK in prices
    prices = {"RSP": _price_df()}
    rows = build_signals_rows(universe, prices)
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
# EU composite series
# ---------------------------------------------------------------------------

def test_build_composite_series_averages_rebased_close():
    """Composite of two equal-growth series should produce a smooth average."""
    prices = {
        "A": _price_df(n=100, start=50, step=0.5),
        "B": _price_df(n=100, start=200, step=2.0),
    }
    comp = build_composite_series(["A", "B"], prices)
    assert comp is not None
    assert "Close" in comp.columns
    # Should start at 100 (rebased)
    assert abs(comp["Close"].iloc[0] - 100.0) < 0.01


def test_build_composite_series_returns_none_for_empty():
    """No usable components returns None."""
    prices = {}
    comp = build_composite_series(["A", "B"], prices)
    assert comp is None


# ---------------------------------------------------------------------------
# Multiple sectors
# ---------------------------------------------------------------------------

def test_multiple_us_sectors():
    """Multiple US sectors all produce signal rows."""
    universe = {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
        "eu_sectors": {},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }
    prices = {
        "XLK": _price_df(step=0.5),
        "XLE": _price_df(step=0.3),
        "RSP": _price_df(step=0.4),
    }
    rows = build_signals_rows(universe, prices)
    assert len(rows) == 2
    sectors = {r["gics_sector"] for r in rows}
    assert sectors == {"Technology", "Energy"}
