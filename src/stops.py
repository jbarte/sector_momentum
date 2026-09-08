"""Trailing stop evaluation — pure functions: no DB, no network, no config read.

The rule, in full:

    peak     = max(close) over [entry_date, today]
    drawdown = latest_close / peak - 1
    breached = drawdown <= -stop_frac

Measured from the PEAK, not from entry, and never from a purchase price — the
`positions` table records only when a user starred an item, not what they paid.
A position up 40% and back to up 23% has breached; that is what makes this a
trailing stop rather than a cost-basis stop, and it is the rule
scripts/stoploss_sweep.py measured.

Callers pass `stop_frac` in (from src.horizons.trailing_stop_frac) so this
module stays testable without config on disk. See
sector_momentum-notes/specs/2026-09-07-live-trailing-stop-loss-design.md.
"""
from __future__ import annotations

import pandas as pd


def evaluate_stop(prices_df: pd.DataFrame | None, entry_date,
                  stop_frac: float) -> dict | None:
    """Peak/drawdown for one holding, or None if it cannot be evaluated.

    None means "no opinion", never "not breached": no price frame, no closes
    at or after `entry_date`, or a non-positive peak. The caller must treat
    None as "skip this position", not as a passing result — silently reading
    it as unbreached would hide a data outage as a clean bill of health.
    """
    if prices_df is None or prices_df.empty or "Close" not in prices_df:
        return None

    entry = pd.Timestamp(entry_date)
    if entry.tzinfo is not None:
        # Production entry_date comes from positions.created_at via
        # psycopg2/pandas and is tz-aware (datetime64[us, UTC]); cached price
        # frames are always tz-naive here. Comparing the two raises TypeError.
        # .normalize() floors to midnight so a star made at any time of day
        # (e.g. 14:30 UTC) still includes that same calendar day's
        # midnight-stamped price bar in the peak window.
        entry = entry.tz_localize(None).normalize()
    window = prices_df.loc[prices_df.index >= entry, "Close"].dropna()
    if window.empty:
        return None

    peak = float(window.max())
    if peak <= 0:
        return None

    latest = float(window.iloc[-1])
    drawdown = latest / peak - 1.0
    return {
        "peak": peak,
        "peak_on": window.idxmax().date(),
        "latest": latest,
        "drawdown": drawdown,
        "stopped_on": window.index[-1].date(),
        "breached": drawdown <= -stop_frac,
    }


def ticker_for(item_type: str, name: str, themes_cfg: dict) -> str | None:
    """The ETF ticker for a position, or None if it isn't a resolvable theme.

    Positions carry item_type 'theme' or the retired 'sector'; only themes have
    an entry in themes.yaml, and a sector position (a leftover row from the
    retired cohort) has no ticker and must be skipped rather than guessed at.
    """
    if item_type != "theme":
        return None
    entry = (themes_cfg.get("themes") or {}).get(name)
    if isinstance(entry, dict):
        return entry.get("ticker")
    return entry if isinstance(entry, str) else None
