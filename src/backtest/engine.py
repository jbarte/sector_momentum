"""Per-track orchestration: replay scores -> simulate -> metrics."""
from __future__ import annotations

import logging

import pandas as pd

from src.backtest import metrics, replay, strategy

logger = logging.getLogger(__name__)


def _theme_instruments(themes_cfg: dict) -> dict[str, str]:
    return {f"THEME|{name}": (cfg["ticker"] if isinstance(cfg, dict) else cfg)
            for name, cfg in themes_cfg.get("themes", {}).items()}


def unbuyable_keys(themes_cfg: dict) -> frozenset[str]:
    """Sector keys the reader has no route to purchase (`unbuyable: true`).

    Scored but never held: they still shape the cross-sectional z-scores, and
    the backtest must not book them or it reports returns from trades that
    could not be made. The dashboard reads the same config flag, so the board
    and the published figures describe one strategy.
    """
    return frozenset(
        f"THEME|{name}"
        for name, cfg in themes_cfg.get("themes", {}).items()
        if isinstance(cfg, dict) and cfg.get("unbuyable")
    )


def resolve_benchmark(themes_cfg: dict, prices: dict[str, pd.DataFrame]) -> str | None:
    """The configured benchmark, or the documented SPY fallback, or None."""
    benchmark = themes_cfg.get("benchmark") or "ACWI"
    if benchmark in prices:
        return benchmark
    if "SPY" in prices:
        logger.warning("Theme benchmark %s missing — falling back to SPY", benchmark)
        return "SPY"
    logger.warning("Theme track skipped — benchmark %s missing", benchmark)
    return None


def score_calendar(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    calendar: list[pd.Timestamp],
    min_members: int = 1,
) -> dict[pd.Timestamp, pd.DataFrame]:
    """Score every date in `calendar`.

    Extracted so the horizon sweep can score once per cadence and reuse the
    result across the whole top_n x buffer grid. Scoring replays the signal
    pipeline per date and is by far the expensive step; top_n and buffer do not
    affect scores, so re-scoring per grid cell would be ~12x slower for nothing.
    """
    out: dict[pd.Timestamp, pd.DataFrame] = {}
    for d in calendar:
        scored = replay.score_themes_as_of(themes_cfg, prices, d)
        if scored is not None and len(scored) >= min_members:
            out[d] = scored
    return out


def run_theme_track(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    top_n: int = 3,
    cost_bps: float = 0.0,
    rebalance_freq: str = "M",
    buffer: int = 0,
) -> dict | None:
    benchmark = resolve_benchmark(themes_cfg, prices)
    if benchmark is None:
        return None

    calendar = replay.rebalance_dates(prices[benchmark].index, rebalance_freq)
    if len(calendar) < 3:
        return None

    instrument_of = _theme_instruments(themes_cfg)

    score_by_date = score_calendar(themes_cfg, prices, calendar, min_members=top_n)
    if len(score_by_date) < 2:
        return None

    dates = sorted(score_by_date.keys())
    track_tickers = list(instrument_of.values()) + [benchmark]
    fwd = strategy.forward_returns(prices, track_tickers, dates)

    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=top_n,
                            cost_bps=cost_bps, buffer=buffer,
                            unbuyable=unbuyable_keys(themes_cfg))
    if not sim["dates"]:
        return None

    bench_rets_list = [fwd.loc[d, benchmark] for d in sim["dates"]]
    strat_rets_s = pd.Series(sim["strategy_returns"]).reset_index(drop=True)
    bench_rets_s = pd.Series(bench_rets_list).reset_index(drop=True)

    valid = bench_rets_s.notna()
    strat_rets_s = strat_rets_s[valid].reset_index(drop=True)
    bench_rets_s = bench_rets_s[valid].reset_index(drop=True)
    sim_dates = [d for d, v in zip(sim["dates"], valid) if v]
    sim_holdings = [h for h, v in zip(sim["holdings"], valid) if v]

    strat_eq = metrics.equity_curve(strat_rets_s)
    bench_eq = metrics.equity_curve(bench_rets_s)

    # Annualise on THIS track's cadence. Leaving the metrics defaults (12) in
    # place would annualise a quarterly track as monthly and treble its CAGR.
    ppy = metrics.periods_per_year(sim["dates"])

    if not sim_dates:
        return None

    eq_dates = list(sim_dates)
    later = [d for d in calendar if d > sim_dates[-1]]
    if later:
        eq_dates.append(later[0])
    n_points = min(len(eq_dates), len(strat_eq), len(bench_eq))

    equity_curve = [
        {"date": eq_dates[i].strftime("%Y-%m-%d"),
         "strategy": float(strat_eq.iloc[i]),
         "benchmark": float(bench_eq.iloc[i])}
        for i in range(n_points)
    ]

    return {
        "region": "THEME",
        "benchmark": benchmark,
        "top_n": top_n,
        "cost_bps": cost_bps,
        "rebalance_freq": rebalance_freq,
        "buffer": buffer,
        "start": eq_dates[0].strftime("%Y-%m-%d"),
        "end": eq_dates[n_points - 1].strftime("%Y-%m-%d"),
        "metrics": {
            "total_return": metrics.total_return(strat_eq),
            "cagr": metrics.cagr(strat_eq, ppy),
            "ann_vol": metrics.annualized_vol(strat_rets_s, ppy),
            "sharpe": metrics.sharpe(strat_rets_s, ppy),
            "max_drawdown": metrics.max_drawdown(strat_eq),
            "hit_rate": metrics.hit_rate(strat_rets_s, bench_rets_s),
            "avg_turnover": metrics.avg_turnover(sim["turnover"]),
            "benchmark_total_return": metrics.total_return(bench_eq),
            "benchmark_cagr": metrics.cagr(bench_eq, ppy),
            # Churn, in the terms a human asks about. avg_turnover says what
            # fraction of the book moves per rebalance; these say how often you
            # actually trade and how long a position lasts.
            "trades_total": sim["trades_total"],
            "trades_per_year": sim["trades_per_year"],
            "median_holding_days": sim["median_holding_days"],
            "mean_holding_days": sim["mean_holding_days"],
            "open_positions": sim["open_positions"],
            "periods_per_year": round(ppy, 2),
        },
        "equity_curve": equity_curve,
        "holdings": [
            {"date": d.strftime("%Y-%m-%d"), "sectors": secs}
            for d, secs in zip(sim_dates, sim_holdings)
        ],
    }
