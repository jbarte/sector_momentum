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
from src.backtest.replay import (
    DEFAULT_EVAL_START, FETCH_START, WARMUP_DAYS, validate_eval_start,
    validate_eval_window,
)
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
# The frontier can now drop a shipped preset for a SECOND reason, unrelated to
# these bounds: its band may be dead too often on the chosen window (see
# MIN_LIVE_SHARE). That is a fact about the window, not a grid error, so it is
# reported loudly by _warn_degenerate_presets rather than fixed by widening.
#
# Widened past 8 on 2026-08-30 because the shipped `long` preset (top_n 5,
# buffer 8) sat on the old maximum, so nothing in the record said whether the
# cells just outside it were better. They are not, and the reason is the
# `live` column below rather than the returns: exit_rank 15 can fire on only
# 51% of a 2015- monthly calendar, so those cells are part buy-and-hold and
# their returns are not comparable to an interior cell's. 10 is where that
# becomes true for every top_n here; past it the grid measures the universe's
# growth, not the rule.
BUFFERS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

# A cell whose SELL line cannot fire is not evidence about that cell. Bands are
# absolute ranks, so nothing can rank past `exit_rank = top_n + buffer` until
# the scored universe is LARGER than it — and this universe grew from 10 priced
# themes in 2015 to 18 in 2023. Cells below this share of live dates are still
# reported (the number is the finding) but kept off the frontier, which rewards
# low churn and would otherwise be won outright by cells that never sell.
MIN_LIVE_SHARE = 0.9

BACKTEST_CACHE = "data/backtest_cache"

# Fetch window, evaluation window and the warm-up rule are defined once in
# src.backtest.replay and shared with backtest.py. They were duplicated here
# until 2026-08-14; a safety constant with two copies is precisely how this
# script and backtest.py came to disagree about which window they were running.
# Re-exported under the names this module already used so callers and tests are
# unaffected.
DEFAULT_START = DEFAULT_EVAL_START
_validate_start = validate_eval_start
_validate_window = validate_eval_window


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default=DEFAULT_START,
                   help="Start of the EVALUATION window. Price history is always "
                        f"fetched from {FETCH_START}, and this must be at least "
                        f"{WARMUP_DAYS} days later so trailing-window signals are "
                        "warm on the first evaluated date.")
    p.add_argument("--end", default=None,
                   help="End of the EVALUATION window (inclusive). Defaults to "
                        "the last available bar. Pass it to make two runs "
                        "DISJOINT: without it every window runs to today, so any "
                        "two overlap and 'the same cell wins on both' is much "
                        "weaker evidence than it reads as. `--end 2014-12-31` "
                        "and `--start 2015-01-01` partition the history.")
    p.add_argument("--cost-bps", type=float, default=None,
                   help="Round-trip cost in bps applied on turnover. Defaults to "
                        "costs.round_trip_bps in config/weights.yaml. Sweeping at 0 "
                        "systematically favours the cadence that trades most, which "
                        "is how the pre-2026-08-09 presets were picked.")
    p.add_argument("--out", default="horizon_sweep.md")
    return p.parse_args()


def _band_live_share(score_by_date, exit_rank: int, dates=None) -> float:
    """Share of rebalance dates on which the SELL line can actually fire.

    Needs the scored universe to be strictly larger than `exit_rank`: with 18
    themes and an exit_rank of 18, the worst possible rank is 18, which is
    still inside the band. Returns 0.0 for an empty calendar rather than
    raising — a cadence with no scored dates is already reported elsewhere.

    `dates` restricts the denominator to the rebalances a simulation actually
    evaluated. Pass it: `simulate` always drops the final scored date (it has
    no forward window), and that date carries the LARGEST universe, so scoring
    every key would bias this share upward exactly where it matters least.
    """
    keys = list(score_by_date) if dates is None else [d for d in dates if d in score_by_date]
    if not keys:
        return 0.0
    return sum(1 for d in keys if len(score_by_date[d]) > exit_rank) / len(keys)


def _is_degenerate(live: float | None) -> bool:
    """Whether a cell's band is dead often enough to disqualify its numbers.

    Compares the value the report PRINTS, not the raw float: a live share of
    0.897 renders as `90%` and must not then carry a flag whose legend says
    "below 90%".
    """
    if live is None:
        return False
    return round(100 * live) < round(100 * MIN_LIVE_SHARE)


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
        "live": _band_live_share(score_by_date, top_n + buffer, sim["dates"]),
        "cagr": metrics.cagr(strat_eq, ppy),
        "bench_cagr": metrics.cagr(bench_eq, ppy),
        "sharpe": metrics.sharpe(strat, ppy),
        "max_dd": metrics.max_drawdown(strat_eq),
        "turnover": metrics.avg_turnover(sim["turnover"]),
        "trades_per_year": sim["trades_per_year"],
        "median_hold": sim["median_holding_days"],
        "rebalances": len(sim["dates"]),
    }


