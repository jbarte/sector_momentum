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


def _select(ranked_index, prev: set[str], top_n: int, buffer_frac: float) -> list[str]:
    """Pick this period's holdings, with a hysteresis band.

    Hold anything already held while its rank stays within
    `top_n + round_half_up(buffer_frac * len(ranked_index))`, then fill
    whatever slots remain from the best names not already held. With
    `buffer_frac=0` this reduces exactly to `ranked_index[:top_n]`, which is
    the behaviour every existing backtest number was produced with.

    `buffer_frac` is a FRACTION of the scored universe, not an absolute rank
    count — the exit rank is resolved fresh from `len(ranked_index)` on every
    call, so the same buffer_frac yields a wider band as the universe grows.
    See sector_momentum-notes/specs/2026-08-30-fractional-hysteresis-band-design.md.

    A previously-held name that has no score this period (its prices went
    missing) is absent from `rank_of` and is therefore dropped — a position we
    can no longer rank is a position we cannot claim to still hold.
    """
    from src.horizons import _round_half_up
    rank_of = {sk: i for i, sk in enumerate(ranked_index)}   # 0-based
    exit_rank = top_n + _round_half_up(buffer_frac * len(ranked_index))
    keep = {sk for sk in prev if rank_of.get(sk, 10 ** 9) < exit_rank}
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
    buffer_frac: float = 0.0,
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
        picks = _select(list(ranked.index), prev, top_n, buffer_frac)
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


