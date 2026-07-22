#!/usr/bin/env python3
"""Regime-conditional weighting research harness (dev-only).

Compares the fixed 50/50 level/change baseline against a small set of
regime-conditional weight schemes (SPY vs 200-DMA) across the US and EU monthly
top-N rotation tracks. Prints a comparison table and writes it to markdown.

    python scripts/regime_research.py
    python scripts/regime_research.py --start 2005-01-01 --top-n 5 --out /tmp/regime.md

NOT imported by scan.py / dashboard / backtest.py — running it has no side
effects on backtests/ or config/weights.yaml.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Run from anywhere: put the repo root on sys.path so `src` / `backtest` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("regime_research")

BACKTEST_CACHE = "data/backtest_cache"

# (name, on=(level,change), off=(level,change)); baseline uses weights_fn=None.
SCHEMES = [
    ("Baseline (50/50)", None, None),
    ("V1 on 50/50 off 30/70", (0.50, 0.50), (0.30, 0.70)),
    ("V2 on 60/40 off 30/70", (0.60, 0.40), (0.30, 0.70)),
    ("V3 on 70/30 off 50/50", (0.70, 0.30), (0.50, 0.50)),
    ("Contrarian on 30/70 off 70/30", (0.30, 0.70), (0.70, 0.30)),
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regime-conditional weighting research.")
    p.add_argument("--start", default="2003-01-01")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--out", default="regime_research.md", help="Markdown output path.")
    return p.parse_args()


def _fmt(m: dict) -> str:
    return (f"{100*m['cagr']:.1f} | {m['sharpe']:.2f} | {100*m['max_drawdown']:.1f} "
            f"| {100*m['hit_rate']:.0f} | {100*m['total_return']:.0f} | {100*m['avg_turnover']:.0f}")


def run(args: argparse.Namespace) -> int:
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_track, _track_instruments
    from src.backtest.regime import make_weights_fn, regime_stats
    from src.backtest import replay
    from backtest import build_ticker_list

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)
    if "SPY" not in tickers:
        tickers.append("SPY")
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s …", len(tickers), args.start, end)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end, cache_dir=BACKTEST_CACHE)
    spy_df = prices.get("SPY")
    if spy_df is None:
        logger.error("SPY missing — cannot compute regime. Aborting.")
        return 1

    lines = ["# Regime-conditional weighting — results", "",
             f"Generated over {args.start} → {end}, top-{args.top_n} monthly rotation.", ""]

    for region, bench in [("US", universe["us_benchmark"]), ("EU", universe["eu_benchmark"])]:
        instruments = _track_instruments(universe, region)
        # Regime context over this region's scored calendar.
        cal = replay.month_end_dates(prices[bench].index) if bench in prices else []
        stats = regime_stats(spy_df, cal)
        lines += [f"## {region}",
                  f"Regime context: {100*stats['pct_risk_on']:.0f}% risk-on across "
                  f"{stats['n_dates']} month-ends, {stats['n_switches']} switches.", "",
                  "| Scheme | CAGR% | Sharpe | MaxDD% | Hit% | TotRet% | Turn% |",
                  "|---|---|---|---|---|---|---|"]
        for name, on, off in SCHEMES:
            wf = None if on is None else make_weights_fn(spy_df, on=on, off=off)
            res = run_track(universe, prices, region, bench, instruments,
                            top_n=args.top_n, weights_fn=wf)
            if not res:
                lines.append(f"| {name} | — | — | — | — | — | — |")
                continue
            lines.append(f"| {name} | " + _fmt(res["metrics"]) + " |")
        lines.append("")

    md = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(md)
    print(md)
    logger.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