def _warn_degenerate_presets(rows: list[dict], universe_size: int) -> list[str]:
    """Say so, loudly, when the window cannot evaluate a preset we actually ship.

    The liveness guard silently dropping `medium` and `long` from the frontier
    is worse than not having it: the whole point of this script is to check the
    shipped configuration, and a reader skimming the frontier would conclude
    the presets had been beaten rather than not measured. The default
    `--start 2004-01-01` window does exactly this — the theme universe is
    smaller than either preset's exit_rank for years — so this is the common
    case, not an edge one.

    Presets are stored as a fraction of the universe now (buffer_frac), while
    this sweep's own exploratory grid stays absolute-rank on purpose (that
    grid is what a future re-sweep would operate on, not this run) — so a
    shipped preset's buffer_frac is resolved against THIS RUN's own universe
    size before comparing it to a grid row's absolute buffer.
    """
    from src.horizons import horizons
    notes = []
    for h in horizons():
        exit_rank = h.exit_rank(universe_size)
        buffer_at_universe = exit_rank - h.top_n
        hit = next((r for r in rows if r["freq"] == h.rebalance
                    and r["top_n"] == h.top_n and r["buffer"] == buffer_at_universe), None)
        if hit is None or not _is_degenerate(hit.get("live")):
            continue
        notes.append(
            f"shipped preset `{h.key}` ({h.rebalance}/{h.top_n}/{buffer_at_universe}, "
            f"exit_rank {exit_rank}) is live on only "
            f"{hit['live']:.0%} of this window's rebalance dates, so it is "
            f"NOT on the frontier below — it was not measured, not beaten."
        )
        logger.warning("%s", notes[-1])
    if notes:
        logger.warning("Pick a later --start so the universe exceeds the "
                       "exit_rank throughout, or read the cell table instead "
                       "of the frontier.")
    return notes


def main() -> int:
    args = _parse_args()

    try:
        _validate_window(args.start, args.end)
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
                                          since=args.start, until=args.end)
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

    _write(rows, args, benchmark, Path(args.out), len(instrument_of))
    return 0


def _write(rows: list[dict], args, benchmark: str, out: Path, universe_size: int) -> None:
    def fmt(v, pct=False, nd=1):
        if v is None:
            return "—"
        return f"{100 * v:.{nd}f}%" if pct else f"{v:.{nd}f}"

    lines = [
        "# Rebalance horizon sweep",
        "",
        f"- evaluated from `{args.start}` to "
        f"`{args.end or 'the last available bar'}` "
        f"(history fetched from `{FETCH_START}`), "
        f"cost `{args.cost_bps:.0f}` bps, benchmark `{benchmark}`",
        f"- benchmark CAGR `{fmt(rows[0]['bench_cagr'], pct=True)}` "
        "(varies slightly by cadence — each cadence measures it on its own calendar)",
        "",
        "`median hold` excludes positions still open at the end: their true",
        "duration is unknown and counting them would bias the median short.",
        "",
        "`live` is the share of rebalance dates on which the SELL line could",
        "actually fire — it needs the scored universe to be larger than",
        f"`exit_rank = top_n + buffer`. Below {MIN_LIVE_SHARE:.0%} the cell is part",
        "buy-and-hold by construction, its return is not comparable to an",
        "interior cell's, and it is excluded from the frontier below.",
        "",
        "| cadence | top_n | buffer | band | live | CAGR | Sharpe | max DD | turnover | trades/yr | median hold (d) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        live = r.get("live")
        flag = " ⚠" if _is_degenerate(live) else ""
        lines.append(
            f"| {r['freq']} | {r['top_n']} | {r['buffer']} | {fmt(r.get('band'), pct=True, nd=0)} | "
            f"{fmt(live, pct=True, nd=0)}{flag} | "
            f"{fmt(r['cagr'], pct=True)} | "
            f"{fmt(r['sharpe'], nd=2)} | {fmt(r['max_dd'], pct=True)} | "
            f"{fmt(r['turnover'], pct=True, nd=0)} | {fmt(r['trades_per_year'])} | "
            f"{fmt(r['median_hold'], nd=0)} |"
        )

    # The frontier: for each achievable churn level, the best return available.
    warnings = _warn_degenerate_presets(rows, universe_size)
    if warnings:
        lines += ["", "> **This window cannot evaluate every shipped preset.**"]
        lines += [f"> - {w}" for w in warnings]
        lines += ["> - Pick a later `--start` so the universe exceeds the "
                  "`exit_rank` throughout, or read the cell table above "
                  "instead of the frontier."]

    lines += ["", "## Return / churn frontier", "",
              "Cells that are not beaten on BOTH return and churn by another cell.",
              "",
              f"Cells whose band is live on under {MIN_LIVE_SHARE:.0%} of rebalance dates are",
              "excluded: they cannot sell for part of the window, so they win this",
              "frontier on churn for a reason that has nothing to do with the rule.",
              "", "| cadence | top_n | buffer | CAGR | Sharpe | trades/yr | median hold (d) |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    scored = [r for r in rows
              if r["trades_per_year"] is not None and r["cagr"] is not None
              and not _is_degenerate(r.get("live"))]
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
