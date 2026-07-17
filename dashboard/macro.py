"""Macro regime context — SPY vs 200-DMA + VIX band."""
from __future__ import annotations
import logging
from datetime import date, timedelta
import pandas as pd

logger = logging.getLogger("dashboard.build")
_SMA_WINDOW = 200
_VIX_THRESHOLDS = (15, 25)

def build_macro_context(spy_df, vix_df):
    if spy_df is None or vix_df is None:
        return None
    if len(spy_df) < _SMA_WINDOW:
        return None
    spy_close = spy_df["Close"]
    spy_last = float(spy_close.iloc[-1])
    spy_sma200 = float(spy_close.rolling(_SMA_WINDOW).mean().iloc[-1])
    spy_distance_pct = (spy_last - spy_sma200) / spy_sma200 * 100
    vix_last = float(vix_df["Close"].iloc[-1])
    if vix_last < _VIX_THRESHOLDS[0]:
        vix_band = "Calm"
    elif vix_last <= _VIX_THRESHOLDS[1]:
        vix_band = "Elevated"
    else:
        vix_band = "Stressed"
    return {
        "spy_last": round(spy_last, 2),
        "spy_sma200": round(spy_sma200, 2),
        "spy_distance_pct": round(spy_distance_pct, 1),
        "spy_above": spy_last > spy_sma200,
        "vix_last": round(vix_last, 2),
        "vix_band": vix_band,
    }

def fetch_macro_data(cache_dir="data/cache"):
    from src.data.prices import fetch_prices
    start = (date.today() - timedelta(days=400)).isoformat()
    end = date.today().isoformat()
    try:
        prices = fetch_prices(["SPY", "^VIX"], start=start, end=end, cache_dir=cache_dir)
    except Exception as exc:
        logger.warning("Macro price fetch failed: %s", exc)
        return None
    return build_macro_context(prices.get("SPY"), prices.get("^VIX"))


def build_page_context(shared: dict) -> dict:
    """Assemble macro regime context (used by all pages)."""
    macro = fetch_macro_data(
        cache_dir=str(shared["project_root"] / "data" / "cache"),
    )
    if macro:
        logger.info(
            "Macro: SPY %+.1f%% vs 200-DMA, VIX %.1f (%s)",
            macro["spy_distance_pct"],
            macro["vix_last"],
            macro["vix_band"],
        )
    else:
        logger.warning("Macro data unavailable — regime bar will be hidden")
    return {
        "macro": macro,
    }
