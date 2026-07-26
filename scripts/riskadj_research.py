#!/usr/bin/env python3
"""Risk-adjusted momentum signal research harness (dev-only).

Compares the baseline level signal set against variants that substitute (or add)
risk-adjusted momentum signals, across the US and EU monthly top-N rotation
tracks. Also reports per-era metrics (a win must be consistent, not one-regime
luck) and the cross-sectional correlation of each new signal against the existing
level signals (a return/vol signal is correlated with the return it is built from,
so an *additive* variant mostly re-weights momentum rather than testing risk
adjustment).

    python scripts/riskadj_research.py
    python scripts/riskadj_research.py --start 2005-01-01 --out /tmp/riskadj.md

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
logger = logging.getLogger("riskadj_research")

BACKTEST_CACHE = "data/backtest_cache"
BASELINE_LEVEL = ["rs_ratio", "return_3m", "return_6m", "above_50dma"]
N_ERAS = 3

# (name, level_signals or None for the baseline default)
VARIANTS = [
    ("Baseline", None),
    ("V1 additive rar_6m",      ["rs_ratio", "return_3m", "return_6m", "above_50dma", "rar_6m"]),
    ("V2 sub 6m -> rar_6m",     ["rs_ratio", "return_3m", "rar_6m", "above_50dma"]),
    ("V3 sub both -> rar",      ["rs_ratio", "rar_3m", "rar_6m", "above_50dma"]),
    ("V4 sub 6m -> calmar_6m",  ["rs_ratio", "return_3m", "calmar_6m", "above_50dma"]),
]
NEW_SIGNALS = ["rar_3m", "rar_6m", "calmar_6m"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Risk-adjusted momentum signal research.")
    p.add_argument("--start", default="2003-01-01")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=0.0,
                   help="Per-rotation cost in bps (default 0.0, matching backtest.py).")
    p.add_argument("--out", default="riskadj_research.md", help="Markdown output path.")
    return p.parse_args()


def _fmt_metrics(m: dict) -> str:
    return (f"{100*m['cagr']:.2f} | {m['sharpe']:.2f} | "
            f"{100*m['max_drawdown']:.1f} | {100*m['hit_rate']:.0f} | "
            f"{100*m['avg_turnover']:.0f}")


def _era_rows(rets, n_eras: int):
    """[(label, cagr, sharpe)] for n_eras contiguous slices of a return series."""
    from src.backtest import metrics
    out = []
    if rets is None or len(rets) < n_eras * 2:
        return out
    size = len(rets) // n_eras
    for i in range(n_eras):
        lo = i * size
        hi = len(rets) if i == n_eras - 1 else (i + 1) * size
        chunk = rets.iloc[lo:hi]
        if len(chunk) < 2:
            continue
        eq = metrics.equity_curve(chunk.reset_index(drop=True))
        label = f"{chunk.index[0].strftime('%Y-%m')}→{chunk.index[-1].strftime('%Y-%m')}"
        out.append((label, metrics.cagr(eq), metrics.sharpe(chunk)))
    return out


def _correlation_lines(universe, prices, region) -> list[str]:
    """Cross-sectional correlation of the new signals vs the baseline level set,
    measured on the most recent scored month-end."""
    import pandas as pd
    from src.backtest import replay
    from src.pipeline import build_signals_rows

    bench = universe["us_benchmark"] if region == "US" else universe["eu_benchmark"]
    if bench not in prices:
        return ["_benchmark missing — correlation skipped._", ""]
    cal = replay.month_end_dates(prices[bench].index)
    if not cal:
        return ["_no month-ends — correlation skipped._", ""]

    truncated = replay.truncate_prices(prices, cal[-1])
    rows = [r for r in build_signals_rows(universe, truncated) if r["region"] == region]
    if len(rows) < 3:
        return ["_too few sectors — correlation skipped._", ""]
    df = pd.DataFrame(rows)

    lines = ["| New signal | " + " | ".join(BASELINE_LEVEL) + " |",
             "|---" * (len(BASELINE_LEVEL) + 1) + "|"]
    for new in NEW_SIGNALS:
        if new not in df.columns:
            continue
        cells = []
        for base in BASELINE_LEVEL:
            if base not in df.columns:
                cells.append("—")
                continue
            r = df[[new, base]].astype(float).corr().iloc[0, 1]
            cells.append("—" if pd.isna(r) else f"{r:+.2f}")
        lines.append(f"| {new} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"_Cross-sectional Pearson r across {len(rows)} {region} sectors "
                 f"as of {cal[-1].strftime('%Y-%m-%d')}._")
    lines.append("")
    return lines


def run(args: argparse.Namespace) -> int:
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_track, _track_instruments
    from src.backtest.walkforward import returns_from_equity_curve
    from backtest import build_ticker_list

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s …", len(tickers), args.start, end)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end,
                          cache_dir=BACKTEST_CACHE)

    lines = ["# Risk-adjusted momentum — signal research", "",
             f"Generated over {args.start} → {end}, top-{args.top_n} monthly rotation, "
             f"cost_bps={args.cost_bps:.0f}. Change signal set unchanged in every variant.",
             ""]

    for region, bench in [("US", universe["us_benchmark"]),
                          ("EU", universe["eu_benchmark"])]:
        lines += [f"## {region}", ""]
        instruments = _track_instruments(universe, region)

        lines += ["### Variants (full history)", "",
                  "| Variant | CAGR% | Sharpe | MaxDD% | Hit% | Turn% |",
                  "|---|---|---|---|---|---|"]
        rets_by_variant: dict = {}
        for name, level_signals in VARIANTS:
            res = run_track(universe, prices, region, bench, instruments,
                            top_n=args.top_n, cost_bps=args.cost_bps,
                            level_signals=level_signals)
            if not res:
                lines.append(f"| {name} | — | — | — | — | — |")
                logger.warning("%s / %s — run_track returned no result", region, name)
                continue
            rets_by_variant[name] = returns_from_equity_curve(res["equity_curve"])
            lines.append(f"| {name} | " + _fmt_metrics(res["metrics"]) + " |")
        lines.append("")

        lines += [f"### Per-era consistency ({N_ERAS} contiguous eras)", "",
                  "A variant that only wins in one era does not clear the bar.", "",
                  "| Variant | Era | CAGR% | Sharpe |", "|---|---|---|---|"]
        for name, _ in VARIANTS:
            for label, c, s in _era_rows(rets_by_variant.get(name), N_ERAS):
                lines.append(f"| {name} | {label} | {100*c:.2f} | {s:.2f} |")
        lines.append("")

        lines += ["### Signal correlation (redundancy check)", ""]
        lines += _correlation_lines(universe, prices, region)

    lines += ["---", "",
              "**Reading note:** a return/volatility signal is correlated with the "
              "raw return it is built from, so the *additive* variant (V1) largely "
              "re-weights momentum rather than testing risk adjustment. The "
              "substitutive variants (V2/V3/V4) are the real test of the hypothesis. "
              "Trading costs are not modelled unless --cost-bps is set; turnover is "
              "reported but not charged.", ""]

    md = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(md)
    print(md)
    logger.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(run(_parse_args()))
