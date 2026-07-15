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
    # Pad to meet _MIN_LIVE_SECTORS threshold
    from src.data.trends_symbols import _MIN_LIVE_SECTORS
    for i in range(_MIN_LIVE_SECTORS - len(trends)):
        trends[f"US|Pad{i}"] = pd.Series([float(i + 1)] * 4)
    s = score_symbol_sentiment(trends)
    assert s["US|Technology"] > s["US|Utilities"] > s["US|Energy"]


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


def test_score_below_threshold_returns_all_nan():
    """Fewer than _MIN_LIVE_SECTORS live keys -> all-NaN Series."""
    from src.data.trends_symbols import _MIN_LIVE_SECTORS
    trends = {
        f"US|Sector{i}": pd.Series([float(i)] * 4)
        for i in range(_MIN_LIVE_SECTORS - 1)
    }
    s = score_symbol_sentiment(trends)
    assert len(s) == len(trends)
    assert s.isna().all(), f"Expected all NaN, got {s.to_dict()}"


def test_score_at_threshold_returns_z_scores():
    """Exactly _MIN_LIVE_SECTORS live keys -> valid z-scores (not NaN)."""
    from src.data.trends_symbols import _MIN_LIVE_SECTORS
    trends = {
        f"US|Sector{i}": pd.Series([float(i + 1) * (j + 1) for j in range(13)])
        for i in range(_MIN_LIVE_SECTORS)
    }
    s = score_symbol_sentiment(trends)
    assert len(s) == _MIN_LIVE_SECTORS
    assert not s.isna().any(), f"Expected no NaN, got {s.to_dict()}"
    assert abs(s.mean()) < 1e-9, "Cross-sectional z should be centred near zero"


def test_score_slopes_trailing_momentum_window_only():
    """score_symbol_sentiment slopes the trailing _MOMENTUM_WINDOW weeks, not the full series."""
    from src.data.trends_symbols import _MOMENTUM_WINDOW
    n = 10
    trends_full = {}
    trends_tail = {}
    for i in range(n):
        ramp = [float(i + 1) * (j + 1) for j in range(_MOMENTUM_WINDOW)]
        prefix = [999.0] * (52 - _MOMENTUM_WINDOW)
        trends_full[f"US|S{i}"] = pd.Series(prefix + ramp)
        trends_tail[f"US|S{i}"] = pd.Series(ramp)
    s_full = score_symbol_sentiment(trends_full)
    s_tail = score_symbol_sentiment(trends_tail)
    pd.testing.assert_series_equal(s_full, s_tail, atol=1e-9)


def test_empty_trends_returns_empty_series():
    """Empty input -> empty Series (no crash)."""
    s = score_symbol_sentiment({})
    assert len(s) == 0


def test_min_live_override_lowers_threshold_for_small_cohorts():
    """A smaller cohort (e.g. themes) can pass a lower min_live and still score.

    Six live keys are below the default _MIN_LIVE_SECTORS (8) — all-NaN by
    default — but score as a valid cross-section when min_live is lowered.
    """
    trends = {
        f"THEME|T{i}": pd.Series([float(i + 1) * (j + 1) for j in range(13)])
        for i in range(6)
    }
    assert score_symbol_sentiment(trends).isna().all()          # default bar (8) → NaN
    s = score_symbol_sentiment(trends, min_live=5)              # theme bar (5) → z-scores
    assert not s.isna().any()
    assert abs(s.mean()) < 1e-9
