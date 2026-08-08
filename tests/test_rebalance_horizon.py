"""Rebalance cadence and hysteresis buffer.

The load-bearing property is that the new parameters are *inert at their
defaults*: `freq="M"`, `buffer=0` must reproduce exactly what the engine did
before they existed, because every committed backtest number was produced that
way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest import replay, strategy


# ---------------------------------------------------------------------------
# rebalance_dates
# ---------------------------------------------------------------------------

def _bdays(start="2022-01-03", periods=520):
    return pd.bdate_range(start, periods=periods)


def test_monthly_is_identical_to_month_end_dates():
    """The regression gate for adding cadence at all."""
    idx = _bdays()
    assert replay.rebalance_dates(idx, "M") == replay.month_end_dates(idx)


def test_each_freq_returns_last_trading_day_of_its_period():
    idx = _bdays()
    for freq, period in (("W", "W"), ("M", "M"), ("Q", "Q")):
        for d in replay.rebalance_dates(idx, freq):
            same_period = idx[idx.to_period(period) == pd.Timestamp(d).to_period(period)]
            assert d == same_period.max(), f"{freq}: {d} is not its period's last day"


def test_cadences_are_ordered_by_frequency():
    """W yields more dates than M, which yields more than Q — and the doubled
    cadences land between their base and the next step up."""
    idx = _bdays()
    n = {f: len(replay.rebalance_dates(idx, f)) for f in ("W", "2W", "M", "2M", "Q")}
    assert n["W"] > n["2W"] > n["M"] > n["2M"] > n["Q"], n


def test_doubled_cadence_is_every_second_period_end():
    idx = _bdays()
    assert replay.rebalance_dates(idx, "2W") == replay.rebalance_dates(idx, "W")[::2]
    assert replay.rebalance_dates(idx, "2M") == replay.rebalance_dates(idx, "M")[::2]


def test_empty_index_yields_no_dates():
    assert replay.rebalance_dates(pd.DatetimeIndex([]), "M") == []


def test_unknown_freq_raises():
    """A typo must fail loudly rather than silently falling back to monthly and
    producing a plausible-looking sweep row under the wrong label."""
    with pytest.raises(ValueError, match="unknown rebalance freq"):
        replay.rebalance_dates(_bdays(), "fortnightly")


# ---------------------------------------------------------------------------
# hysteresis
# ---------------------------------------------------------------------------

def _ranked(*keys):
    """A ranked index, best first."""
    return list(keys)


def test_buffer_zero_is_a_plain_top_n_slice():
    """buffer=0 must reduce exactly to ranked[:top_n] regardless of what was
    previously held — this is what every existing backtest number assumes."""
    ranked = _ranked("A", "B", "C", "D", "E")
    for prev in (set(), {"D"}, {"D", "E"}, {"A", "D"}):
        assert strategy._select(ranked, prev, top_n=3, buffer=0) == ["A", "B", "C"]


def test_buffer_holds_a_name_that_slipped_one_rank():
    """The whole point: a holding at rank top_n+1 survives with buffer=1 and is
    sold with buffer=0."""
    ranked = _ranked("A", "B", "C", "D")      # D is rank 4 (0-based 3)
    held = {"D"}
    assert "D" not in strategy._select(ranked, held, top_n=3, buffer=0)
    assert "D" in strategy._select(ranked, held, top_n=3, buffer=1)


def test_buffer_does_not_hold_past_the_band():
    ranked = _ranked("A", "B", "C", "D", "E")  # E is rank 5 (0-based 4)
    assert "E" not in strategy._select(ranked, {"E"}, top_n=3, buffer=1)
    assert "E" in strategy._select(ranked, {"E"}, top_n=3, buffer=2)


def test_free_slots_fill_from_the_best_unheld():
    ranked = _ranked("A", "B", "C", "D")
    picks = strategy._select(ranked, {"D"}, top_n=3, buffer=1)
    assert picks == ["A", "B", "D"], "should keep D and fill from the top"


def test_selection_never_exceeds_top_n():
    ranked = _ranked("A", "B", "C", "D", "E", "F")
    for buffer in range(5):
        picks = strategy._select(ranked, {"D", "E", "F"}, top_n=3, buffer=buffer)
        assert len(picks) <= 3, f"buffer={buffer} over-filled: {picks}"


def test_picks_come_out_rank_ordered():
    ranked = _ranked("A", "B", "C", "D")
    assert strategy._select(ranked, {"D", "B"}, top_n=3, buffer=1) == ["A", "B", "D"]


def test_unscored_holding_is_dropped():
    """A held name whose prices vanished has no rank this period. We cannot
    claim to still hold something we cannot rank, so it must be sold."""
    ranked = _ranked("A", "B", "C")
    picks = strategy._select(ranked, {"GONE"}, top_n=3, buffer=5)
    assert "GONE" not in picks
    assert picks == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# churn stats
# ---------------------------------------------------------------------------

def _dates(n, step_days=30):
    return [pd.Timestamp("2020-01-31") + pd.Timedelta(days=step_days * i) for i in range(n)]


def test_churn_counts_entries_and_exits():
    dates = _dates(3)
    holdings = [["A", "B"], ["A", "C"], ["A", "C"]]
    st = strategy.churn_stats(dates, holdings)
    # open A,B (2) + close B, open C (2) = 4
    assert st["trades_total"] == 4


def test_open_positions_are_censored_not_counted_short():
    """A position still open at the end has an unknown true duration. Counting
    it would drag the median toward the sample length."""
    dates = _dates(3)
    holdings = [["A"], ["A"], ["A"]]          # never closed
    st = strategy.churn_stats(dates, holdings)
    assert st["open_positions"] == 1
    assert st["median_holding_days"] is None, "censored hold must not be counted"


def test_holding_days_measure_the_closed_span():
    dates = _dates(3, step_days=30)           # 0, 30, 60 days
    holdings = [["A"], ["A"], ["B"]]          # A held from d0, closed at d2
    st = strategy.churn_stats(dates, holdings)
    assert st["median_holding_days"] == 60.0


def test_trades_per_year_normalises_by_span():
    dates = [pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01")]   # ~1 year
    holdings = [["A"], ["B"]]                 # open A, close A + open B = 3
    st = strategy.churn_stats(dates, holdings)
    assert st["trades_per_year"] == pytest.approx(3.0, abs=0.1)


def test_empty_input_is_safe():
    st = strategy.churn_stats([], [])
    assert st["trades_total"] == 0 and st["trades_per_year"] is None


# ---------------------------------------------------------------------------
# end-to-end through simulate
# ---------------------------------------------------------------------------

def _scored(order):
    """A scored frame whose composite ordering is `order` (best first)."""
    return pd.DataFrame({"composite": np.linspace(1.0, 0.0, len(order))}, index=list(order))


def test_simulate_buffer_reduces_turnover():
    """Alternating leadership: with buffer=0 the book churns every period; with
    buffer=1 the incumbent is held through the wobble."""
    dates = _dates(4)
    orders = [("A", "B", "C"), ("B", "A", "C"), ("A", "B", "C"), ("B", "A", "C")]
    score_by_date = {d: _scored(o) for d, o in zip(dates, orders)}
    fwd = pd.DataFrame({t: [0.01] * len(dates) for t in ("ta", "tb", "tc")}, index=dates)
    inst = {"A": "ta", "B": "tb", "C": "tc"}

    tight = strategy.simulate(score_by_date, fwd, inst, top_n=1, cost_bps=0, buffer=0)
    loose = strategy.simulate(score_by_date, fwd, inst, top_n=1, cost_bps=0, buffer=1)

    assert tight["trades_total"] > loose["trades_total"]
    assert loose["holdings"] == [["A"], ["A"], ["A"], ["A"]], loose["holdings"]


# ---------------------------------------------------------------------------
# annualisation
# ---------------------------------------------------------------------------

def test_periods_per_year_recovers_each_cadence():
    """The metrics module defaults to 12 (monthly). Once cadence is a parameter
    that default silently annualises a quarterly track as monthly and trebles
    its CAGR — this helper is what prevents that."""
    from src.backtest import metrics
    idx = pd.bdate_range("2010-01-01", "2020-01-01")
    for freq, expected in (("W", 52), ("2W", 26), ("M", 12), ("2M", 6), ("Q", 4)):
        dates = replay.rebalance_dates(idx, freq)
        assert metrics.periods_per_year(dates) == pytest.approx(expected, rel=0.06), freq


def test_periods_per_year_is_safe_on_degenerate_input():
    from src.backtest import metrics
    assert metrics.periods_per_year([]) == 12.0
    assert metrics.periods_per_year([pd.Timestamp("2020-01-01")]) == 12.0
    same = [pd.Timestamp("2020-01-01")] * 2
    assert metrics.periods_per_year(same) == 12.0


def test_cagr_scales_with_periods_per_year():
    """Same equity curve, different cadence -> different annualised return.
    Doubling periods_per_year halves the elapsed years and so squares growth."""
    from src.backtest import metrics
    eq = pd.Series([1.0, 1.1, 1.21])          # two periods, +10% each
    slow = metrics.cagr(eq, periods_per_year=1)
    fast = metrics.cagr(eq, periods_per_year=2)
    assert fast > slow
    assert (1 + slow) ** 2 == pytest.approx(1 + fast, rel=1e-9)
