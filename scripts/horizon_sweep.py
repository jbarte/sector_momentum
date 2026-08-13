#!/usr/bin/env python3
"""Sweep rebalance cadence x top_n x hysteresis buffer, reporting return vs churn.

The question this answers: which operating point keeps most of the backtested
return while holding positions for weeks-to-months instead of days?

    python3 scripts/horizon_sweep.py
    python3 scripts/horizon_sweep.py --start 2010-01-01 --out /tmp/sweep.md

Scoring is done ONCE PER CADENCE and reused across the whole top_n x buffer
grid: top_n and buffer only affect selection, not scores, so re-scoring per cell
would be ~12x slower for identical numbers.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from itertools import product
from pathlib import Path

import pandas as pd
import yaml

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest import engine, metrics, replay, strategy
from src.data.prices import fetch_prices

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("horizon_sweep")

CADENCES = ["W", "2W", "M", "2M", "Q"]
TOP_N = [3, 4, 5]
# Buffers must BRACKET the shipped presets or the frontier this script prints
# excludes the configuration actually in use. The 2026-08-08 audit found the
# optimum at buffer 5 (hold band ~50% of a 20-theme universe); the original
# 0-3 grid stopped short of it and pointed at the wrong cells.
#
# The band is `(top_n + buffer) / universe_size`, so the useful buffer range
# moves when the universe does — the `band` column below is the number to read,
# not the raw buffer.
BUFFERS = [0, 1, 2, 3, 4, 5, 6, 7, 8]
BACKTEST_CACHE = "data/backtest_cache"

# History is ALWAYS fetched from here, whatever --start says. --start bounds the
# evaluation window only. Fetching from --start instead is a trap this script
# fell into until 2026-08-13: signals with a trailing window return NaN until
# their lookback fills (compute_ma_structure needs 200 bars for above_200dma),
# so a `--start 2008-01-01` run scored the whole 2008 crash on a degraded signal
# set. It was enough to invert the ranking of the horizon presets — `M/5/7`
# beat `M/5/4` by 1.9pp CAGR starved, and lost to it by 0.8pp warm.
FETCH_START = "2003-01-01"

# How much history an evaluation window must leave behind its first date.
# above_200dma needs 200 TRADING bars; a calendar year is ~252 of them, so a
# year of margin clears it without needing a trading calendar here.
WARMUP_DAYS = 365

# Deliberately NOT FETCH_START. Defaulting the evaluation start to the fetch
# start would leave the bare `python3 scripts/horizon_sweep.py` — the run that
# actually picks the presets — evaluating from the first fetched bar, i.e. the
# exact defect this separation exists to remove.
DEFAULT_START = "2004-01-01"


def _validate_start(start: str) -> pd.Timestamp:
    """Parse an evaluation start, rejecting one that would evaluate cold.

    Raises rather than clamping: a silently-moved window is what produced a
    report header claiming a start the run never used.
    """
    ts = pd.Timestamp(start)
    earliest = pd.Timestamp(FETCH_START) + pd.Timedelta(days=WARMUP_DAYS)
    if ts < earliest:
        raise ValueError(
            f"--start {ts.date()} leaves too little history to warm the signals: "
            f"prices are fetched from {FETCH_START}, and evaluation must begin no "
            f"earlier than {earliest.date()} ({WARMUP_DAYS} days later) so "
            f"above_200dma is populated on the first evaluated date."
        )
    return ts


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default=DEFAULT_START,
                   help="Start of the EVALUATION window. Price history is always "
                        f"fetched from {FETCH_START}, and this must be at least "
                        f"{WARMUP_DAYS} days later so trailing-window signals are "
                        "warm on the first evaluated date.")
    p.add_argument("--cost-bps", type=float, default=None,
                   help="Round-trip cost in bps applied on turnover. Defaults to "
                        "costs.round_trip_bps in config/weights.yaml. Sweeping at 0 "
                        "systematically favours the cadence that trades most, which "
                        "is how the pre-2026-08-09 presets were picked.")
    p.add_argument("--out", default="horizon_sweep.md")
    return p.parse_args()


def _cell(score_by_date, fwd, instrument_of, benchmark, top_n, buffer, cost_bps,
          unbuyable=frozenset()):
    """One grid cell. Mirrors run_theme_track's metric assembly, minus the
    equity-curve serialisation we don't need here."""
    sim = strategy.simulate(score_by_date, fwd, instrument_of,
                            top_n=top_n, cost_bps=cost_bps, buffer=buffer,
                            unbuyable=unbuyable)
    if not sim["dates"]:
        return None

    bench = pd.Series([fwd.loc[d, benchmark] for d in sim["dates"]]).reset_index(drop=True)
    strat = pd.Series(sim["strategy_returns"]).reset_index(drop=True)
    valid = bench.notna()
    strat, bench = strat[valid].reset_index(drop=True), bench[valid].reset_index(drop=True)
    if strat.empty:
        return None

    strat_eq, bench_eq = metrics.equity_curve(strat), metrics.equity_curve(bench)
    # Annualise on this cadence, not the monthly default — otherwise the whole
    # cross-cadence comparison this script exists for is meaningless.
    ppy = metrics.periods_per_year(sim["dates"])
    return {
        "top_n": top_n,
        "buffer": buffer,
        "ppy": ppy,
        "band": None,          # filled by the caller, which knows universe size
        "cagr": metrics.cagr(strat_eq, ppy),
        "bench_cagr": metrics.cagr(bench_eq, ppy),
        "sharpe": metrics.sharpe(strat, ppy),
        "max_dd": metrics.max_drawdown(strat_eq),
        "turnover": metrics.avg_turnover(sim["turnover"]),
        "trades_per_year": sim["trades_per_year"],
        "median_hold": sim["median_holding_days"],
        "rebalances": len(sim["dates"]),
    }


