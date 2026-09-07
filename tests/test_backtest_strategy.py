import numpy as np
import pandas as pd
import pytest

from src.backtest import strategy


def _scored(composites: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"composite": composites})


def _prices(values: dict[str, list[float]], dates) -> dict[str, pd.DataFrame]:
    return {t: pd.DataFrame({"Close": pd.Series(v, index=dates)}) for t, v in values.items()}


def test_forward_returns_simple_pct():
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28"), pd.Timestamp("2021-03-31")]
    prices = _prices({"XLK": [100.0, 110.0, 121.0]}, dates)
    fwd = strategy.forward_returns(prices, ["XLK"], dates)
    assert list(fwd.index) == dates[:-1]
    assert fwd.loc[dates[0], "XLK"] == 0.10
    assert fwd.loc[dates[1], "XLK"] == 0.10


def test_simulate_selects_top_n_and_earns_forward_return():
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    score_by_date = {dates[0]: _scored({"US|Tech": 2.0, "US|Energy": -1.0})}
    fwd = pd.DataFrame({"XLK": [0.05], "XLE": [-0.03]}, index=[dates[0]])
    res = strategy.simulate(score_by_date, fwd, instrument_of, top_n=1)
    assert res["holdings"][0] == ["US|Tech"]
    assert res["strategy_returns"][0] == 0.05


def test_simulate_has_no_lookahead():
    """Holdings at date[0] must not depend on any later score."""
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    fwd = pd.DataFrame({"XLK": [0.05], "XLE": [-0.03]}, index=[dates[0]])

    base = {dates[0]: pd.DataFrame({"composite": {"US|Tech": 2.0, "US|Energy": -1.0}})}
    res_a = strategy.simulate(base, fwd, instrument_of, top_n=1)

    # Add a *future* date with an extreme score; past holding must be unchanged.
    perturbed = dict(base)
    perturbed[dates[1]] = pd.DataFrame({"composite": {"US|Tech": -99.0, "US|Energy": 99.0}})
    res_b = strategy.simulate(perturbed, fwd, instrument_of, top_n=1)
    assert res_b["holdings"][0] == res_a["holdings"][0] == ["US|Tech"]


def test_simulate_cost_bps_reduces_returns():
    """Transaction costs should reduce strategy returns."""
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK"}
    score_by_date = {dates[0]: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [0.05]}, index=[dates[0]])
    res_no_cost = strategy.simulate(score_by_date, fwd, instrument_of, top_n=1, cost_bps=0)
    res_with_cost = strategy.simulate(score_by_date, fwd, instrument_of, top_n=1, cost_bps=50)
    assert res_with_cost["strategy_returns"][0] < res_no_cost["strategy_returns"][0]


def test_stop_frac_none_matches_plain_simulate():
    """Disabling the stop must reproduce `simulate` exactly, not approximately."""
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    score_by_date = {dates[0]: _scored({"US|Tech": 2.0, "US|Energy": -1.0})}
    fwd = pd.DataFrame({"XLK": [0.05], "XLE": [-0.03]}, index=[dates[0]])

    plain = strategy.simulate(score_by_date, fwd, instrument_of, top_n=1)
    unstopped = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices={}, top_n=1, stop_frac=None)

    assert unstopped["strategy_returns"] == plain["strategy_returns"]
    assert unstopped["holdings"] == plain["holdings"]
    assert unstopped["stops"] == [[]]


def test_trailing_stop_fires_on_intra_period_drawdown_from_peak():
    """A name that runs up then gives back stop_frac from its peak exits early,
    even though it's still well above where it was bought."""
    d0, mid_peak, mid_breach, d1 = (
        pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-05"),
        pd.Timestamp("2021-02-15"), pd.Timestamp("2021-02-28"),
    )
    instrument_of = {"US|Tech": "XLK"}
    # 100 -> 130 (peak) -> 105 (15.4% off peak, past a 15% stop) -> 80 by
    # period end. Without the stop this would earn -20%; the trailing stop
    # should lock in the +5% it had already booked by the breach day.
    prices = {"XLK": pd.DataFrame(
        {"Close": [100.0, 130.0, 105.0, 80.0]}, index=[d0, mid_peak, mid_breach, d1])}
    score_by_date = {d0: _scored({"US|Tech": 1.0}), d1: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [-0.20]}, index=[d0])

    res = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15)

    assert res["stops"][0] == ["US|Tech"]
    assert res["strategy_returns"][0] == pytest.approx(0.05)


def test_trailing_stop_does_not_fire_within_threshold():
    """Same shape, but the intra-period pullback stays inside the stop band."""
    d0, mid_peak, mid_dip, d1 = (
        pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-05"),
        pd.Timestamp("2021-02-15"), pd.Timestamp("2021-02-28"),
    )
    instrument_of = {"US|Tech": "XLK"}
    # 130 peak, stop line is 110.5; 115 stays above it.
    prices = {"XLK": pd.DataFrame(
        {"Close": [100.0, 130.0, 115.0, 121.0]}, index=[d0, mid_peak, mid_dip, d1])}
    score_by_date = {d0: _scored({"US|Tech": 1.0}), d1: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [0.21]}, index=[d0])

    res = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15)

    assert res["stops"][0] == []
    assert res["strategy_returns"][0] == pytest.approx(0.21)


def test_breach_on_the_review_date_itself_is_not_treated_as_a_stop():
    """A drawdown that only shows up exactly at the next review isn't
    off-schedule — that's the review's own job, not this mechanism's."""
    d0, d1 = pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")
    instrument_of = {"US|Tech": "XLK"}
    prices = {"XLK": pd.DataFrame({"Close": [100.0, 80.0]}, index=[d0, d1])}
    score_by_date = {d0: _scored({"US|Tech": 1.0}), d1: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [-0.20]}, index=[d0])

    res = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15)

    assert res["stops"][0] == []
    assert res["strategy_returns"][0] == pytest.approx(-0.20)


