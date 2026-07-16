"""Badge scorecard — historical hit rates for Entry/Exit and trajectory badges."""
from __future__ import annotations

import statistics
from datetime import timedelta

import pandas as pd

from dashboard.rows import _compute_rank_trajectories, _compute_setup, _safe_float
from src.backtest.strategy import close_at
from src.data.prices import fetch_prices

FORWARD_DAYS = 5
MIN_OBS = 3
MIN_SCANS = 6
TRAJECTORY_WINDOW = 5

_BADGE_ORDER = [
    ("▲ Entry", "entry", True),
    ("↑↑ Rising fast", "rising_fast", True),
    ("↑ Rising", "rising", True),
    ("→ Flat", "flat", None),
    ("↓ Falling", "falling", False),
    ("↓↓ Falling fast", "falling_fast", False),
    ("▼ Exit", "exit", False),
    ("No badge", "no_badge", None),
]

_TRAJ_STATE_TO_KEY = {
    "strong_up": "rising_fast",
    "up": "rising",
    "flat": "flat",
    "down": "falling",
    "strong_down": "falling_fast",
}


def _sector_ticker_map(universe: dict) -> dict[str, str]:
    """Build {region|sector: ticker} from universe config.

    EU sectors may be lists (composites); use the first ticker.
    """
    result: dict[str, str] = {}
    for sector, ticker in universe.get("us_sectors", {}).items():
        result[f"US|{sector}"] = ticker
    for sector, ticker_or_list in universe.get("eu_sectors", {}).items():
        if isinstance(ticker_or_list, list):
            ticker = ticker_or_list[0] if ticker_or_list else None
        else:
            ticker = ticker_or_list
        if ticker:
            result[f"EU|{sector}"] = ticker
    return result


def _forward_date(
    price_index: pd.DatetimeIndex, scan_date: pd.Timestamp, n: int
) -> pd.Timestamp | None:
    """Find the n-th trading day after scan_date using the price index."""
    future = price_index[price_index > scan_date]
    if len(future) < n:
        return None
    return future[n - 1]


def build_badge_scorecard(
    history_df: pd.DataFrame,
    universe: dict,
    price_cache_dir: str = "data/cache",
) -> list[dict]:
    """Compute badge scorecard stats from scan history and cached prices.

    For each scan (once at least 5 prior scans exist, so a rank trajectory
    can be computed) and each sector in that scan, buckets the sector's
    forward N-day return by its trajectory badge, plus separately by
    Entry/Exit setup badge.

    Returns a list of 8 dicts (one per badge type) ordered per _BADGE_ORDER,
    or an empty list if fewer than MIN_SCANS scans are available.
    """
    if history_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) < MIN_SCANS:
        return []

    ticker_map = _sector_ticker_map(universe)
    all_tickers = sorted(set(ticker_map.values()))

    # run_at may be tz-naive or tz-aware depending on the source; normalise
    # to tz-naive UTC so comparisons against the (tz-naive) price index work.
    scan_dates: dict = {}
    for sid in scan_ids:
        mask = history_df["scan_id"] == sid
        run_at = pd.to_datetime(history_df.loc[mask, "run_at"].iloc[0], utc=True)
        scan_dates[sid] = run_at.tz_localize(None).normalize()

    earliest = min(scan_dates.values()) - timedelta(days=10)
    latest = max(scan_dates.values()) + timedelta(days=15)
    prices = fetch_prices(
        all_tickers,
        start=earliest.strftime("%Y-%m-%d"),
        end=latest.strftime("%Y-%m-%d"),
        cache_dir=price_cache_dir,
    )

    observations: dict[str, list[float]] = {key: [] for _, key, _ in _BADGE_ORDER}

    for idx in range(TRAJECTORY_WINDOW, len(scan_ids)):
        window_ids = scan_ids[idx - (TRAJECTORY_WINDOW - 1) : idx + 1]
        window_df = history_df[history_df["scan_id"].isin(window_ids)].copy()

        current_sid = scan_ids[idx]
        current_rows = history_df[history_df["scan_id"] == current_sid]
        scan_date = scan_dates[current_sid]

        trajectories = _compute_rank_trajectories(window_df)

        for _, row_data in current_rows.iterrows():
            region = row_data["region"]
            sector = row_data["gics_sector"]
            sk = f"{region}|{sector}"

            traj = trajectories.get(sk, {"state": "flat"})
            traj_state = traj["state"]
            traj_key = _TRAJ_STATE_TO_KEY.get(traj_state, "flat")

            row_dict = {
                "_raw_composite": _safe_float(row_data.get("composite")),
                "_raw_change": _safe_float(row_data.get("change_score")),
                "trajectory_state": traj_state,
            }
            _compute_setup(row_dict)
            setup = row_dict["setup"]

            ticker = ticker_map.get(sk)
            if not ticker or ticker not in prices:
                continue
            price_df = prices[ticker]
            fwd_date = _forward_date(price_df.index, scan_date, FORWARD_DAYS)
            if fwd_date is None:
                continue

            p0 = close_at(price_df, scan_date)
            p1 = close_at(price_df, fwd_date)
            if not p0 or not p1 or p0 != p0 or p1 != p1:
                continue
            fwd_ret = p1 / p0 - 1.0

            observations[traj_key].append(fwd_ret)
            if setup == "entry":
                observations["entry"].append(fwd_ret)
            elif setup == "exit":
                observations["exit"].append(fwd_ret)
            else:
                observations["no_badge"].append(fwd_ret)

    result: list[dict] = []
    for label, key, bullish in _BADGE_ORDER:
        obs = observations[key]
        count = len(obs)
        if count < MIN_OBS:
            result.append({
                "badge": label,
                "badge_key": key,
                "count": count,
                "hit_rate": None,
                "mean_return": None,
                "median_return": None,
            })
        else:
            if bullish is True:
                hits = sum(1 for r in obs if r > 0)
            elif bullish is False:
                hits = sum(1 for r in obs if r < 0)
            else:  # neutral (flat / no-badge): % positive as baseline
                hits = sum(1 for r in obs if r > 0)
            result.append({
                "badge": label,
                "badge_key": key,
                "count": count,
                "hit_rate": round(hits / count, 3),
                "mean_return": round(statistics.mean(obs), 6),
                "median_return": round(statistics.median(obs), 6),
            })

    return result
