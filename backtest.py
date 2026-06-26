#!/usr/bin/env python3
"""backtest.py — strategy backtest for the sector-momentum scanner.

Fetches long price history, runs the US and EU top-N monthly rotation
strategies, and writes results to backtests/ for the dashboard to render.

    python backtest.py                 # both tracks, full history
    python backtest.py --top-n 5       # override hold count
    python backtest.py --start 2010-01-01
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("backtest")

DEFAULT_START = "2003-01-01"
BACKTEST_CACHE = "data/backtest_cache"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sector-momentum strategy backtest.")
    p.add_argument("--top-n", type=int, default=5, help="Number of sectors to hold (default 5).")
    p.add_argument("--start", default=DEFAULT_START, help="History start date (YYYY-MM-DD).")
    p.add_argument("--out", default="backtests", help="Output directory.")
    return p.parse_args()


def build_ticker_list(universe: dict) -> list[str]:
    raw = (list(universe.get("us_sectors", {}).values())
           + list(universe.get("eu_sectors", {}).values())
           + [universe["us_benchmark"], universe["eu_benchmark"]])
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def run(args: argparse.Namespace) -> int:
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_all
    from src.backtest.results import write_results

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (cache=%s) …", len(tickers), args.start, end, BACKTEST_CACHE)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end, cache_dir=BACKTEST_CACHE)
    logger.info("Got %d / %d tickers", len(prices), len(tickers))

    logger.info("Running tracks (top_n=%d) …", args.top_n)
    tracks = run_all(universe, prices, top_n=args.top_n)

    path = write_results(tracks, out_dir=args.out,
                         generated_at=datetime.utcnow().isoformat() + "Z", top_n=args.top_n)

    for region, tr in tracks.items():
        if not tr:
            logger.info("  %s: no result (insufficient data)", region)
            continue
        m = tr["metrics"]
        logger.info("  %s %s→%s | strat CAGR %.1f%% vs bench %.1f%% | Sharpe %.2f | maxDD %.1f%%",
                    region, tr["start"], tr["end"], 100 * m["cagr"],
                    100 * m["benchmark_cagr"], m["sharpe"], 100 * m["max_drawdown"])
    logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