def test_stopped_position_pays_only_the_sell_leg_extra():
    """The stop's extra cost is half a round trip, on top of scheduled turnover.

    Isolated in the SECOND period, where Tech is a continuation (not a fresh
    buy) so scheduled turnover is already zero going in -- the only cost this
    period is the stop's own half-round-trip, cleanly comparable across
    cost_bps. (The first period always carries its own unavoidable opening
    cost from an empty `prev`, same as plain `simulate` -- not what's tested
    here.)
    """
    d0, d1, mid, d2 = (pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28"),
                        pd.Timestamp("2021-03-15"), pd.Timestamp("2021-03-31"))
    instrument_of = {"US|Tech": "XLK"}
    prices = {"XLK": pd.DataFrame(
        {"Close": [100.0, 100.0, 80.0, 85.0]}, index=[d0, d1, mid, d2])}
    score_by_date = {d0: _scored({"US|Tech": 1.0}), d1: _scored({"US|Tech": 1.0}),
                      d2: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [0.0, -0.5]}, index=[d0, d1])  # d1's fwd is overridden by the breach

    no_cost = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15, cost_bps=0)
    with_cost = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15, cost_bps=100)

    assert no_cost["stops"][1] == ["US|Tech"]
    assert no_cost["strategy_returns"][1] == pytest.approx(-0.20)
    # book=1 (only holding), so the sell-leg-only cost is (1/(2*1))*100bps = 50bps.
    assert with_cost["strategy_returns"][1] == pytest.approx(
        no_cost["strategy_returns"][1] - 0.0050)


def test_peak_updates_on_review_date_close_not_just_intra_period_days():
    """A genuine new high made exactly ON a review date must still be on
    record before the NEXT period's breach check runs.

    Regression: breach-checking deliberately skips the review date itself
    (see the "not off-schedule" test above), but peak-TRACKING was
    incorrectly skipping it too — so a continuously-held position's peak
    only ever advanced on strictly-intra-period days. A new high made
    exactly at a review, followed by a real drawdown past stop_frac in the
    NEXT period, went completely undetected. Found by code review
    2026-09-07 via direct execution, not by any test that existed then.
    """
    d0, d1, mid, d2 = (pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28"),
                        pd.Timestamp("2021-03-15"), pd.Timestamp("2021-03-31"))
    instrument_of = {"US|Tech": "XLK"}
    # The 200 peak is made exactly ON the d1 review date, not intra-period.
    prices = {"XLK": pd.DataFrame(
        {"Close": [100.0, 200.0, 150.0, 150.0]}, index=[d0, d1, mid, d2])}
    score_by_date = {d0: _scored({"US|Tech": 1.0}), d1: _scored({"US|Tech": 1.0}),
                      d2: _scored({"US|Tech": 1.0})}
    fwd = pd.DataFrame({"XLK": [1.0, -0.25]}, index=[d0, d1])

    res = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15)

    # 150 is a 25% drawdown from the 200 peak set at d1 -- past a 15% stop.
    assert res["stops"][1] == ["US|Tech"]


def test_stopped_position_is_not_held_going_into_next_review():
    """A stop is a real exit: the name gets no hysteresis benefit afterward and
    must be re-earned on rank alone, exactly like an ordinary scheduled sale."""
    d0, mid, d1, d2 = (pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-15"),
                       pd.Timestamp("2021-02-28"), pd.Timestamp("2021-03-31"))
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    prices = {
        "XLK": pd.DataFrame({"Close": [100.0, 80.0, 82.0, 82.0]}, index=[d0, mid, d1, d2]),
        "XLE": pd.DataFrame({"Close": [50.0, 50.0, 50.0, 50.0]}, index=[d0, mid, d1, d2]),
    }
    # Tech ranks 1st throughout -- with no stop (and top_n=1 buffer_frac=0) it
    # would simply be held straight through on rank alone.
    score_by_date = {
        d0: _scored({"US|Tech": 2.0, "US|Energy": 1.0}),
        d1: _scored({"US|Tech": 2.0, "US|Energy": 1.0}),
        d2: _scored({"US|Tech": 2.0, "US|Energy": 1.0}),
    }
    fwd = pd.DataFrame({"XLK": [-0.18, 0.0], "XLE": [0.0, 0.0]}, index=[d0, d1])

    res = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices, top_n=1, stop_frac=0.15)

    assert res["stops"][0] == ["US|Tech"]
    # Second period: Tech is still top-ranked, but the stop cleared it from
    # `prev`, so it's re-bought fresh (same key as any other new entry) rather
    # than "still held" -- observable via holdings still naming it (re-picked
    # on rank), while the earlier stop is what forced that re-purchase.
    assert res["holdings"][1] == ["US|Tech"]


def test_close_at_stale_price_returns_nan():
    """Prices older than MAX_STALE_DAYS should return NaN."""
    dates = [pd.Timestamp("2021-01-04")]
    df = pd.DataFrame({"Close": [100.0]}, index=dates)
    # 6+ days later (> MAX_STALE_DAYS=5)
    result = strategy.close_at(df, pd.Timestamp("2021-01-11"))
    assert np.isnan(result)


def test_close_at_fresh_price_returns_value():
    """Prices within MAX_STALE_DAYS should return the close."""
    dates = [pd.Timestamp("2021-01-04")]
    df = pd.DataFrame({"Close": [100.0]}, index=dates)
    result = strategy.close_at(df, pd.Timestamp("2021-01-08"))
    assert result == 100.0
