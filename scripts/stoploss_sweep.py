#!/usr/bin/env python3
"""Does a trailing stop-loss help either shipped horizon preset?

The question this answers: on top of the ALREADY-CHOSEN `medium`/`long`
presets (cadence, top_n, buffer_frac — not re-optimised here), does adding a
trailing stop-loss checked on daily closes between reviews improve return,
risk, or churn — or does it just give back return to noise, the way stops
often do on a momentum strategy?

    python3 scripts/stoploss_sweep.py
    python3 scripts/stoploss_sweep.py --start 2010-01-01 --out /tmp/stops.md

Three design choices are fixed, not swept (see `src.backtest.strategy.
simulate_with_stop`'s docstring for the reasoning behind each):
  1. TRAILING stop, from the position's peak close since entry — not a fixed
     stop from the entry price.
  2. A stopped slot goes to CASH until the next scheduled review, rather than
     immediately buying the next-ranked name.
  3. A stop costs HALF a round trip (the sell leg) at the moment it fires;
     the eventual buy that refills the slot is charged normally whenever a
     future review picks a replacement.

What IS swept is `stop_frac` itself, against a `None` (no stop) baseline, for
each shipped preset.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest import engine, metrics, replay, strategy
from src.backtest.replay import (
    DEFAULT_EVAL_START, FETCH_START, WARMUP_DAYS, validate_eval_window,
)
from src.data.prices import fetch_prices
from src.horizons import horizons

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("stoploss_sweep")

# None first so every report leads with the no-stop baseline it's judged
# against. The grid is finer below 20% because that is where the first pass
# put the whole effect -- 25% and 30% barely fire (0.7-2.6 stops/yr) and are
# kept only to show the effect decaying to the baseline, which is the shape
# that says a real mechanism rather than a lucky cell.
STOP_FRACS: list[float | None] = [None, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30]

BACKTEST_CACHE = "data/backtest_cache"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=DEFAULT_EVAL_START,
                   help="Start of the EVALUATION window. Price history is always "
                        f"fetched from {FETCH_START}, and this must be at least "
                        f"{WARMUP_DAYS} days later so trailing-window signals are "
                        "warm on the first evaluated date.")
    p.add_argument("--end", default=None, help="End of the EVALUATION window (inclusive).")
    p.add_argument("--cost-bps", type=float, default=None,
                   help="Round-trip cost in bps. Defaults to costs.round_trip_bps "
                        "in config/weights.yaml.")
    p.add_argument("--out", default="stoploss_sweep.md")
    return p.parse_args()


def _cell(score_by_date, fwd, instrument_of, prices, benchmark, top_n, buffer_frac,
          cost_bps, stop_frac, unbuyable=frozenset()) -> dict | None:
    sim = strategy.simulate_with_stop(
        score_by_date, fwd, instrument_of, prices,
        top_n=top_n, cost_bps=cost_bps, buffer_frac=buffer_frac,
        unbuyable=unbuyable, stop_frac=stop_frac)
    if not sim["dates"]:
        return None

    bench = pd.Series([fwd.loc[d, benchmark] for d in sim["dates"]]).reset_index(drop=True)
    strat = pd.Series(sim["strategy_returns"]).reset_index(drop=True)
    valid = bench.notna()
    strat, bench = strat[valid].reset_index(drop=True), bench[valid].reset_index(drop=True)
    if strat.empty:
        return None

    strat_eq = metrics.equity_curve(strat)
    ppy = metrics.periods_per_year(sim["dates"])
    span_days = (sim["dates"][-1] - sim["dates"][0]).days
    stops_total = sum(len(s) for s in sim["stops"])
    stops_per_year = round(stops_total / (span_days / 365.25), 1) if span_days > 0 else None

    return {
        "stop_frac": stop_frac,
        "cagr": metrics.cagr(strat_eq, ppy),
        "sharpe": metrics.sharpe(strat, ppy),
        "max_dd": metrics.max_drawdown(strat_eq),
        "turnover": metrics.avg_turnover(sim["turnover"]),
        "trades_per_year": sim["trades_per_year"],
        "median_hold": sim["median_holding_days"],
        "stops_total": stops_total,
        "stops_per_year": stops_per_year,
        "rebalances": len(sim["dates"]),
    }


def main() -> int:
    args = _parse_args()

    try:
        validate_eval_window(args.start, args.end)
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

    unbuyable = engine.unbuyable_keys(themes_cfg)
    universe_size = len(instrument_of)
    rows: list[dict] = []

    for h in horizons():
        calendar = replay.rebalance_dates(prices[benchmark].index, h.rebalance,
                                          since=args.start, until=args.end)
        if len(calendar) < 3:
            logger.warning("%s: fewer than 3 rebalance dates — skipping", h.key)
            continue

        logger.info("%s (%s/%d/%.3f): scoring %d rebalance dates …",
                    h.key, h.rebalance, h.top_n, h.buffer_frac, len(calendar))
        score_by_date = engine.score_calendar(themes_cfg, prices, calendar,
                                              min_members=h.top_n)
        if len(score_by_date) < 2:
            logger.warning("%s: not enough scored dates — skipping", h.key)
            continue

        dates = sorted(score_by_date)
        fwd = strategy.forward_returns(prices, list(instrument_of.values()) + [benchmark], dates)

        for stop_frac in STOP_FRACS:
            cell = _cell(score_by_date, fwd, instrument_of, prices, benchmark,
                         h.top_n, h.buffer_frac, args.cost_bps, stop_frac, unbuyable)
            if cell:
                rows.append({"preset": h.key, "rebalance": h.rebalance,
                            "top_n": h.top_n, **cell})

    if not rows:
        logger.error("Sweep produced no cells.")
        return 1

    _write(rows, args, benchmark, Path(args.out), universe_size)
    return 0


def _write(rows: list[dict], args, benchmark: str, out: Path, universe_size: int) -> None:
    def fmt(v, pct=False, nd=1):
        if v is None:
            return "—"
        return f"{100 * v:.{nd}f}%" if pct else f"{v:.{nd}f}"

    def stop_label(sf):
        return "none" if sf is None else f"{100 * sf:.0f}%"

    lines = [
        "# Trailing stop-loss sweep",
        "",
        f"- evaluated from `{args.start}` to "
        f"`{args.end or 'the last available bar'}` "
        f"(history fetched from `{FETCH_START}`), "
        f"cost `{args.cost_bps:.0f}` bps, benchmark `{benchmark}`, "
        f"universe `{universe_size}` themes",
        "",
        "Trailing from the position's peak close SINCE ENTRY, checked on daily",
        "closes strictly between reviews. A stopped slot goes to cash until the",
        "next review (not reallocated). Cost: half a round trip at the moment",
        "the stop fires; the eventual refill is charged normally at whichever",
        "future review picks a replacement. See `simulate_with_stop`'s",
        "docstring for why each of those was chosen over the alternative.",
        "",
        "`median hold` is at REVIEW-date granularity and therefore slightly",
        "OVERSTATES true holding time for a stopped position (by less than one",
        "period) — `stops/yr` is the exact count of off-schedule exits; read",
        "that, not the duration column, as the stop-activity number.",
        "",
        "| preset | stop | CAGR | Sharpe | max DD | turnover | trades/yr | "
        "median hold (d) | stops/yr |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['preset']} | {stop_label(r['stop_frac'])} | "
            f"{fmt(r['cagr'], pct=True)} | {fmt(r['sharpe'], nd=2)} | "
            f"{fmt(r['max_dd'], pct=True)} | {fmt(r['turnover'], pct=True, nd=0)} | "
            f"{fmt(r['trades_per_year'])} | {fmt(r['median_hold'], nd=0)} | "
            f"{fmt(r['stops_per_year'])} |"
        )

    lines += ["", "## Reading this",
              "",
              "For each preset, compare every `stop` row against its own `none`",
              "row (same cadence, same top_n/buffer — the stop is the only thing",
              "that changed). A stop that helps should show up as a shallower",
              "`max DD` without giving back much CAGR; a stop that hurts gives",
              "back CAGR while `max DD` barely moves, because the strategy's",
              "existing drawdowns are usually the WHOLE UNIVERSE falling",
              "together (which no per-position stop can do anything about) —",
              "see the note this script exists to check in the conversation",
              "that produced it."]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%d cells)", out, len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
