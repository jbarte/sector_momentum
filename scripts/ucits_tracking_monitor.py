#!/usr/bin/env python3
"""Does the UCITS equivalent actually track the US-listed ETF this project scores?

`config/themes.yaml` records the closest UCITS equivalent per theme — ticker,
ISIN, TER, issuer, and a hand-assigned `match` quality of exact/close/partial —
but nothing ever measured whether it tracks. That gap is the difference between
what the leaderboard ranks and what a reader can actually buy on Avanza. This
computes the realized return difference over 3m/6m/1y, per theme, grouped by
`match`, so the label can be checked against data instead of judgement alone.

DISPLAYED RETURNS ARE EACH LISTING'S OWN, NATIVE-CURRENCY RETURN — a reader
should see what the EUR-quoted fund actually did in EUR. But every UCITS
equivalent here is unhedged, so its EUR price already carries the EUR/USD move
on top of the US-listed asset it holds: a PERFECTLY tracking fund still shows a
raw return gap equal to the FX move. `diff_*` therefore FX-adjusts the UCITS
side to USD before subtracting (via `fx_adjust_to_usd`), so a currency-year
does not read as a tracking-quality crisis. This corrects the original
2026-08-30 design decision, which reasoned "a percentage return is
dimensionless, so no FX rate is needed" — true for two returns on the same
underlying currency exposure, not true for an unhedged cross-currency wrapper.
Measured impact was small the day this was found (+0.1pp, a quiet FX year) —
the fix is for the year it will not be.

CORRELATION AND ANNUALIZED TRACKING ERROR (weekly returns, FX-adjusted) are
reported alongside the point-in-time diffs. They validate the hand-assigned
`match` label directly and — on the first live pull — ordered it correctly
(median correlation exact 0.93 > close 0.84 > partial 0.54) where the
point-in-time diff did not (close's median |diff| exceeded partial's). Below
`MIN_JOINT_WEEKS` of overlapping weekly returns (a fund too newly listed for
correlation to mean anything) these report None rather than a noisy number.

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
FX_TICKER = "EURUSD=X"  # USD per 1 EUR. Every UCITS entry shipped today is
                        # EUR-quoted (Xetra) — see resolve_yf_ticker.

#: Weekly return observations required before correlation/tracking-error mean
#: anything, rather than being dominated by a handful of noisy points. 26
#: weeks (~6 months) is a floor, not a target — it is enough to reject a fund
#: that IPO'd a few weeks ago, not enough on its own to trust a borderline
#: correlation.
MIN_JOINT_WEEKS = 26


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


def fx_adjust_to_usd(price_eur: pd.Series, fx_usd_per_eur: pd.Series) -> pd.Series:
    """Convert a EUR-quoted price series to its USD equivalent.

    The FX series is forward-filled onto the price series' own dates rather
    than intersected — the two rarely share a calendar exactly (different
    market holidays), and dropping to a bare intersection would silently thin
    the very history the trailing-return and tracking-stats functions need.
    """
    if price_eur.empty or fx_usd_per_eur.empty:
        return pd.Series(dtype=float)
    rate = fx_usd_per_eur.reindex(price_eur.index, method="ffill")
    return (price_eur * rate).dropna()


def weekly_returns(series: pd.Series) -> pd.Series:
    """Friday-to-Friday percent returns from a daily series.

    Weekly, not daily: the US and Xetra sessions close at different times, so
    daily returns compare bars that were never simultaneous. That timing noise
    would swamp any real tracking signal at daily frequency.
    """
    weekly = series.dropna().resample("W-FRI").last().dropna()
    return weekly.pct_change().dropna()


def tracking_stats(us: pd.Series, uc_usd: pd.Series,
                   min_weeks: int = MIN_JOINT_WEEKS) -> dict:
    """Weekly-return correlation and annualized tracking error between two
    USD-denominated series (the caller FX-adjusts before calling this).

    Correlation validates the hand-assigned `match` label far better than a
    single point-in-time return gap does (see the module docstring) — but only
    once there are enough weekly observations for it to mean something. Below
    `min_weeks`, both fields are None rather than a number computed from too
    few points to trust; `n_weeks` is always returned so the report can show
    the reader why.
    """
    a, b = weekly_returns(us), weekly_returns(uc_usd)
    joint = pd.concat([a, b], axis=1, join="inner").dropna()
    n = len(joint)
    if n < min_weeks:
        return {"correlation": None, "tracking_error": None, "n_weeks": n}
    corr = float(joint.iloc[:, 0].corr(joint.iloc[:, 1]))
    te = float((joint.iloc[:, 1] - joint.iloc[:, 0]).std() * (52 ** 0.5))
    return {"correlation": corr, "tracking_error": te, "n_weeks": n}


def tracking_report(pairs: list[dict], prices: dict[str, pd.Series],
                    as_of: pd.Timestamp, fx: pd.Series | None = None) -> list[dict]:
    """One row per pair: trailing returns in each listing's own currency, the
    FX-adjusted difference (UCITS - US), and weekly-return correlation /
    tracking error.

    `fx` (USD per 1 EUR) is optional and, when omitted, `diff_*` falls back to
    subtracting native-currency returns directly — the pre-fix behaviour, kept
    for callers that do not have an FX series to hand — and correlation/
    tracking_error are always None in that case, since they require a common
    currency to mean anything.

    A pair whose price data is missing (fetch failed, or not yet on the grid)
    reports None for every field it cannot compute rather than raising — this
    is a monitor meant to run unattended, and a single bad ticker must not
    blank the rest of the report.
    """
    rows = []
    for pair in pairs:
        us = prices.get(pair["us_ticker"])
        uc = prices.get(pair["yf_ticker"])
        uc_usd = fx_adjust_to_usd(uc, fx) if (uc is not None and fx is not None) else uc
        row = dict(pair)
        for label, months in WINDOWS.items():
            us_r = trailing_return(us, as_of, months) if us is not None else None
            uc_r = trailing_return(uc, as_of, months) if uc is not None else None
            uc_r_adj = (trailing_return(uc_usd, as_of, months)
                       if uc_usd is not None else None)
            row[f"us_{label}"] = us_r
            row[f"ucits_{label}"] = uc_r  # native currency, unconverted, for the reader
            diff_source = uc_r_adj if fx is not None else uc_r
            row[f"diff_{label}"] = (diff_source - us_r
                                    if us_r is not None and diff_source is not None
                                    else None)
        if us is not None and uc_usd is not None and fx is not None:
            row.update(tracking_stats(us, uc_usd))
        else:
            row.update({"correlation": None, "tracking_error": None, "n_weeks": 0})
        rows.append(row)
    return rows


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="ucits_tracking.md")
    return p.parse_args()


def _load_prices(pairs: list[dict]) -> tuple[dict[str, pd.Series], pd.Series | None]:
    tickers = sorted({p["us_ticker"] for p in pairs} | {p["yf_ticker"] for p in pairs}
                     | {FX_TICKER})
    end = date.today().strftime("%Y-%m-%d")
    logger.info("Fetching %d tickers %s → %s …", len(tickers), FETCH_START, end)
    frames = fetch_prices(tickers=tickers, start=FETCH_START, end=end,
                          cache_dir=BACKTEST_CACHE)
    fx = frames.get(FX_TICKER)
    if fx is None:
        # Soft-degrade, not abort: the report is still useful without the FX
        # fix — it just reverts to native-currency diffs and drops
        # correlation/tracking_error, which is exactly what happened before
        # this was added.
        logger.warning("%s fetch failed — diff_* will NOT be FX-adjusted, and "
                       "correlation/tracking_error will be empty", FX_TICKER)
    prices = {t: df["Close"] for t, df in frames.items() if t != FX_TICKER}
    return prices, (fx["Close"] if fx is not None else None)


def main() -> int:
    args = _parse_args()
    with open("config/themes.yaml") as fh:
        themes_cfg = yaml.safe_load(fh) or {}

    pairs = theme_pairs(themes_cfg)
    if not pairs:
        logger.error("No UCITS pairs found in config/themes.yaml.")
        return 1

    prices, fx = _load_prices(pairs)
    as_of = max((s.index.max() for s in prices.values() if len(s)), default=None)
    if as_of is None:
        logger.error("No price data fetched for any pair.")
        return 1

    rows = tracking_report(pairs, prices, as_of, fx=fx)
    _write(rows, as_of, Path(args.out), fx_adjusted=fx is not None)
    return 0


def _write(rows: list[dict], as_of: pd.Timestamp, out: Path,
          fx_adjusted: bool = True) -> None:
    def fmt(v):
        return "—" if v is None else f"{100 * v:+.1f}%"

    def fmt_corr(v):
        return "—" if v is None else f"{v:.2f}"

    diff_note = (
        "- `diff` = UCITS return − US return, FX-ADJUSTED to a common currency "
        "first — an unhedged fund's raw currency return is not what tracking "
        "quality means to measure. `US`/`UCITS` columns are each listing's own, "
        "unconverted native-currency return, for reference."
        if fx_adjusted else
        "- ⚠ FX fetch failed this run — `diff` is the RAW native-currency gap, "
        "unadjusted. Treat any large diff as possibly currency-driven, not "
        "necessarily a tracking problem."
    )
    corr_note = (
        "- `corr`/`track err` are weekly-return correlation and annualized "
        f"tracking error (FX-adjusted). `—` below {MIN_JOINT_WEEKS} weeks of "
        "joint history — a fund too newly listed for either to mean anything."
        if fx_adjusted else
        "- `corr`/`track err` require an FX-adjusted common currency and are "
        "empty this run (FX fetch failed)."
    )

    lines = [
        "# UCITS tracking-difference monitor",
        "",
        f"- as of `{as_of.date()}`",
        diff_note,
        corr_note,
        "",
        "| match | theme | US | UCITS | US 3m | UCITS 3m | diff 3m | "
        "US 6m | UCITS 6m | diff 6m | US 1y | UCITS 1y | diff 1y | "
        "corr | track err | weeks |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda r: (r["match"], r["theme"])):
        lines.append(
            f"| {r['match']} | {r['theme']} | {r['us_ticker']} | {r['ucits_ticker']} | "
            f"{fmt(r['us_3m'])} | {fmt(r['ucits_3m'])} | {fmt(r['diff_3m'])} | "
            f"{fmt(r['us_6m'])} | {fmt(r['ucits_6m'])} | {fmt(r['diff_6m'])} | "
            f"{fmt(r['us_1y'])} | {fmt(r['ucits_1y'])} | {fmt(r['diff_1y'])} | "
            f"{fmt_corr(r['correlation'])} | {fmt(r['tracking_error'])} | "
            f"{r['n_weeks']} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%d pairs)", out, len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