def simulate_with_stop(
    score_by_date: dict[pd.Timestamp, pd.DataFrame],
    fwd_returns: pd.DataFrame,
    instrument_of: dict[str, str],
    prices: dict[str, pd.DataFrame],
    top_n: int = 5,
    cost_bps: float = 0.0,
    buffer_frac: float = 0.0,
    unbuyable: frozenset[str] = frozenset(),
    stop_frac: float | None = None,
) -> dict:
    """`simulate`, plus an optional trailing stop checked on daily closes.

    Exploratory (2026-09-07), answering "does a stop-loss help this
    strategy?" — see sector_momentum-notes for the conversation that led
    here. Not wired into the live scan or the shipped presets.

    `stop_frac` is the trailing drawdown from a position's peak close SINCE
    ITS CURRENT ENTRY — not since the strategy's inception, and not reset
    just because a scheduled review rolls over while the position is still
    held — that forces an off-schedule exit. `stop_frac=None` disables the
    whole mechanism: `test_stop_frac_none_matches_plain_simulate` pins that
    the returns then reproduce `simulate`'s exactly.

    Three design choices were made deliberately, not incidentally:

    1. TRAILING, not fixed-from-entry. A fixed stop never fires on a name
       that ran up first and gave part of it back while still above entry —
       which is exactly the "let it run too far" case a reader asking about
       stops usually means.
    2. A stopped slot goes to CASH until the next scheduled review, rather
       than immediately buying the next-ranked name. Reallocating would
       conflate the stop's effect with an extra off-schedule trade, making
       it impossible to attribute a return difference to the stop alone.
    3. Cost is charged as HALF a round trip (the sell leg only) at the
       moment of the stop; the eventual buy that refills the slot is
       ordinary turnover at whichever future review picks a replacement, and
       is charged there via the normal formula. A stop followed by a refill
       therefore costs the same TOTAL as one ordinary swap, split across two
       events instead of one — not double-charged, not free.

    The peak persists across periods for a continuously-held name (an
    8-month hold that never re-enters `_select` still has one running peak,
    not one that quietly resets every review) and is reset only on a fresh
    entry — a scheduled buy, or a re-buy after a prior sale or stop starts
    a new peak from the new entry price, never the position's earlier life.

    `churn_stats` on the result is computed at REVIEW-date granularity, same
    as `simulate`'s — it does not know a stopped name actually left mid-
    period, so `median_holding_days` slightly OVERSTATES true holding time
    for a stopped name (by less than one period). `stops` (see below) is the
    exact count; use it rather than treating the duration figures as stop-
    aware.

    Returns the same keys as `simulate`, plus `stops`: one list per
    surviving period, naming which sk's were stopped out that period.
    """
    dates = sorted(score_by_date.keys())
    out_dates: list[pd.Timestamp] = []
    strat_rets: list[float] = []
    holdings: list[list[str]] = []
    turnover: list[float] = []
    stops: list[list[str]] = []
    prev: set[str] = set()
    entry_price: dict[str, float] = {}
    peak_price: dict[str, float] = {}

    for i, d in enumerate(dates):
        if d not in fwd_returns.index:
            continue  # last date / no forward window
        next_d = dates[i + 1] if i + 1 < len(dates) else None

        scored = score_by_date[d]
        ranked = scored.sort_values("composite", ascending=False)
        picks = _select(list(ranked.index), prev, top_n, buffer_frac)
        if unbuyable:
            picks = [sk for sk in picks if sk not in unbuyable]
        if not picks:
            continue

        cur = set(picks)
        rets: list[float] = []
        stopped_this_period: list[str] = []

        for sk in picks:
            ticker = instrument_of.get(sk)
            df = prices.get(ticker) if ticker else None
            fwd = fwd_returns.loc[d].get(ticker, float("nan")) if ticker else float("nan")

            if sk not in prev:
                # Fresh entry (new buy, or a re-buy after a prior sale/stop):
                # start a brand new peak from today's price, discarding any
                # stale entry/peak this key held from an earlier, closed life.
                p0 = close_at(df, d) if df is not None else float("nan")
                if not np.isnan(p0):
                    entry_price[sk] = p0
                    peak_price[sk] = p0
                else:
                    entry_price.pop(sk, None)
                    peak_price.pop(sk, None)

            no_stop_data = (
                stop_frac is None or df is None or next_d is None or sk not in peak_price
            )
            if no_stop_data:
                if not np.isnan(fwd):
                    rets.append(fwd)
                continue

            # BREACH-CHECKING is strictly BETWEEN reviews, excluding next_d
            # itself: a breach that coincides with the next scheduled review
            # isn't off-schedule at all, it's just what that review would see
            # when it re-ranks -- already handled by the ordinary sell/hold/
            # hysteresis logic, not this mechanism's job.
            window = df.loc[(df.index > d) & (df.index < next_d), "Close"]
            breach_ret = None
            for price in window:
                price = float(price)
                if np.isnan(price):
                    continue
                peak_price[sk] = max(peak_price[sk], price)
                if entry_price[sk] > 0 and price <= peak_price[sk] * (1 - stop_frac):
                    breach_ret = price / entry_price[sk] - 1.0
                    break

            if breach_ret is not None:
                rets.append(breach_ret)
                stopped_this_period.append(sk)
                cur.discard(sk)
                del entry_price[sk]
                del peak_price[sk]
                continue

            if not np.isnan(fwd):
                rets.append(fwd)

            # PEAK-TRACKING is not exempt from next_d the way breach-checking
            # is: a genuine new high made exactly on a review date must still
            # be on record before the NEXT period's breach check runs, or a
            # real peak-to-trough drawdown starting right after that review
            # is measured against a stale, lower peak and can silently fail
            # to fire. Bug found by code review 2026-09-07 -- caught only by
            # directly executing a price path with a new high landing exactly
            # on a review date, which no existing test constructed.
            end_price = close_at(df, next_d)
            if not np.isnan(end_price):
                peak_price[sk] = max(peak_price[sk], end_price)

        if not rets:
            continue

        out_dates.append(d)
        # Scheduled turnover is computed on the FULL picks list, unaffected
        # by a mid-period stop -- the review's own buy/sell decisions and
        # what price does afterward are two separate cost events (see #3
        # above). `book` here matches `simulate`'s definition exactly.
        full = set(picks)
        book = max(len(full), len(prev), 1)
        to = len(full ^ prev) / (2 * book) if prev else 1.0
        cost = to * cost_bps / 10_000
        cost += len(stopped_this_period) * (cost_bps / 10_000) / (2 * book)

        strat_rets.append(float(np.mean(rets)) - cost)
        holdings.append(picks)
        turnover.append(to)
        stops.append(stopped_this_period)
        prev = cur  # excludes anything stopped out -- next review sees it as unheld

    return {
        "dates": out_dates,
        "strategy_returns": strat_rets,
        "holdings": holdings,
        "turnover": turnover,
        "stops": stops,
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
