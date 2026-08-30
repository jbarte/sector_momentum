#!/usr/bin/env python3
"""Does the UCITS equivalent actually track the US-listed ETF this project scores?

`config/themes.yaml` records the closest UCITS equivalent per theme — ticker,
ISIN, TER, issuer, and a hand-assigned `match` quality of exact/close/partial —
but nothing ever measured whether it tracks. That gap is the difference between
what the leaderboard ranks and what a reader can actually buy on Avanza. This
computes the realized return difference over 3m/6m/1y, per theme, grouped by
`match`, so the label can be checked against data instead of judgement alone.

COMPARISON IS IN EACH LISTING'S OWN CURRENCY — deliberately not converted to a
common one (decided 2026-08-30, Jonas). A percentage return is dimensionless:
the US ETF's USD return and its UCITS equivalent's EUR return are already
comparable without an FX rate, and converting first would inject FX movement
into a number meant to isolate tracking quality, not currency risk.

    python3 scripts/ucits_tracking_monitor.py
    python3 scripts/ucits_tracking_monitor.py --out /tmp/ucits_tracking.md

Shipping has no UCITS equivalent (see themes.yaml) and is absent from the
report, not a zero row — there is nothing to compare it against.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.prices import fetch_prices

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("ucits_tracking_monitor")

WINDOWS = {"3m": 3, "6m": 6, "1y": 12}
FETCH_START = "2023-01-01"  # covers the 1y window with room for the asof lookback
BACKTEST_CACHE = "data/backtest_cache"


def resolve_yf_ticker(ticker: str) -> str:
    """The Yahoo Finance symbol for a UCITS ticker as it appears in themes.yaml.

    All 17 UCITS entries shipped as of 2026-08-30 resolve on Xetra (verified
    live against yfinance) — the common cross-listing exchange for the
    DWS/iShares/VanEck/Invesco/Global X/WisdomTree issuers this config uses. A
    future entry that trades somewhere else is not silently mis-fetched: it
    comes back with no price data, `fetch_prices` logs and omits it, and the
    report shows the pair as unmeasurable rather than wrong.
    """
    return ticker if "." in ticker else f"{ticker}.DE"


def theme_pairs(themes_cfg: dict) -> list[dict]:
    """One dict per (theme, UCITS candidate): the US ticker to compare against.

    A theme with no `ucits` block (Shipping, which has no equivalent at all) is
    skipped — there is nothing to pair it with. A theme is not required to be
    `unbuyable` to be absent; absence from `ucits` is what means "nothing to
    compare here."
    """
    themes = themes_cfg.get("themes", {})
    ucits = themes_cfg.get("ucits", {})
    pairs = []
    for theme, entries in ucits.items():
        us_cfg = themes.get(theme)
        if not us_cfg:
            continue
        us_ticker = us_cfg["ticker"] if isinstance(us_cfg, dict) else us_cfg
        for entry in entries:
            pairs.append({
                "theme": theme,
                "us_ticker": us_ticker,
                "ucits_ticker": entry["ticker"],
                "yf_ticker": resolve_yf_ticker(entry["ticker"]),
                "match": entry["match"],
            })
    return pairs


def trailing_return(series: pd.Series, as_of: pd.Timestamp, months: int) -> float | None:
    """Realized return from `months` ago to the last available price at/before
    `as_of`, in the series' own units (no currency conversion happens here or
    anywhere in this module).

    Uses asof semantics throughout: a calendar month offset rarely lands on a
    trading day, and the most recent bar can be NaN (an incomplete session) —
    both need "the last real price at or before this date," not an exact-index
    lookup. Returns None rather than NaN when the series does not reach back
    far enough to answer the question; a caller distinguishing "measured
    zero" from "could not measure" needs that split explicit.
    """
    if series.empty:
        return None
    series = series.dropna()
    if series.empty:
        return None
    start_date = as_of - pd.DateOffset(months=months)
    if start_date < series.index.min():
        return None
    end_price = series.asof(as_of)
    start_price = series.asof(start_date)
    if pd.isna(end_price) or pd.isna(start_price) or start_price == 0:
        return None
    return float(end_price / start_price - 1)


def tracking_report(pairs: list[dict], prices: dict[str, pd.Series],
                    as_of: pd.Timestamp) -> list[dict]:
    """One row per pair: trailing returns for both listings, in their own
    currency, and the difference (UCITS - US) per window.

    A pair whose price data is missing (fetch failed, or not yet on the grid)
    reports None for every field it cannot compute rather than raising — this
    is a monitor meant to run unattended, and a single bad ticker must not
    blank the rest of the report.
    """
    rows = []
    for pair in pairs:
        us = prices.get(pair["us_ticker"])
        uc = prices.get(pair["yf_ticker"])
        row = dict(pair)
        for label, months in WINDOWS.items():
            us_r = trailing_return(us, as_of, months) if us is not None else None
            uc_r = trailing_return(uc, as_of, months) if uc is not None else None
            row[f"us_{label}"] = us_r
            row[f"ucits_{label}"] = uc_r
            row[f"diff_{label}"] = (uc_r - us_r) if us_r is not None and uc_r is not None else None
        rows.append(row)
    return rows


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="ucits_tracking.md")
    return p.parse_args()


def _load_prices(pairs: list[dict]) -> dict[str, pd.Series]:
    tickers = sorted({p["us_ticker"] for p in pairs} | {p["yf_ticker"] for p in pairs})
    end = date.today().strftime("%Y-%m-%d")
    logger.info("Fetching %d tickers %s → %s …", len(tickers), FETCH_START, end)
    frames = fetch_prices(tickers=tickers, start=FETCH_START, end=end,
                          cache_dir=BACKTEST_CACHE)
    return {t: df["Close"] for t, df in frames.items()}


def main() -> int:
    args = _parse_args()
    with open("config/themes.yaml") as fh:
        themes_cfg = yaml.safe_load(fh) or {}

    pairs = theme_pairs(themes_cfg)
    if not pairs:
        logger.error("No UCITS pairs found in config/themes.yaml.")
        return 1

    prices = _load_prices(pairs)
    as_of = max((s.index.max() for s in prices.values() if len(s)), default=None)
    if as_of is None:
        logger.error("No price data fetched for any pair.")
        return 1

    rows = tracking_report(pairs, prices, as_of)
    _write(rows, as_of, Path(args.out))
    return 0


def _write(rows: list[dict], as_of: pd.Timestamp, out: Path) -> None:
    def fmt(v):
        return "—" if v is None else f"{100 * v:+.1f}%"

    lines = [
        "# UCITS tracking-difference monitor",
        "",
        f"- as of `{as_of.date()}`",
        "- comparison is in EACH LISTING'S OWN CURRENCY — a return is "
        "dimensionless, so no FX conversion happens here",
        "- `diff` = UCITS return − US return; negative means the UCITS "
        "equivalent lagged the listing this project actually scores",
        "",
        "| match | theme | US | UCITS | US 3m | UCITS 3m | diff 3m | "
        "US 6m | UCITS 6m | diff 6m | US 1y | UCITS 1y | diff 1y |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda r: (r["match"], r["theme"])):
        lines.append(
            f"| {r['match']} | {r['theme']} | {r['us_ticker']} | {r['ucits_ticker']} | "
            f"{fmt(r['us_3m'])} | {fmt(r['ucits_3m'])} | {fmt(r['diff_3m'])} | "
            f"{fmt(r['us_6m'])} | {fmt(r['ucits_6m'])} | {fmt(r['diff_6m'])} | "
            f"{fmt(r['us_1y'])} | {fmt(r['ucits_1y'])} | {fmt(r['diff_1y'])} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%d pairs)", out, len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
