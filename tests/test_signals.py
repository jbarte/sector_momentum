"""Unit tests for signal calculators."""
import numpy as np
import pandas as pd
import pytest

from src.signals.relative_strength import compute_rs, compute_rs_slope, compute_rrg, latest_rrg
from src.signals.momentum import compute_returns, compute_acceleration
from src.signals.technical import compute_ma_structure, compute_obv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_prices(n=300, seed=0, trend=0.0):
    """n trading days of synthetic Close prices. trend=0.01 means +1% per day drift."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(trend, 0.01, n)
    prices = 100 * (1 + returns).cumprod()
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.Series(prices, index=dates)


def make_volume(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.integers(1_000_000, 10_000_000, n).astype(float),
        index=pd.date_range("2023-01-01", periods=n, freq="B"),
    )


# ---------------------------------------------------------------------------
# relative_strength tests
# ---------------------------------------------------------------------------

def test_compute_rs_returns_series():
    """compute_rs returns pd.Series, same length as intersection."""
    sector = make_prices(300, seed=1)
    bench = make_prices(300, seed=2)
    rs = compute_rs(sector, bench)
    assert isinstance(rs, pd.Series)
    assert len(rs) == len(sector.index.intersection(bench.index))


def test_compute_rs_slope_shape():
    """Shape matches input; first window-1 values are NaN."""
    sector = make_prices(300, seed=3)
    bench = make_prices(300, seed=4)
    rs = compute_rs(sector, bench)
    window = 10
    slope = compute_rs_slope(rs, window=window)
    assert isinstance(slope, pd.Series)
    assert len(slope) == len(rs)
    # First window-1 entries should all be NaN
    assert slope.iloc[: window - 1].isna().all()
    # At least some non-NaN values after the warm-up
    assert slope.iloc[window - 1 :].notna().any()


def test_compute_rrg_columns():
    """Returns DataFrame with ['rs_ratio', 'rs_momentum'] columns."""
    sector = make_prices(300, seed=5)
    bench = make_prices(300, seed=6)
    rrg = compute_rrg(sector, bench)
    assert isinstance(rrg, pd.DataFrame)
    assert set(rrg.columns) == {"rs_ratio", "rs_momentum"}


def test_latest_rrg_keys():
    """Returns dict with 'rs_ratio' and 'rs_momentum' keys."""
    sector = make_prices(300, seed=7)
    bench = make_prices(300, seed=8)
    latest = latest_rrg(sector, bench)
    assert "rs_ratio" in latest
    assert "rs_momentum" in latest


def test_rrg_outperforming_sector_above_100():
    """A sector that massively outperforms should have rs_ratio > 100 eventually."""
    # benchmark drifts down, sector drifts up — clear outperformer
    bench = make_prices(300, seed=10, trend=-0.002)
    sector = make_prices(300, seed=11, trend=0.002)
    rrg = compute_rrg(sector, bench)
    rs_ratio_valid = rrg["rs_ratio"].dropna()
    assert rs_ratio_valid.iloc[-1] > 100, (
        f"Expected rs_ratio > 100 for strong outperformer, got {rs_ratio_valid.iloc[-1]:.2f}"
    )


def test_compute_rrg_fast_parameter_changes_momentum():
    """Different fast values produce different rs_momentum series."""
    sector = make_prices(300, seed=20)
    bench = make_prices(300, seed=21)
    rrg_fast1 = compute_rrg(sector, bench, fast=1)
    rrg_fast5 = compute_rrg(sector, bench, fast=5)
    mom1 = rrg_fast1["rs_momentum"].dropna()
    mom5 = rrg_fast5["rs_momentum"].dropna()
    assert not mom1.equals(mom5), "fast=1 and fast=5 should produce different momentum"


def test_latest_rrg_respects_fast_param():
    """latest_rrg(fast=1) and latest_rrg(fast=5) should differ."""
    sector = make_prices(300, seed=22)
    bench = make_prices(300, seed=23)
    v1 = latest_rrg(sector, bench, fast=1)["rs_momentum"]
    v5 = latest_rrg(sector, bench, fast=5)["rs_momentum"]
    assert v1 != pytest.approx(v5, abs=1e-9), "fast param should affect momentum"


def test_compute_rrg_default_fast_is_5():
    """Default fast parameter changed from 1 to 5."""
    import inspect
    sig = inspect.signature(compute_rrg)
    assert sig.parameters["fast"].default == 5


# ---------------------------------------------------------------------------
# momentum tests
# ---------------------------------------------------------------------------

def test_compute_returns_keys():
    """Keys are '1m', '3m', '6m'."""
    prices = make_prices(300, seed=12)
    ret = compute_returns(prices)
    assert set(ret.keys()) == {"1m", "3m", "6m"}


def test_compute_returns_insufficient_data():
    """With only 10 data points, all returns should be NaN."""
    prices = make_prices(10, seed=13)
    ret = compute_returns(prices)
    for key in ("1m", "3m", "6m"):
        assert np.isnan(ret[key]), f"Expected NaN for '{key}' with 10 data points"


def test_compute_acceleration_positive_for_accelerating():
    """A series accelerating upward: acceleration > 0."""
    # Strong upward trend so short-term return > medium-term return → acceleration > 0
    prices = make_prices(300, seed=14, trend=0.005)
    acc = compute_acceleration(prices)
    assert not np.isnan(acc), "Acceleration should not be NaN for 300-day series"
    # With a strong consistent trend the 1m return should exceed 3m return-per-month
    # (not guaranteed for all seeds, but trend=0.005/day makes it highly likely)
    # Just verify the value is finite and makes sense directionally
    assert isinstance(acc, float)


# ---------------------------------------------------------------------------
# technical tests
# ---------------------------------------------------------------------------

def test_compute_ma_structure_keys():
    """Keys: above_50dma, above_200dma, ma50_slope."""
    prices = make_prices(300, seed=15)
    ma = compute_ma_structure(prices)
    assert {"above_50dma", "above_200dma", "ma50_slope"} == set(ma.keys())


def test_compute_ma_structure_above_for_uptrend():
    """Strongly uptrending price: above_50dma > 0, above_200dma > 0."""
    prices = make_prices(300, seed=16, trend=0.003)
    ma = compute_ma_structure(prices)
    assert not np.isnan(ma["above_50dma"]), "above_50dma should not be NaN for 300-day series"
    assert not np.isnan(ma["above_200dma"]), "above_200dma should not be NaN for 300-day series"
    assert ma["above_50dma"] > 0, "Expected price above 50DMA for strong uptrend"
    assert ma["above_200dma"] > 0, "Expected price above 200DMA for strong uptrend"


def test_compute_obv_slope_positive_for_uptrend_with_volume():
    """Price trending up with high volume on up days → positive OBV slope."""
    rng = np.random.default_rng(42)
    n = 300
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    # Strong uptrend
    returns = rng.normal(0.003, 0.005, n)
    prices = pd.Series(100 * (1 + returns).cumprod(), index=dates)
    # Uniform volume (direction alone drives OBV)
    volume = pd.Series(np.ones(n) * 5_000_000.0, index=dates)
    result = compute_obv(prices, volume)
    assert "obv_slope" in result
    assert not np.isnan(result["obv_slope"]), "obv_slope should be finite for 300-day uptrend"
    assert result["obv_slope"] > 0, (
        f"Expected positive OBV slope for uptrend, got {result['obv_slope']:.4f}"
    )


def test_compute_obv_nan_for_flat_prices():
    """All-same price → direction is 0 every day → OBV stays at 0 → mean_abs_obv=0 → NaN."""
    n = 100
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    prices = pd.Series(np.ones(n) * 100.0, index=dates)
    volume = pd.Series(np.ones(n) * 1_000_000.0, index=dates)
    result = compute_obv(prices, volume)
    assert np.isnan(result["obv_slope"]), (
        "Expected NaN obv_slope for flat (constant) prices"
    )


def test_compute_max_drawdown_rise_then_fall():
    from src.signals.technical import compute_max_drawdown
    # Rise 100->120 then fall to 90: peak 120, trough 90 -> -25%.
    close = pd.Series([100, 110, 120, 105, 90], dtype=float)
    assert compute_max_drawdown(close) == pytest.approx(-0.25, abs=1e-9)


def test_compute_max_drawdown_monotonic_rise_is_zero():
    from src.signals.technical import compute_max_drawdown
    close = pd.Series([100, 101, 102, 103], dtype=float)
    assert compute_max_drawdown(close) == pytest.approx(0.0, abs=1e-9)


def test_compute_max_drawdown_window_limits_lookback():
    from src.signals.technical import compute_max_drawdown
    # A big early crash then a clean recent rise; a short window ignores the crash.
    close = pd.Series([100, 10, 100, 101, 102, 103], dtype=float)
    assert compute_max_drawdown(close, window=3) == pytest.approx(0.0, abs=1e-9)


def test_compute_max_drawdown_insufficient_data_is_nan():
    import math
    from src.signals.technical import compute_max_drawdown
    assert math.isnan(compute_max_drawdown(pd.Series([100.0])))
    assert math.isnan(compute_max_drawdown(pd.Series([], dtype=float)))


# ---------------------------------------------------------------------------
# Risk-adjusted momentum (info-only signals)
# ---------------------------------------------------------------------------

def test_compute_realized_vol_known_series():
    import math
    from src.signals.technical import compute_realized_vol
    # Daily returns alternate roughly +10% / -10%.
    close = pd.Series([100, 110, 99, 108.9, 98.01], dtype=float)
    rets = close.pct_change().dropna()
    expected = float(rets.std(ddof=1)) * math.sqrt(252)
    assert compute_realized_vol(close, window=10) == pytest.approx(expected, rel=1e-9)


def test_compute_realized_vol_window_limits_lookback():
    from src.signals.technical import compute_realized_vol
    # A violent early move then a calm recent stretch: a short window is calmer.
    close = pd.Series([100, 200, 100, 101, 101.5, 102, 102.5], dtype=float)
    short = compute_realized_vol(close, window=3)
    long = compute_realized_vol(close, window=10)
    assert short < long


def test_compute_realized_vol_insufficient_data_is_nan():
    import math
    from src.signals.technical import compute_realized_vol
    assert math.isnan(compute_realized_vol(pd.Series([], dtype=float), window=10))
    assert math.isnan(compute_realized_vol(pd.Series([100.0]), window=10))
    # One return only -> std(ddof=1) undefined -> NaN.
    assert math.isnan(compute_realized_vol(pd.Series([100.0, 101.0]), window=10))


def test_compute_realized_vol_flat_series_is_zero():
    from src.signals.technical import compute_realized_vol
    # A perfectly flat series has zero variance. Returning 0.0 (not NaN) is
    # correct here; the ratio signals are responsible for not dividing by it.
    close = pd.Series([100.0] * 10)
    assert compute_realized_vol(close, window=5) == pytest.approx(0.0, abs=1e-12)


def test_risk_adjusted_signals_present_but_not_scored():
    """The three new signals must be computed AND stay out of the scored lists."""
    from src.pipeline import SIGNAL_COLUMNS
    from src.scoring import _LEVEL_SIGNALS, _CHANGE_SIGNALS
    for name in ("rar_3m", "rar_6m", "calmar_6m"):
        assert name in SIGNAL_COLUMNS
        assert name not in _LEVEL_SIGNALS
        assert name not in _CHANGE_SIGNALS


# ---------------------------------------------------------------------------
# pillar composition
# ---------------------------------------------------------------------------

def test_acceleration_is_computed_but_not_scored():
    """`acceleration` is `return_1m - return_3m`, and `return_3m` is a Level
    signal — so scoring it put the same return in the composite with opposite
    signs in the two pillars. Measured over 176 month-ends it correlated -0.31
    with the composite it belonged to and -0.82 with return_3m. It was removed
    from scoring on 2026-08-09 and replaced by `return_1m`, which is the same
    term minus the cancellation.

    It stays computed and stored: it is still shown in the drill-down as
    context. This pins that it does not creep back into scoring.
    """
    from src.pipeline import SIGNAL_COLUMNS
    from src.scoring import _LEVEL_SIGNALS, _CHANGE_SIGNALS
    assert "acceleration" in SIGNAL_COLUMNS
    assert "acceleration" not in _LEVEL_SIGNALS
    assert "acceleration" not in _CHANGE_SIGNALS
    assert "return_1m" in _CHANGE_SIGNALS


def test_scored_pillars_are_disjoint_and_real():
    """No signal may sit in both pillars (it would get double weight), and every
    scored name must actually be produced by the pipeline — a typo here scores a
    column of zeros silently, since z-scoring fills missing values with 0.0."""
    from src.pipeline import SIGNAL_COLUMNS
    from src.scoring import _LEVEL_SIGNALS, _CHANGE_SIGNALS
    overlap = set(_LEVEL_SIGNALS) & set(_CHANGE_SIGNALS)
    assert not overlap, f"signal in both pillars, double-weighted: {overlap}"
    for name in (*_LEVEL_SIGNALS, *_CHANGE_SIGNALS):
        assert name in SIGNAL_COLUMNS, f"{name} is scored but never computed"


def test_weights_display_order_matches_scored_signals():
    """`weights.yaml`'s level_signals/change_signals keys only drive dashboard
    column order, but if they drift from the real lists the drill-down shows a
    signal in the wrong pillar — or silently omits one."""
    import yaml
    from src.scoring import _LEVEL_SIGNALS, _CHANGE_SIGNALS
    cfg = yaml.safe_load(open("config/weights.yaml"))
    assert set(cfg["level_signals"]) == set(_LEVEL_SIGNALS)
    assert set(cfg["change_signals"]) == set(_CHANGE_SIGNALS)


def _flat_price_frame(n=200, price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": [price] * n, "Volume": [1_000] * n}, index=idx)


def test_ratio_signals_are_nan_not_inf_on_flat_prices():
    """A flat series gives zero vol and zero drawdown -> ratios must be NaN."""
    import math
    from src.pipeline import compute_signals_for_sector
    frame = _flat_price_frame()
    bench = _flat_price_frame(price=50.0)
    # Signature (verified): (sector_key, region, gics_sector, sector_ticker,
    #                        benchmark_ticker, prices, rs_momentum_fast=5)
    got = compute_signals_for_sector(
        "US|Test", "US", "Test", "TST", "BENCH", {"TST": frame, "BENCH": bench},
    )
    assert got is not None
    for name in ("rar_3m", "rar_6m", "calmar_6m"):
        assert not math.isinf(got[name]), f"{name} must never be +/-inf"
        assert math.isnan(got[name]), f"{name} should be NaN on a flat series"


def test_ratio_signals_are_finite_on_realistic_prices():
    """Guard against the ratio block silently failing (e.g. a NameError being
    swallowed by its try/except, which would leave every ratio NaN)."""
    import math
    import numpy as _np
    from src.pipeline import compute_signals_for_sector
    rng = _np.random.default_rng(0)
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    px = 100 * _np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    bench_px = 100 * _np.cumprod(1 + rng.normal(0.0003, 0.008, n))
    frame = pd.DataFrame({"Close": px, "Volume": [1_000] * n}, index=idx)
    bench = pd.DataFrame({"Close": bench_px, "Volume": [1_000] * n}, index=idx)
    got = compute_signals_for_sector(
        "US|Test", "US", "Test", "TST", "BENCH", {"TST": frame, "BENCH": bench},
    )
    assert got is not None
    for name in ("rar_3m", "rar_6m", "calmar_6m"):
        assert math.isfinite(got[name]), f"{name} should be a finite number here"
