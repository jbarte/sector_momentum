"""Top-N equal-weight monthly rebalance simulation (long-only, no costs)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def close_at(df: pd.DataFrame, date: pd.Timestamp) -> float:
    sliced = df["Close"][df.index <= date]
    if sliced.empty:
        return float("nan")
    return float(sliced.iloc[-1])


def forward_returns(
    prices: dict[str, pd.DataFrame],
    tickers: list[str],
    dates: list[pd.Timestamp],
) -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        row: dict[str, float] = {}
        for t in tickers:
            df = prices.get(t)
            if df is None:
                row[t] = float("nan")
                continue
            p0, p1 = close_at(df, d0), close_at(df, d1)
            row[t] = round(p1 / p0 - 1.0, 10) if (p0 and not np.isnan(p0) and not np.isnan(p1)) else float("nan")
        rows[d0] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def simulate(
    score_by_date: dict[pd.Timestamp, pd.DataFrame],
    fwd_returns: pd.DataFrame,
    instrument_of: dict[str, str],
    top_n: int = 5,
) -> dict:
    dates = sorted(score_by_date.keys())
    out_dates: list[pd.Timestamp] = []
    strat_rets: list[float] = []
    holdings: list[list[str]] = []
    turnover: list[float] = []
    prev: set[str] = set()

    for d in dates:
        if d not in fwd_returns.index:
            continue  # last date / no forward window
        scored = score_by_date[d]
        ranked = scored.sort_values("composite", ascending=False)
        picks = list(ranked.index[:top_n])
        if not picks:
            continue

        rets = []
        for sk in picks:
            ticker = instrument_of.get(sk)
            r = fwd_returns.loc[d].get(ticker, float("nan")) if ticker else float("nan")
            if not np.isnan(r):
                rets.append(r)
        if not rets:
            continue

        out_dates.append(d)
        strat_rets.append(float(np.mean(rets)))
        holdings.append(picks)
        cur = set(picks)
        turnover.append(len(cur ^ prev) / (2 * top_n) if prev else 1.0)
        prev = cur

    return {
        "dates": out_dates,
        "strategy_returns": strat_rets,
        "holdings": holdings,
        "turnover": turnover,
    }
