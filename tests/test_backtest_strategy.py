import numpy as np
import pandas as pd

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
