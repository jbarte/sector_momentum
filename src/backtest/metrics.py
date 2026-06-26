"""Pure performance metrics over periodic returns / equity curves."""
from __future__ import annotations

import numpy as np
import pandas as pd


def equity_curve(returns: pd.Series, initial: float = 1.0) -> pd.Series:
    curve = (1.0 + returns.fillna(0.0)).cumprod()
    return pd.concat([pd.Series([initial], index=[curve.index[0] - 1]), initial * curve])


def total_return(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: float = 12) -> float:
    n = len(equity)
    if n < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (n - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def annualized_vol(returns: pd.Series, periods_per_year: float = 12) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe(returns: pd.Series, periods_per_year: float = 12) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    return float(dd.min())


def hit_rate(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    s, b = strategy_returns.align(benchmark_returns, join="inner")
    if len(s) == 0:
        return 0.0
    return float((s > b).mean())


def avg_turnover(turnovers: list[float]) -> float:
    if not turnovers:
        return 0.0
    return float(np.mean(turnovers))
