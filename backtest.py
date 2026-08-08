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
    from src.horizons import horizons

    with open("config/themes.yaml") as f:
        themes_cfg = yaml.safe_load(f) or {}
    tickers = build_theme_ticker_list(themes_cfg)

    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (cache=%s) …", len(tickers), args.start, end, BACKTEST_CACHE)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end, cache_dir=BACKTEST_CACHE)
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
            rebalance_freq=args.rebalance, buffer=args.buffer)}
    else:
        tracks = {}
        for h in horizons():
            logger.info("Running %-6s (rebalance=%-2s top_n=%d buffer=%d) …",
                        h.key, h.rebalance, h.top_n, h.buffer)
            tracks[h.key] = run_theme_track(
                themes_cfg, prices, top_n=h.top_n, cost_bps=args.cost_bps,
                rebalance_freq=h.rebalance, buffer=h.buffer)

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
