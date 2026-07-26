#!/usr/bin/env python3
"""Walk-forward level/change weight validation harness (dev-only).

Phase A grades 11 fixed level/change splits (plus the regime spike's V3) over the
full history for the US and EU tracks. Phase B reuses those per-scheme monthly
return series to build an out-of-sample walk-forward track: every `--cadence`
months, pick the best scheme by Sharpe over the trailing `--window` months and
apply it forward.

    python scripts/walkforward_weights.py
    python scripts/walkforward_weights.py --start 2005-01-01 --window 60 --out /tmp/wf.md

NOT imported by scan.py / dashboard / backtest.py -- running it has no side
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
logger = logging.getLogger("walkforward_weights")

BACKTEST_CACHE = "data/backtest_cache"
BASELINE = "0.5/0.5"
WINDOW_VARIANTS = (36, 60, 120)


def _grid_schemes():
    """[(name, weights_fn_or_None)] for level_weight 0.0..1.0 in 0.1 steps.

    The 0.5/0.5 entry passes weights_fn=None so it exercises the exact same path
    as the live config default (config/weights.yaml is 0.50/0.50).
    """
    out = []
    for i in range(11):
        lw = round(0.1 * i, 1)
        cw = round(1.0 - lw, 1)
        name = f"{lw:.1f}/{cw:.1f}"
        if name == BASELINE:
            out.append((name, None))
        else:
            out.append((name, (lambda l, c: (lambda _d: (l, c)))(lw, cw)))
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward level/change weight validation.")
    p.add_argument("--start", default="2003-01-01")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--window", type=int, default=60,
                   help="Trailing selection window in months (default 60).")
    p.add_argument("--cadence", type=int, default=12,
                   help="Months between re-selections (default 12).")
    p.add_argument("--out", default="walkforward_weights.md", help="Markdown output path.")
    return p.parse_args()


def _fmt_metrics(m: dict) -> str:
    return (f"{100*m['cagr']:.2f} | {m['sharpe']:.2f} | "
            f"{100*m['max_drawdown']:.1f} | {100*m['hit_rate']:.0f} | "
            f"{100*m['avg_turnover']:.0f}")


def run(args: argparse.Namespace) -> int:
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_track, _track_instruments
    from src.backtest.regime import make_weights_fn
    from src.backtest import metrics
    from src.backtest.walkforward import (
        align_scheme_returns, count_switches, returns_from_equity_curve,
        stitch_walk_forward,
    )
    from backtest import build_ticker_list

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)
    if "SPY" not in tickers:
        tickers.append("SPY")
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s …", len(tickers), args.start, end)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end,
                          cache_dir=BACKTEST_CACHE)

    spy_df = prices.get("SPY")
    schemes = _grid_schemes()
    if spy_df is not None:
        schemes.append(("V3 (regime 70/30 on, 50/50 off)",
                        make_weights_fn(spy_df, on=(0.70, 0.30), off=(0.50, 0.50))))
    else:
        logger.warning("SPY missing — skipping the V3 regime candidate.")

    lines = ["# Walk-forward level/change weight validation", "",
             f"Generated over {args.start} → {end}, top-{args.top_n} monthly rotation. "
             f"Selection metric: Sharpe. Cadence: {args.cadence} months.", ""]
    if spy_df is None:
        lines += ["> **Note:** SPY was unavailable, so the V3 regime candidate was "
                  "skipped. The 11 fixed splits are unaffected.", ""]

    for region, bench in [("US", universe["us_benchmark"]),
                          ("EU", universe["eu_benchmark"])]:
        lines += [f"## {region}", ""]
        instruments = _track_instruments(universe, region)

        # ---- Phase A: fixed-scheme grid over full history -------------------
        returns_by_scheme: dict = {}
        bench_returns = None
        lines += ["### Phase A — fixed schemes (full history, in-sample)", "",
                  "| Scheme (level/change) | CAGR% | Sharpe | MaxDD% | Hit% | Turn% |",
                  "|---|---|---|---|---|---|"]
        for name, wfn in schemes:
            res = run_track(universe, prices, region, bench, instruments,
                            top_n=args.top_n, weights_fn=wfn)
            if not res:
                lines.append(f"| {name} | — | — | — | — | — |")
                logger.warning("%s / %s — run_track returned no result", region, name)
                continue
            returns_by_scheme[name] = returns_from_equity_curve(res["equity_curve"])
            if bench_returns is None:
                bench_returns = returns_from_equity_curve(res["equity_curve"],
                                                          key="benchmark")
            lines.append(f"| {name} | " + _fmt_metrics(res["metrics"]) + " |")
        lines.append("")

        if BASELINE not in returns_by_scheme:
            lines += [f"_Baseline {BASELINE} produced no track for {region}; "
                      "walk-forward skipped._", ""]
            logger.error("%s — baseline missing, cannot run walk-forward", region)
            continue

        # ---- Phase B: walk-forward (out-of-sample) --------------------------
        R = align_scheme_returns(returns_by_scheme)
        lines += ["### Phase B — walk-forward (out-of-sample)", "",
                  f"{len(R)} shared months across {len(R.columns)} schemes.", "",
                  "| Track | CAGR% | Sharpe | MaxDD% | Hit% | Switches |",
                  "|---|---|---|---|---|---|"]

        base_rets = R[BASELINE]
        base_eq = metrics.equity_curve(base_rets.reset_index(drop=True))
        base_bench = (bench_returns.reindex(R.index)
                      if bench_returns is not None else None)
        base_hit = (metrics.hit_rate(base_rets.reset_index(drop=True),
                                     base_bench.reset_index(drop=True))
                    if base_bench is not None else float("nan"))
        lines.append(
            f"| Fixed {BASELINE} (incumbent) | {100*metrics.cagr(base_eq):.2f} | "
            f"{metrics.sharpe(base_rets):.2f} | "
            f"{100*metrics.max_drawdown(base_eq):.1f} | {100*base_hit:.0f} | 0 |")

        selection_notes = []
        for w in WINDOW_VARIANTS:
            wf_rets, history = stitch_walk_forward(R, window=w,
                                                   cadence=args.cadence,
                                                   baseline=BASELINE)
            wf_eq = metrics.equity_curve(wf_rets.reset_index(drop=True))
            hit = (metrics.hit_rate(wf_rets.reset_index(drop=True),
                                    base_bench.reset_index(drop=True))
                   if base_bench is not None else float("nan"))
            tag = f"Walk-forward (window {w}m)"
            if w == args.window:
                tag += " **[default]**"
            lines.append(
                f"| {tag} | {100*metrics.cagr(wf_eq):.2f} | "
                f"{metrics.sharpe(wf_rets):.2f} | "
                f"{100*metrics.max_drawdown(wf_eq):.1f} | {100*hit:.0f} | "
                f"{count_switches(history)} |")
            picks = ", ".join(f"{d.strftime('%Y-%m')}→{n}" for d, n in history)
            selection_notes.append(f"- **window {w}m:** {picks or '(no selections)'}")

        lines += ["", "**Selected scheme over time** (instability here means the "
                  "in-sample optimum is noise):", ""]
        lines += selection_notes
        lines.append("")

    lines += ["---", "",
              "**Limitation:** the walk-forward track is stitched from fixed-scheme "
              "return series, so the extra turnover incurred when the selected scheme "
              "changes at a re-selection date is not charged (each track charges "
              "cost_bps on its own internal rotations only). This mildly *flatters* "
              "the adaptive track — so if it still fails to beat the fixed incumbent, "
              "that conclusion is strengthened.", ""]

    md = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(md)
    print(md)
    logger.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(run(_parse_args()))
