"""Top-N equal-weight monthly rebalance simulation (long-only, optional costs)."""
from __future__ import annotations

import numpy as np
import pandas as pd

MAX_STALE_DAYS = 5


def close_at(df: pd.DataFrame, date: pd.Timestamp) -> float:
    sliced = df["Close"][df.index <= date]
    if sliced.empty:
        return float("nan")
    last_date = sliced.index[-1]
    if (date - last_date).days > MAX_STALE_DAYS:
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


def _select(ranked_index, prev: set[str], top_n: int, buffer: int) -> list[str]:
    """Pick this period's holdings, with a hysteresis band.

    Hold anything already held while its rank stays within `top_n + buffer`,
    then fill whatever slots remain from the best names not already held. With
    `buffer=0` this reduces exactly to `ranked_index[:top_n]`, which is the
    behaviour every existing backtest number was produced with.

    A previously-held name that has no score this period (its prices went
    missing) is absent from `rank_of` and is therefore dropped — a position we
    can no longer rank is a position we cannot claim to still hold.
    """
    rank_of = {sk: i for i, sk in enumerate(ranked_index)}   # 0-based
    keep = {sk for sk in prev if rank_of.get(sk, 10 ** 9) < top_n + buffer}
    free = top_n - len(keep)
    if free > 0:
        keep.update([sk for sk in ranked_index if sk not in keep][:free])
    # Rank order, so `holdings` and turnover are stable run to run.
    return sorted(keep, key=lambda sk: rank_of[sk])


def simulate(
    score_by_date: dict[pd.Timestamp, pd.DataFrame],
    fwd_returns: pd.DataFrame,
    instrument_of: dict[str, str],
    top_n: int = 5,
    cost_bps: float = 0.0,
    buffer: int = 0,
    unbuyable: frozenset[str] = frozenset(),
) -> dict:
    """`unbuyable` names are ranked but never booked.

    They are removed AFTER selection, not before, so the slot they win goes
    unused rather than passing to the next name down. That is deliberate:
    substituting rank N+1 measured worse than the baseline in all three presets
    on both CAGR and Sharpe, while skipping measured better in all three. It
    also matches what the reader can actually do — there is no route to buy the
    instrument, and buying a different one is a different strategy.

    Because they never enter `prev`, they are re-selected and re-dropped every
    period at no turnover cost, which is correct: an untradeable name is never
    traded.
    """
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
        picks = _select(list(ranked.index), prev, top_n, buffer)
        if unbuyable:
            picks = [sk for sk in picks if sk not in unbuyable]
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
        cur = set(picks)
        # Turnover is a fraction of the book actually held, not of the intended
        # top_n. Returns are the equal-weighted mean of the names held, so with
        # an unbuyable name skipped each of the remaining 4 is 25% of the
        # portfolio and swapping one costs 25%, not 20%. Without unbuyable
        # names `_select` always returns exactly top_n, so this is unchanged.
        book = max(len(cur), len(prev), 1)
        to = len(cur ^ prev) / (2 * book) if prev else 1.0
        cost = to * cost_bps / 10_000
        strat_rets.append(float(np.mean(rets)) - cost)
        holdings.append(picks)
        turnover.append(to)
        prev = cur

    return {
        "dates": out_dates,
        "strategy_returns": strat_rets,
        "holdings": holdings,
        "turnover": turnover,
        **churn_stats(out_dates, holdings),
    }


def churn_stats(dates: list[pd.Timestamp], holdings: list[list[str]]) -> dict:
    """How often the strategy trades, and for how long it holds.

    `avg_turnover` already says what fraction of the book changes per rebalance;
    these answer the question a human actually asks — how many trades a year, and
    how long does a position last in days.

    Positions still open at the end are **censored**: their true holding period is
    unknown and at least as long as observed, so including them would bias the
    median short. They are excluded from the duration stats and reported
    separately as `open_positions`.
    """
    if not dates:
        return {"trades_total": 0, "trades_per_year": None,
                "median_holding_days": None, "mean_holding_days": None,
                "open_positions": 0}

    entered: dict[str, pd.Timestamp] = {}
    closed_durations: list[float] = []
    trades = 0
    prev: set[str] = set()

    for d, picks in zip(dates, holdings):
        cur = set(picks)
        for sk in cur - prev:                     # opened
            entered[sk] = d
            trades += 1
        for sk in prev - cur:                     # closed
            trades += 1
            start = entered.pop(sk, None)
            if start is not None:
                closed_durations.append((d - start).days)
        prev = cur

    span_days = (dates[-1] - dates[0]).days
    per_year = round(trades / (span_days / 365.25), 1) if span_days > 0 else None

    return {
        "trades_total": trades,
        "trades_per_year": per_year,
        "median_holding_days": (round(float(np.median(closed_durations)), 1)
                                if closed_durations else None),
        "mean_holding_days": (round(float(np.mean(closed_durations)), 1)
                              if closed_durations else None),
        "open_positions": len(entered),
    }
