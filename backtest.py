#!/usr/bin/env python3
"""backtest.py — strategy backtest for the sector-momentum scanner.

Fetches long price history, runs the US and EU top-N monthly rotation
strategies, and writes results to backtests/ for the dashboard to render.

    python backtest.py                      # every preset, full history
    python backtest.py --top-n 5            # override hold count
    python backtest.py --start 2015-01-01 --out /tmp/bt   # windowed exploration

`--start` bounds the EVALUATION window; history always comes from
replay.FETCH_START. A windowed run must pass --out: it may not overwrite the
committed backtests/ artifact the dashboard renders.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("backtest")

# Fetch window, evaluation window and the warm-up rule they must respect all
# live in src.backtest.replay, shared with scripts/horizon_sweep.py. Two
# copies of these is how this file and the sweep came to disagree.
from src.backtest.replay import (  # noqa: E402
    DEFAULT_EVAL_START, FETCH_START, validate_eval_start,
)

BACKTEST_CACHE = "data/backtest_cache"

# Git-tracked and rendered by the dashboard, so it is guarded above: only a
# default-window run may write here.
DEFAULT_OUT = "backtests"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sector-momentum strategy backtest.")
    p.add_argument("--top-n", type=int, default=5, help="Number of sectors to hold (default 5).")
    p.add_argument("--start", default=DEFAULT_EVAL_START,
                   help="Start of the EVALUATION window (YYYY-MM-DD). Price history "
                        "is always fetched from " + FETCH_START + ", so trailing-window "
                        "signals are warm on the first evaluated date.")
    p.add_argument("--out", default=DEFAULT_OUT, help="Output directory.")
    p.add_argument("--cost-bps", type=float, default=None,
                   help="Round-trip transaction cost in basis points, applied on turnover. "
                        "Defaults to costs.round_trip_bps in config/weights.yaml. "
                        "Pass 0 to reproduce the pre-2026-08-09 cost-free figures.")
    p.add_argument("--no-rotations", action="store_true",
                   help="Skip the rotation event-study.")
    p.add_argument("--themes", action="store_true", default=True,
                   help="Run the theme backtest (default: on).")
    p.add_argument("--no-themes", action="store_true",
                   help="Skip the theme backtest.")
    p.add_argument("--theme-top-n", type=int, default=3,
                   help="Number of themes to hold (default 3).")
    p.add_argument("--rebalance", default="M", choices=["W", "2W", "M", "2M", "Q"],
                   help="Rebalance cadence (default M = month-end).")
    p.add_argument("--buffer", type=int, default=0,
                   help="Hysteresis band in ranks: hold while rank <= top_n + buffer "
                        "(default 0 = sell as soon as a name leaves the top N).")
    return p.parse_args()


def build_theme_ticker_list(themes_cfg: dict) -> list[str]:
    tickers = [
        (cfg["ticker"] if isinstance(cfg, dict) else cfg)
        for cfg in themes_cfg.get("themes", {}).values()
    ]
    bench = themes_cfg.get("benchmark") or "ACWI"
    if bench not in tickers:
        tickers.append(bench)
    if "SPY" not in tickers:
        tickers.append("SPY")
    return tickers


def run(args: argparse.Namespace) -> int:
    import yaml
    from src.data.prices import fetch_prices
    from src.backtest.engine import run_theme_track
    from src.backtest.results import write_results
    from src.horizons import horizons, round_trip_bps

    try:
        validate_eval_start(args.start)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    # A non-default window must not silently overwrite the SHIPPED artifact.
    # `backtests/` is git-tracked and the dashboard's Backtest tab renders it, so
    # an exploratory `--start 2015-01-01` would replace the published curve with
    # a windowed one and say nothing. Refuse instead, and name the way out.
    # Windowed runs only became useful with this fix, so the hazard arrived with
    # it.
    # realpath, not string equality: `--out backtests/`, `--out ./backtests` and
    # an absolute path all name the tracked directory and all bypassed a `==`.
    if (args.start != DEFAULT_EVAL_START
            and os.path.realpath(args.out) == os.path.realpath(DEFAULT_OUT)):
        logger.error(
            "refusing to overwrite the committed artifact in %s/ with a windowed "
            "run (--start %s). Pass --out somewhere else, e.g. --out /tmp/bt.",
            DEFAULT_OUT, args.start)
        return 1

    # None (not 0) means "unset" so an explicit `--cost-bps 0` still works for
    # reproducing the historical cost-free figures.
    if args.cost_bps is None:
        args.cost_bps = round_trip_bps()
    logger.info("Transaction cost: %.0f bps round-trip", args.cost_bps)

    with open("config/themes.yaml") as f:
        themes_cfg = yaml.safe_load(f) or {}
    tickers = build_theme_ticker_list(themes_cfg)

    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (evaluating from %s, cache=%s) …",
                len(tickers), FETCH_START, end, args.start, BACKTEST_CACHE)
    prices = fetch_prices(tickers=tickers, start=FETCH_START, end=end,
                          cache_dir=BACKTEST_CACHE)
    logger.info("Got %d / %d tickers", len(prices), len(tickers))

    # One track per horizon preset, keyed by preset. The dashboard's Backtest
    # tab switches on this key, so every preset the reader can select must have
    # a curve here — a missing key renders an empty chart.
    #
    # --rebalance/--buffer/--theme-top-n override the presets entirely and
    # produce a single ad-hoc track, for one-off "what if" runs.
    if args.rebalance != "M" or args.buffer != 0 or args.theme_top_n != 3:
        logger.info("Ad-hoc track (top_n=%d, rebalance=%s, buffer=%d) — presets skipped",
                    args.theme_top_n, args.rebalance, args.buffer)
        tracks = {"custom": run_theme_track(
            themes_cfg, prices, top_n=args.theme_top_n, cost_bps=args.cost_bps,
            rebalance_freq=args.rebalance, buffer=args.buffer, since=args.start)}
    else:
        tracks = {}
        for h in horizons():
            logger.info("Running %-6s (rebalance=%-2s top_n=%d buffer=%d) …",
                        h.key, h.rebalance, h.top_n, h.buffer)
            tracks[h.key] = run_theme_track(
                themes_cfg, prices, top_n=h.top_n, cost_bps=args.cost_bps,
                rebalance_freq=h.rebalance, buffer=h.buffer, since=args.start)

    # ANY missing track is a failed run, not just an all-empty one. A partial
    # result is the more dangerous case: `long` needs one more scored theme than
    # `medium` (score_calendar's min_members=top_n), so a thin window can write
    # `{"medium": {...}, "long": null}` over the artifact and exit 0 — leaving a
    # stale equity_long.csv beside it and silently dropping Long from the
    # dashboard's Backtest tab. Fail, leaving whatever was there intact.
    empty = [k for k, v in tracks.items() if not v]
    if empty:
        logger.error("no result for %s (evaluating from %s) — nothing written; "
                     "%s/ left unchanged",
                     ", ".join(sorted(empty)), args.start, args.out)
        return 1

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = write_results(tracks, out_dir=args.out,
                         generated_at=generated_at, top_n=args.top_n)

    for region, tr in tracks.items():
        if not tr:
            logger.info("  %s: no result (insufficient data)", region)
            continue
        m = tr["metrics"]
        logger.info("  %-6s %s→%s | CAGR %.1f%% vs %.1f%% | Sharpe %.2f | maxDD %.1f%% "
                    "| %.0f trades/yr | median hold %s d",
                    region, tr["start"], tr["end"], 100 * m["cagr"],
                    100 * m["benchmark_cagr"], m["sharpe"], 100 * m["max_drawdown"],
                    m.get("trades_per_year") or 0, m.get("median_holding_days"))
    logger.info("Wrote %s", path)

    return 0


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
