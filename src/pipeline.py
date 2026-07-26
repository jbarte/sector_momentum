# src/pipeline.py
"""Signal-orchestration helpers shared by the live scan and the backtest.

Pure functions over a {ticker -> OHLCV DataFrame} price dict. No I/O, no
network, no "now": every signal reads the last row of whatever window it is
given, so these can be driven as-of any historical date by truncating prices.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_COLUMNS = [
    "rs_ratio",
    "rs_momentum",
    "return_1m",
    "return_3m",
    "return_6m",
    "acceleration",
    "above_50dma",
    "above_200dma",
    "ma50_slope",
    "obv_slope",
    "breadth_above_50dma",
    "max_dd_1y",
    "rar_3m",
    "rar_6m",
    "calmar_6m",
]


def compute_signals_for_sector(
    sector_key: str,
    region: str,
    gics_sector: str,
    sector_ticker: str,
    benchmark_ticker: str,
    prices: dict[str, pd.DataFrame],
    rs_momentum_fast: int = 5,
) -> dict | None:
    """
    Compute all signal-pillar values for one sector ETF vs its benchmark.

    Returns a flat signal dict or None if the sector should be skipped.
    Errors are caught per-signal so partial data is still returned.
    """
    from src.signals.relative_strength import latest_rrg
    from src.signals.momentum import compute_returns, compute_acceleration
    from src.signals.technical import (
        compute_ma_structure, compute_obv, compute_max_drawdown, compute_realized_vol,
    )

    if sector_ticker not in prices:
        logger.warning("Skipping %s (%s) — ticker %s not in price data", gics_sector, region, sector_ticker)
        return None
    sector_df = prices[sector_ticker]
    if benchmark_ticker not in prices:
        logger.warning("Skipping %s (%s) — benchmark ticker %s not in price data", gics_sector, region, benchmark_ticker)
        return None

    bench_df = prices[benchmark_ticker]

    if "Close" not in sector_df.columns:
        logger.warning("Skipping %s (%s) — no Close column in sector data", gics_sector, region)
        return None

    sector_close = sector_df["Close"]
    bench_close = bench_df["Close"]

    signals: dict[str, float] = {col: float("nan") for col in SIGNAL_COLUMNS}

    # --- Relative strength (RRG) ---
    try:
        rrg = latest_rrg(sector_close, bench_close, fast=rs_momentum_fast)
        signals["rs_ratio"] = rrg["rs_ratio"]
        signals["rs_momentum"] = rrg["rs_momentum"]
    except Exception as exc:
        logger.warning("RRG failed for %s (%s): %s", gics_sector, region, exc)

    # --- Momentum returns ---
    try:
        rets = compute_returns(sector_close)
        signals["return_1m"] = rets.get("1m", float("nan"))
        signals["return_3m"] = rets.get("3m", float("nan"))
        signals["return_6m"] = rets.get("6m", float("nan"))
    except Exception as exc:
        logger.warning("compute_returns failed for %s (%s): %s", gics_sector, region, exc)

    # --- Acceleration ---
    try:
        signals["acceleration"] = compute_acceleration(sector_close)
    except Exception as exc:
        logger.warning("compute_acceleration failed for %s (%s): %s", gics_sector, region, exc)

    # --- MA structure ---
    try:
        ma = compute_ma_structure(sector_close)
        signals["above_50dma"] = ma.get("above_50dma", float("nan"))
        signals["above_200dma"] = ma.get("above_200dma", float("nan"))
        signals["ma50_slope"] = ma.get("ma50_slope", float("nan"))
    except Exception as exc:
        logger.warning("compute_ma_structure failed for %s (%s): %s", gics_sector, region, exc)

    # --- OBV slope ---
    try:
        if "Volume" in sector_df.columns:
            obv = compute_obv(sector_close, sector_df["Volume"])
            signals["obv_slope"] = obv.get("obv_slope", float("nan"))
        else:
            logger.warning("No Volume column for %s (%s) — obv_slope set to NaN", gics_sector, region)
    except Exception as exc:
        logger.warning("compute_obv failed for %s (%s): %s", gics_sector, region, exc)

    # --- Max drawdown (trailing 1y, info-only) ---
    try:
        signals["max_dd_1y"] = compute_max_drawdown(sector_close)
    except Exception as exc:
        logger.warning("compute_max_drawdown failed for %s (%s): %s", gics_sector, region, exc)

    # --- Risk-adjusted momentum (info-only; not part of scoring) ---
    # Divides each return by the risk taken to earn it. Volatility windows match
    # compute_returns' horizons (3m=63d, 6m=126d) so numerator and denominator
    # cover the same period. Guarded so a zero/NaN denominator yields NaN, never inf.
    try:
        def _ratio(numer: float, denom: float) -> float:
            if numer is None or denom is None:
                return float("nan")
            n, d = float(numer), float(denom)
            if not math.isfinite(n) or not math.isfinite(d) or d == 0.0:
                return float("nan")
            return n / d

        vol_3m = compute_realized_vol(sector_close, window=63)
        vol_6m = compute_realized_vol(sector_close, window=126)
        signals["rar_3m"] = _ratio(signals["return_3m"], vol_3m)
        signals["rar_6m"] = _ratio(signals["return_6m"], vol_6m)
        # Drawdown is negative; its magnitude is the risk taken. abs(NaN) is NaN,
        # and _ratio rejects a non-finite or zero denominator, so no extra guard.
        signals["calmar_6m"] = _ratio(signals["return_6m"], abs(float(signals["max_dd_1y"])))
    except Exception as exc:
        logger.warning("risk-adjusted signals failed for %s (%s): %s", gics_sector, region, exc)

    return signals


def build_signals_rows(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    signal_params: dict | None = None,
) -> list[dict]:
    """
    Iterate over all US + EU sectors, compute signals, and collect into a list
    of flat dicts suitable for a long-format DataFrame.

    Each dict has keys: region, gics_sector, sector_key, + all SIGNAL_COLUMNS.
    """
    us_benchmark = universe["us_benchmark"]
    eu_benchmark = universe["eu_benchmark"]
    sp = signal_params or {}
    rs_fast = sp.get("rs_momentum_fast", 5)

    rows: list[dict] = []

    # US sectors
    for gics_sector, ticker in universe.get("us_sectors", {}).items():
        sector_key = f"US|{gics_sector}"
        sig = compute_signals_for_sector(
            sector_key=sector_key,
            region="US",
            gics_sector=gics_sector,
            sector_ticker=ticker,
            benchmark_ticker=us_benchmark,
            prices=prices,
            rs_momentum_fast=rs_fast,
        )
        if sig is None:
            continue
        row = {"region": "US", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    # EU sectors
    for gics_sector, ticker in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        sig = compute_signals_for_sector(
            sector_key=sector_key,
            region="EU",
            gics_sector=gics_sector,
            sector_ticker=ticker,
            benchmark_ticker=eu_benchmark,
            prices=prices,
            rs_momentum_fast=rs_fast,
        )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    return rows


def build_theme_signals_rows(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    signal_params: dict | None = None,
) -> list[dict]:
    """Compute signal rows for each theme ETF vs one global benchmark.

    themes_cfg = {"benchmark": <ticker>, "themes": {name: etf_ticker, ...}}.
    Rows use region="THEME", gics_sector=<name>, sector_key="THEME|<name>", and all
    SIGNAL_COLUMNS. breadth_above_50dma stays NaN (themes have no constituent list).
    A theme whose ETF has no price data is skipped. The benchmark falls back to "SPY"
    when the configured benchmark ticker is absent from ``prices``.
    """
    benchmark = themes_cfg.get("benchmark") or "ACWI"
    if benchmark not in prices and "SPY" in prices:
        logger.warning("Themes benchmark %s unavailable — falling back to SPY", benchmark)
        benchmark = "SPY"

    sp = signal_params or {}
    rs_fast = sp.get("rs_momentum_fast", 5)

    rows: list[dict] = []
    for name, cfg in themes_cfg.get("themes", {}).items():
        ticker = cfg["ticker"] if isinstance(cfg, dict) else cfg
        if ticker not in prices:
            logger.warning("Theme %s: ETF %s has no price data — skipping", name, ticker)
            continue
        sector_key = f"THEME|{name}"
        sig = compute_signals_for_sector(
            sector_key=sector_key,
            region="THEME",
            gics_sector=name,
            sector_ticker=ticker,
            benchmark_ticker=benchmark,
            prices=prices,
            rs_momentum_fast=rs_fast,
        )
        if sig is None:
            continue
        row = {"region": "THEME", "gics_sector": name, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)
    return rows
