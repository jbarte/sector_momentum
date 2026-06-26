import numpy as np
import pandas as pd
import pytest

from src.backtest import metrics as m


def test_equity_curve_and_total_return():
    rets = pd.Series([0.10, -0.10, 0.20])
    eq = m.equity_curve(rets)
    # 1.0 * 1.1 * 0.9 * 1.2 = 1.188
    assert eq.iloc[-1] == pytest.approx(1.188)
    assert m.total_return(eq) == pytest.approx(0.188)


def test_max_drawdown():
    eq = pd.Series([1.0, 1.2, 0.9, 1.0])  # peak 1.2 -> trough 0.9 = -0.25
    assert m.max_drawdown(eq) == pytest.approx(-0.25)


def test_sharpe_zero_vol_is_zero():
    rets = pd.Series([0.01, 0.01, 0.01])
    assert m.sharpe(rets) == 0.0


def test_hit_rate():
    strat = pd.Series([0.02, -0.01, 0.03])
    bench = pd.Series([0.01, 0.00, 0.04])
    # strat beats bench only in period 0 -> 1 of 3
    assert m.hit_rate(strat, bench) == pytest.approx(1 / 3)


def test_cagr_one_year_monthly():
    rets = pd.Series([0.0] * 12)
    eq = m.equity_curve(rets)
    assert m.cagr(eq) == pytest.approx(0.0)
