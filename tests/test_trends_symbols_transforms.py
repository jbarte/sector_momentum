import math

import numpy as np
import pandas as pd
from src.data.trends_symbols import (
    _slope, _normalize_by_anchor, _aggregate,
    _acceleration, _range_position, _spike_z, _volatility,
    derived_signals, DERIVED_SIGNAL_NAMES,
)


def test_slope_sign():
    assert _slope([1, 2, 3, 4]) > 0
    assert _slope([4, 3, 2, 1]) < 0
    assert _slope([0, 0, 0, 0]) == 0.0
    assert _slope([5]) == 0.0


def test_normalize_by_anchor_divides_and_drops_anchor():
    raw = {"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert "SPY" not in out
    assert out["XLK"] == [50.0, 100.0, 200.0]   # (x/anchor)*100


def test_normalize_anchor_all_zero_passthrough():
    raw = {"SPY": [0.0, 0.0], "XLK": [3.0, 4.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert out["XLK"] == [3.0, 4.0]


def test_aggregate_means_live_symbols_and_zeros_dead():
    norm = {"XLK": [2.0, 4.0], "VGT": [4.0, 8.0], "DEAD": [0.0, 0.0]}
    smap = {"US|Technology": ["XLK", "VGT", "DEAD"], "US|Energy": ["DEAD"]}
    agg = _aggregate(norm, smap, window=2)
    assert list(agg["US|Technology"]) == [3.0, 6.0]   # mean of XLK,VGT; DEAD excluded
    assert list(agg["US|Energy"]) == [0.0, 0.0]       # no live symbols


def test_acceleration_sign_and_guards():
    # first half flat, second half rising → positive acceleration
    assert _acceleration([1, 1, 1, 2, 4, 6]) > 0
    # first half rising, second half flat → negative acceleration
    assert _acceleration([1, 3, 5, 6, 6, 6]) < 0
    # too short for two 3-point halves → neutral
    assert _acceleration([1, 2, 3, 4]) == 0.0


def test_range_position_bounds():
    assert _range_position([0, 5, 10]) == 1.0        # latest at window high
    assert _range_position([10, 5, 0]) == 0.0        # latest at window low
    assert _range_position([2, 8, 5]) == 0.5         # midpoint of 2..8
    assert _range_position([3, 3, 3]) == 0.5         # flat → neutral
    assert _range_position([]) == 0.5


def test_spike_z_breakout_and_guards():
    # last point far above a mildly-varying baseline → large positive z
    assert _spike_z([10, 11, 9, 10, 30]) > 2
    # zero-variance baseline → undefined std → no spike
    assert _spike_z([10, 10, 10, 10, 30]) == 0.0
    # flat series → no spike
    assert _spike_z([5, 5, 5, 5]) == 0.0
    assert _spike_z([1, 2]) == 0.0                   # too short


def test_volatility_nonneg_and_ordering():
    steady = _volatility([100, 101, 102, 103, 104])
    choppy = _volatility([100, 130, 90, 140, 80])
    assert steady >= 0.0
    assert choppy > steady
    assert _volatility([5, 5, 5]) == 0.0             # flat → zero


def test_derived_signals_keys_and_momentum_matches_slope():
    series = [1, 2, 1, 3, 2, 4, 3, 5]
    out = derived_signals(series)
    assert set(out) == set(DERIVED_SIGNAL_NAMES)
    assert out["momentum"] == _slope(series)
    assert not any(math.isnan(v) for v in out.values())
