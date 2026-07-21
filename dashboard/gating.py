"""Leaderboard freshness gating: pick a lagged scan for the static build.

Guests see the leaderboard as of the newest scan at least LAG_DAYS old;
authenticated users upgrade to the latest scan client-side. Pure functions —
no DB or network I/O.
"""
from __future__ import annotations

import pandas as pd

LAG_DAYS = 7


def _pick_lagged_scan(
    history_df: pd.DataFrame,
    lag_days: int = LAG_DAYS,
    now: pd.Timestamp | None = None,
) -> int | None:
    """Newest scan_id whose run_at age >= lag_days; else the oldest; None if empty."""
    if history_df is None or history_df.empty:
        return None
    if now is None:
        now = pd.Timestamp.now("UTC")

    per_scan = (
        history_df[["scan_id", "run_at"]]
        .drop_duplicates(subset="scan_id")
        .copy()
    )
    per_scan["ts"] = pd.to_datetime(per_scan["run_at"], utc=True, format="ISO8601")
    cutoff = now - pd.Timedelta(days=lag_days)

    old_enough = per_scan[per_scan["ts"] <= cutoff]
    if not old_enough.empty:
        # newest scan that is still at least lag_days old
        return int(old_enough.sort_values("ts").iloc[-1]["scan_id"])

    # nothing old enough — fall back to the oldest scan we have
    return int(per_scan.sort_values("ts").iloc[0]["scan_id"])


def apply_leaderboard_lag(
    history_df: pd.DataFrame,
    *,
    lag_active: bool,
    lag_days: int = LAG_DAYS,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, int | None, str | None]:
    """Resolve the scan the baked leaderboard should render.

    Returns (leaderboard_history_df, scan_id, banner_date).
    - lag_active False: (history_df unchanged, max scan_id or None, None).
    - lag_active True: (history filtered to scan_id <= lagged, lagged scan_id,
      lagged scan date "YYYY-MM-DD").
    """
    if history_df is None or history_df.empty:
        return (history_df if history_df is not None else pd.DataFrame(),
                None, None)

    if not lag_active:
        return history_df, int(history_df["scan_id"].max()), None

    lag_id = _pick_lagged_scan(history_df, lag_days=lag_days, now=now)
    if lag_id is None:
        return history_df, int(history_df["scan_id"].max()), None

    lag_df = history_df[history_df["scan_id"] <= lag_id].copy()
    run_at = lag_df[lag_df["scan_id"] == lag_id]["run_at"].iloc[0]
    banner_date = pd.to_datetime(run_at, utc=True, format="ISO8601").strftime("%Y-%m-%d")
    return lag_df, lag_id, banner_date
