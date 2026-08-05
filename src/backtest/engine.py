"""Per-track orchestration: replay scores -> simulate -> metrics."""
from __future__ import annotations

import logging

import pandas as pd

from src.backtest import metrics, replay, strategy

logger = logging.getLogger(__name__)


def _theme_instruments(themes_cfg: dict) -> dict[str, str]:
    return {f"THEME|{name}": (cfg["ticker"] if isinstance(cfg, dict) else cfg)
            for name, cfg in themes_cfg.get("themes", {}).items()}


def run_theme_track(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    top_n: int = 3,
    cost_bps: float = 0.0,
) -> dict | None:
    benchmark = themes_cfg.get("benchmark") or "ACWI"
    if benchmark not in prices:
        if "SPY" in prices:
            logger.warning("Theme benchmark %s missing — falling back to SPY", benchmark)
            benchmark = "SPY"
        else:
            logger.warning("Theme track skipped — benchmark %s missing", benchmark)
            return None

    calendar = replay.month_end_dates(prices[benchmark].index)
    if len(calendar) < 3:
        return None

    instrument_of = _theme_instruments(themes_cfg)

    score_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for d in calendar:
        scored = replay.score_themes_as_of(themes_cfg, prices, d)
        if scored is not None and len(scored) >= top_n:
            score_by_date[d] = scored
    if len(score_by_date) < 2:
        return None

    dates = sorted(score_by_date.keys())
    track_tickers = list(instrument_of.values()) + [benchmark]
    fwd = strategy.forward_returns(prices, track_tickers, dates)

    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=top_n, cost_bps=cost_bps)
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
        "start": eq_dates[0].strftime("%Y-%m-%d"),
        "end": eq_dates[n_points - 1].strftime("%Y-%m-%d"),
        "metrics": {
            "total_return": metrics.total_return(strat_eq),
            "cagr": metrics.cagr(strat_eq),
            "ann_vol": metrics.annualized_vol(strat_rets_s),
            "sharpe": metrics.sharpe(strat_rets_s),
            "max_drawdown": metrics.max_drawdown(strat_eq),
            "hit_rate": metrics.hit_rate(strat_rets_s, bench_rets_s),
            "avg_turnover": metrics.avg_turnover(sim["turnover"]),
            "benchmark_total_return": metrics.total_return(bench_eq),
            "benchmark_cagr": metrics.cagr(bench_eq),
        },
        "equity_curve": equity_curve,
        "holdings": [
            {"date": d.strftime("%Y-%m-%d"), "sectors": secs}
            for d, secs in zip(sim_dates, sim_holdings)
        ],
    }