def main() -> int:
    args = _parse_args()

    try:
        _validate_start(args.start)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    if args.cost_bps is None:
        from src.horizons import round_trip_bps
        args.cost_bps = round_trip_bps()

    with open("config/themes.yaml") as fh:
        themes_cfg = yaml.safe_load(fh) or {}

    instrument_of = engine._theme_instruments(themes_cfg)
    tickers = sorted(set(instrument_of.values()) | {themes_cfg.get("benchmark", "ACWI"), "SPY"})
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (evaluating from %s) …",
                len(tickers), FETCH_START, end, args.start)
    prices = fetch_prices(tickers=tickers, start=FETCH_START, end=end,
                          cache_dir=BACKTEST_CACHE)

    benchmark = engine.resolve_benchmark(themes_cfg, prices)
    if benchmark is None:
        logger.error("No benchmark available — aborting.")
        return 1

    rows: list[dict] = []
    for freq in CADENCES:
        calendar = replay.rebalance_dates(prices[benchmark].index, freq,
                                          since=args.start)
        if len(calendar) < 3:
            logger.warning("%s: fewer than 3 rebalance dates — skipping", freq)
            continue

        logger.info("%s: scoring %d rebalance dates …", freq, len(calendar))
        score_by_date = engine.score_calendar(themes_cfg, prices, calendar,
                                              min_members=max(TOP_N))
        if len(score_by_date) < 2:
            logger.warning("%s: not enough scored dates — skipping", freq)
            continue

        dates = sorted(score_by_date)
        fwd = strategy.forward_returns(prices, list(instrument_of.values()) + [benchmark], dates)

        for top_n, buffer in product(TOP_N, BUFFERS):
            cell = _cell(score_by_date, fwd, instrument_of, benchmark,
                         top_n, buffer, args.cost_bps,
                         engine.unbuyable_keys(themes_cfg))
            if cell:
                cell["band"] = (top_n + buffer) / max(1, len(instrument_of))
                rows.append({"freq": freq, **cell})

    if not rows:
        logger.error("Sweep produced no cells.")
        return 1

    _write(rows, args, benchmark, Path(args.out))
    return 0


def _write(rows: list[dict], args, benchmark: str, out: Path) -> None:
    def fmt(v, pct=False, nd=1):
        if v is None:
            return "—"
        return f"{100 * v:.{nd}f}%" if pct else f"{v:.{nd}f}"

    lines = [
        "# Rebalance horizon sweep",
        "",
        f"- evaluated from `{args.start}` (history fetched from `{FETCH_START}`), "
        f"cost `{args.cost_bps:.0f}` bps, benchmark `{benchmark}`",
        f"- benchmark CAGR `{fmt(rows[0]['bench_cagr'], pct=True)}` "
        "(varies slightly by cadence — each cadence measures it on its own calendar)",
        "",
        "`median hold` excludes positions still open at the end: their true",
        "duration is unknown and counting them would bias the median short.",
        "",
        "| cadence | top_n | buffer | band | CAGR | Sharpe | max DD | turnover | trades/yr | median hold (d) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['freq']} | {r['top_n']} | {r['buffer']} | {fmt(r.get('band'), pct=True, nd=0)} | "
            f"{fmt(r['cagr'], pct=True)} | "
            f"{fmt(r['sharpe'], nd=2)} | {fmt(r['max_dd'], pct=True)} | "
            f"{fmt(r['turnover'], pct=True, nd=0)} | {fmt(r['trades_per_year'])} | "
            f"{fmt(r['median_hold'], nd=0)} |"
        )

    # The frontier: for each achievable churn level, the best return available.
    lines += ["", "## Return / churn frontier", "",
              "Cells that are not beaten on BOTH return and churn by another cell.",
              "", "| cadence | top_n | buffer | CAGR | Sharpe | trades/yr | median hold (d) |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    scored = [r for r in rows if r["trades_per_year"] is not None and r["cagr"] is not None]
    frontier = [
        r for r in scored
        if not any(o["cagr"] > r["cagr"] and o["trades_per_year"] < r["trades_per_year"]
                   for o in scored)
    ]
    for r in sorted(frontier, key=lambda x: x["trades_per_year"]):
        lines.append(
            f"| {r['freq']} | {r['top_n']} | {r['buffer']} | {fmt(r['cagr'], pct=True)} | "
            f"{fmt(r['sharpe'], nd=2)} | {fmt(r['trades_per_year'])} | {fmt(r['median_hold'], nd=0)} |"
        )

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%d cells, %d on the frontier)", out, len(rows), len(frontier))


if __name__ == "__main__":
    raise SystemExit(main())
