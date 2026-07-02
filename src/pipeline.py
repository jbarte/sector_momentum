# src/pipeline.py
"""Signal-orchestration helpers shared by the live scan and the backtest.

Pure functions over a {ticker -> OHLCV DataFrame} price dict. No I/O, no
network, no "now": every signal reads the last row of whatever window it is
given, so these can be driven as-of any historical date by truncating prices.
"""
from __future__ import annotations

import logging

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
]


def build_composite_series(
    tickers: list[str],
    prices: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    """Equal-weight composite of component ETFs.

    Close = mean of each component's Close rebased to 100 at the common
    (inner-join) start date; Volume = summed component volumes on that index.
    Returns None if no component has usable Close data.
    """
    closes, vols = [], []
    for t in tickers:
        df = prices.get(t)
        if df is None or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        if c.empty:
            continue
        closes.append(c.rename(t))
        if "Volume" in df.columns:
            vols.append(df["Volume"].rename(t))
    if not closes:
        return None
    close_df = pd.concat(closes, axis=1, join="inner").dropna()
    if close_df.empty:
        return None
    rebased = close_df / close_df.iloc[0] * 100.0
    out = pd.DataFrame({"Close": rebased.mean(axis=1)})
    if vols:
        vol_df = pd.concat(vols, axis=1, join="inner").reindex(out.index)
        # min_count=1 ensures a row with no component volume stays NaN, not 0.
        out["Volume"] = vol_df.sum(axis=1, min_count=1)
    return out


def compute_signals_for_sector(
    sector_key: str,
    region: str,
    gics_sector: str,
    sector_ticker: str,
    benchmark_ticker: str,
    prices: dict[str, pd.DataFrame],
    sector_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Compute all signal-pillar values for one sector ETF vs its benchmark.

    Returns a flat signal dict or None if the sector should be skipped.
    Errors are caught per-signal so partial data is still returned.
    When sector_df is provided, signals are computed from it instead of
    prices[sector_ticker].
    """
    from src.signals.relative_strength import latest_rrg
    from src.signals.momentum import compute_returns, compute_acceleration
    from src.signals.technical import compute_ma_structure, compute_obv

    if sector_df is None:
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
        rrg = latest_rrg(sector_close, bench_close)
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

    return signals


def build_signals_rows(
    universe: dict,
    prices: dict[str, pd.DataFrame],
) -> list[dict]:
    """
    Iterate over all US + EU sectors, compute signals, and collect into a list
    of flat dicts suitable for a long-format DataFrame.

    Each dict has keys: region, gics_sector, sector_key, + all SIGNAL_COLUMNS.
    """
    us_benchmark = universe["us_benchmark"]
    eu_benchmark = universe["eu_benchmark"]

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
        )
        if sig is None:
            continue
        row = {"region": "US", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    # EU sectors
    for gics_sector, value in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        tickers = value if isinstance(value, list) else [value]
        if len(tickers) == 1:
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker=tickers[0], benchmark_ticker=eu_benchmark, prices=prices,
            )
        else:
            comp = build_composite_series(tickers, prices)
            if comp is None:
                logger.warning("Skipping EU %s — no composite data for %s", gics_sector, tickers)
                continue
            sig = compute_signals_for_sector(
                sector_key=sector_key, region="EU", gics_sector=gics_sector,
                sector_ticker="+".join(tickers), benchmark_ticker=eu_benchmark,
                prices=prices, sector_df=comp,
            )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    return rows
