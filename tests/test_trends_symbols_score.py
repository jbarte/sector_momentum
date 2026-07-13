import math

import pandas as pd
from src.data.trends_symbols import _cross_zscore, score_symbol_sentiment


def test_cross_zscore_centers_and_scales():
    z = _cross_zscore({"a": 1.0, "b": 2.0, "c": 3.0})
    assert abs(sum(z.values())) < 1e-9          # mean ~0
    assert z["a"] < z["b"] < z["c"]             # order preserved


def test_cross_zscore_excludes_nan_and_handles_degenerate():
    # NaN inputs pass through as NaN, excluded from the mean/std.
    z = _cross_zscore({"a": 1.0, "b": 2.0, "c": float("nan")})
    assert math.isnan(z["c"])
    assert abs(z["a"] + z["b"]) < 1e-9
    # all-equal → all 0.0 (std == 0)
    assert _cross_zscore({"a": 5.0, "b": 5.0}) == {"a": 0.0, "b": 0.0}
    # fewer than two valid values → 0.0
    assert _cross_zscore({"a": 7.0}) == {"a": 0.0}


def test_rising_key_scores_above_falling():
    trends = {
        "US|Technology": pd.Series([1.0, 2.0, 3.0, 4.0]),   # rising
        "US|Energy": pd.Series([4.0, 3.0, 2.0, 1.0]),       # falling
        "US|Utilities": pd.Series([2.0, 2.0, 2.0, 2.0]),    # flat
    }
    s = score_symbol_sentiment(trends)
    assert set(s.index) == set(trends)
    assert s["US|Technology"] > s["US|Utilities"] > s["US|Energy"]
    # cross-sectional z is centered near zero
    assert abs(s.mean()) < 1e-9


def test_aggregate_omits_dead_key():
    """A sector-key with no live symbols is absent from _aggregate's result."""
    from src.data.trends_symbols import _aggregate
    norm_by_symbol = {
        "AAPL": [1.0, 2.0, 3.0],
        "DEAD": [0.0, 0.0, 0.0],
    }
    symbol_map = {
        "US|Technology": ["AAPL"],
        "US|Energy": ["DEAD"],
    }
    result = _aggregate(norm_by_symbol, symbol_map, window=3)
    assert "US|Technology" in result
    assert "US|Energy" not in result, "Dead key should be omitted, not zero-filled"


def test_aggregate_omits_key_with_missing_symbols():
    """A sector-key whose symbols aren't in norm_by_symbol at all is omitted."""
    from src.data.trends_symbols import _aggregate
    norm_by_symbol = {"AAPL": [1.0, 2.0, 3.0]}
    symbol_map = {
        "US|Technology": ["AAPL"],
        "US|Energy": ["XOM"],
    }
    result = _aggregate(norm_by_symbol, symbol_map, window=3)
    assert "US|Technology" in result
    assert "US|Energy" not in result
