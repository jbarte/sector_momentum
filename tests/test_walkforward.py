"""Pure walk-forward selection/stitching logic."""
import numpy as np
import pandas as pd
import pytest

from src.backtest import metrics
from src.backtest.walkforward import (
    align_scheme_returns,
    count_switches,
    returns_from_equity_curve,
    select_scheme,
    stitch_walk_forward,
)


def _equity_curve(returns, start="2010-01-31"):
    """Build a run_track-style equity_curve from known monthly returns."""
    dates = pd.date_range(start, periods=len(returns) + 1, freq="ME")
    eq, v = [1.0], 1.0
    for r in returns:
        v *= (1.0 + r)
        eq.append(v)
    return [{"date": d.strftime("%Y-%m-%d"), "strategy": e, "benchmark": 1.0}
            for d, e in zip(dates, eq)]


def _series(vals, start="2010-01-31"):
    idx = pd.date_range(start, periods=len(vals), freq="ME")
    return pd.Series(vals, index=idx, dtype=float)


def test_returns_from_equity_curve_round_trips():
    src = [0.02, -0.01, 0.03, 0.00]
    got = returns_from_equity_curve(_equity_curve(src))
    assert len(got) == len(src)
    np.testing.assert_allclose(got.values, src, atol=1e-12)


def test_returns_from_equity_curve_handles_degenerate_input():
    assert returns_from_equity_curve([]).empty
    assert returns_from_equity_curve([{"date": "2010-01-31", "strategy": 1.0}]).empty


def test_returns_from_equity_curve_reads_benchmark_key():
    curve = [{"date": "2010-01-31", "strategy": 1.0, "benchmark": 1.0},
             {"date": "2010-02-28", "strategy": 1.5, "benchmark": 1.25}]
    got = returns_from_equity_curve(curve, key="benchmark")
    np.testing.assert_allclose(got.values, [0.25], atol=1e-12)


def test_align_scheme_returns_uses_shared_dates_only():
    a = _series([0.01, 0.02, 0.03])
    b = _series([0.04, 0.05], start="2010-02-28")
    df = align_scheme_returns({"a": a, "b": b})
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2                      # only the overlapping months survive
    assert df.index[0] == pd.Timestamp("2010-02-28")


def test_select_scheme_returns_none_before_first_full_window():
    df = align_scheme_returns({"a": _series([0.01] * 5), "b": _series([0.02] * 5)})
    assert select_scheme(df, pos=3, window=4) is None


def test_select_scheme_uses_only_trailing_window_not_the_future():
    """Look-ahead guard.

    Scheme "past" is clearly better over the 6 months BEFORE pos; scheme
    "future" is clearly better from pos onward. Selecting at pos must pick
    "past" -- an implementation that peeks at data from pos onward picks
    "future" and fails this test.

    The values deliberately alternate rather than being constant: a constant
    series has zero variance, which `metrics.sharpe` scores as 0.0, so both
    schemes would tie and selection would resolve by column order -- making the
    test pass even for a look-ahead implementation.
    """
    past   = [0.06, 0.04] * 3 + [-0.06, -0.04] * 3
    future = [-0.06, -0.04] * 3 + [0.06, 0.04] * 3
    df = align_scheme_returns({"past": _series(past), "future": _series(future)})

    assert select_scheme(df, pos=6, window=6) == "past"

    # Prove the fixture is discriminating: over the forward window the ranking flips.
    fwd = df.iloc[6:12]
    assert metrics.sharpe(fwd["future"]) > metrics.sharpe(fwd["past"])


def test_select_scheme_ranks_a_zero_variance_scheme_at_zero_sharpe():
    """`metrics.sharpe` returns 0.0 for a flat series (verified: it guards
    len<2 and sd==0), so a flat scheme is eligible but loses to any
    positive-Sharpe scheme and beats a negative one."""
    df = align_scheme_returns({"flat": _series([0.0] * 8),
                               "rising": _series([0.01, 0.02] * 4),
                               "falling": _series([-0.01, -0.02] * 4)})
    assert select_scheme(df, pos=8, window=8) == "rising"

    df2 = align_scheme_returns({"flat": _series([0.0] * 8),
                                "falling": _series([-0.01, -0.02] * 4)})
    assert select_scheme(df2, pos=8, window=8) == "flat"


def test_count_switches_counts_changes_only():
    ts = pd.date_range("2010-01-31", periods=4, freq="ME")
    assert count_switches([]) == 0
    assert count_switches([(ts[0], "a"), (ts[1], "a")]) == 0
    assert count_switches([(ts[0], "a"), (ts[1], "b"), (ts[2], "b"),
                           (ts[3], "a")]) == 2


def test_stitch_uses_baseline_during_warmup_then_selected_scheme():
    """12 months, window=4, cadence=4. "hi" genuinely wins on Sharpe.

    Values alternate so both series have non-zero variance -- with constant
    series both would score Sharpe 0.0 and "hi" would win only by column-order
    tie-break, which would not actually exercise selection.
    """
    hi = _series([0.10, 0.08] * 6)      # high mean, low spread -> high Sharpe
    lo = _series([0.01, -0.01] * 6)     # ~zero mean            -> ~zero Sharpe
    df = align_scheme_returns({"hi": hi, "lo": lo})

    wf, history = stitch_walk_forward(df, window=4, cadence=4, baseline="lo")

    assert len(wf) == 12
    assert list(wf.index) == list(df.index)
    # Warm-up (first `window` months) comes from the baseline.
    np.testing.assert_allclose(wf.iloc[:4].values, [0.01, -0.01] * 2, atol=1e-12)
    # After warm-up the better scheme is selected.
    np.testing.assert_allclose(wf.iloc[4:].values, [0.10, 0.08] * 4, atol=1e-12)
    assert [name for _, name in history] == ["hi", "hi"]
    assert [d for d, _ in history] == [df.index[4], df.index[8]]


def test_stitch_short_history_is_all_baseline():
    df = align_scheme_returns({"a": _series([0.01] * 3), "b": _series([0.09] * 3)})
    wf, history = stitch_walk_forward(df, window=6, cadence=12, baseline="a")
    assert history == []
    np.testing.assert_allclose(wf.values, [0.01] * 3, atol=1e-12)


def test_stitch_rejects_unknown_baseline():
    df = align_scheme_returns({"a": _series([0.01] * 8)})
    with pytest.raises(ValueError):
        stitch_walk_forward(df, window=4, cadence=4, baseline="nope")
