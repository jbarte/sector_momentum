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

DEFAULT_START = "2003-01-01"
BACKTEST_CACHE = "data/backtest_cache"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sector-momentum strategy backtest.")
    p.add_argument("--top-n", type=int, default=5, help="Number of sectors to hold (default 5).")
    p.add_argument("--start", default=DEFAULT_START, help="History start date (YYYY-MM-DD).")
    p.add_argument("--out", default="backtests", help="Output directory.")
    p.add_argument("--cost-bps", type=float, default=0.0,
                   help="One-way transaction cost in basis points, applied on turnover (default 0).")
    p.add_argument("--no-rotations", action="store_true",
                   help="Skip the rotation event-study.")
    p.add_argument("--themes", action="store_true", default=True,
                   help="Run the theme backtest (default: on).")
    p.add_argument("--no-themes", action="store_true",
                   help="Skip the theme backtest.")
    p.add_argument("--theme-top-n", type=int, default=3,
                   help="Number of themes to hold (default 3).")
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
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_all, run_theme_track
    from src.backtest.results import write_results, write_theme_results
    from src.backtest.rotations import load_rotations, event_study

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)

    run_themes = args.themes and not args.no_themes
    themes_cfg: dict = {}
    if run_themes:
        with open("config/themes.yaml") as f:
            themes_cfg = yaml.safe_load(f) or {}
        tickers = list(dict.fromkeys(tickers + build_theme_ticker_list(themes_cfg)))

    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (cache=%s) …", len(tickers), args.start, end, BACKTEST_CACHE)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end, cache_dir=BACKTEST_CACHE)
    logger.info("Got %d / %d tickers", len(prices), len(tickers))

    logger.info("Running sector tracks (top_n=%d, cost_bps=%.0f) …", args.top_n, args.cost_bps)
    tracks = run_all(universe, prices, top_n=args.top_n, cost_bps=args.cost_bps)

    rotations_data = []
    if not args.no_rotations:
        rots = load_rotations("config/rotations.yaml")
        rotations_data = event_study(universe, prices, rots)
        logger.info("Rotation event-study: %d/%d rotations produced", len(rotations_data), len(rots))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = write_results(tracks, out_dir=args.out,
                         generated_at=generated_at,
                         top_n=args.top_n, rotations=rotations_data)

    for region, tr in tracks.items():
        if not tr:
            logger.info("  %s: no result (insufficient data)", region)
            continue
        m = tr["metrics"]
        logger.info("  %s %s→%s | strat CAGR %.1f%% vs bench %.1f%% | Sharpe %.2f | maxDD %.1f%%",
                    region, tr["start"], tr["end"], 100 * m["cagr"],
                    100 * m["benchmark_cagr"], m["sharpe"], 100 * m["max_drawdown"])
    logger.info("Wrote %s", path)

    if run_themes:
        logger.info("Running theme track (top_n=%d, cost_bps=%.0f) …", args.theme_top_n, args.cost_bps)
        theme_track = run_theme_track(themes_cfg, prices, top_n=args.theme_top_n, cost_bps=args.cost_bps)
        theme_path = write_theme_results(theme_track, out_dir="backtests_themes",
                                         generated_at=generated_at, top_n=args.theme_top_n)
        if theme_track:
            m = theme_track["metrics"]
            logger.info("  THEME %s→%s | strat CAGR %.1f%% vs bench %.1f%% | Sharpe %.2f | maxDD %.1f%%",
                        theme_track["start"], theme_track["end"], 100 * m["cagr"],
                        100 * m["benchmark_cagr"], m["sharpe"], 100 * m["max_drawdown"])
        else:
            logger.info("  THEME: no result (insufficient data)")
        logger.info("Wrote %s", theme_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
