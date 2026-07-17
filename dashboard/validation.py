"""Forward-return validation & holding-period stats for the Backtest tab."""
from __future__ import annotations

import statistics
from datetime import timedelta

import pandas as pd

from dashboard.badges import _forward_date, _sector_ticker_map
from src.backtest.strategy import close_at
from src.data.prices import fetch_prices

RANK_THRESHOLD = 5
MIN_SCANS = 10
HORIZONS = [5, 21]
HORIZON_LABELS = {5: "5d", 21: "1m"}


def _top5_runs(history_df: pd.DataFrame, region: str) -> list[dict]:
    """Extract contiguous top-5 rank streaks per sector for a single region."""
    if history_df.empty:
        return []

    region_df = history_df[history_df["region"] == region]
    if region_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    scan_id_to_idx = {sid: idx for idx, sid in enumerate(scan_ids)}

    runs: list[dict] = []
    for sector, group in region_df.groupby("gics_sector"):
        ranked = group.set_index("scan_id")["rank"].to_dict()
        in_run = False
        entry_idx = 0
        last_idx = 0

        for sid in scan_ids:
            idx = scan_id_to_idx[sid]
            rank = ranked.get(sid)
            if rank is not None and rank <= RANK_THRESHOLD:
                if not in_run:
                    entry_idx = idx
                    in_run = True
                last_idx = idx
            else:
                if in_run:
                    runs.append({
                        "region": region,
                        "sector": sector,
                        "entry_scan_idx": entry_idx,
                        "exit_scan_idx": last_idx,
                        "duration": last_idx - entry_idx + 1,
                        "ongoing": False,
                    })
                    in_run = False

        if in_run:
            runs.append({
                "region": region,
                "sector": sector,
                "entry_scan_idx": entry_idx,
                "exit_scan_idx": last_idx,
                "duration": last_idx - entry_idx + 1,
                "ongoing": True,
            })

    return runs


def _holding_stats(runs: list[dict], region_label: str) -> dict:
    """Aggregate top-5 run durations into summary statistics."""
    completed = [r for r in runs if not r["ongoing"]]
    ongoing_count = sum(1 for r in runs if r["ongoing"])
    durations = [r["duration"] for r in completed]

    if not durations:
        return {
            "region": region_label,
            "runs": 0,
            "ongoing": ongoing_count,
            "median": None,
            "mean": None,
            "min": None,
            "max": None,
        }

    return {
        "region": region_label,
        "runs": len(durations),
        "ongoing": ongoing_count,
        "median": round(statistics.median(durations)),
        "mean": round(statistics.mean(durations), 1),
        "min": min(durations),
        "max": max(durations),
    }


def _compute_forward_returns(
    history_df: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    benchmark_prices: dict[str, pd.DataFrame],
    region: str,
    benchmark_ticker: str,
    ticker_map: dict[str, str],
    horizons: list[int],
) -> list[dict]:
    """Compute excess forward returns for every top-5 observation in a region."""
    region_df = history_df[history_df["region"] == region]
    if region_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    scan_dates: dict[int, pd.Timestamp] = {}
    for sid in scan_ids:
        mask = history_df["scan_id"] == sid
        run_at = pd.to_datetime(
            history_df.loc[mask, "run_at"].iloc[0], utc=True
        )
        scan_dates[sid] = run_at.tz_localize(None).normalize()

    bench_df = benchmark_prices.get(benchmark_ticker)
    observations: list[dict] = []

    for _, row in region_df.iterrows():
        if row["rank"] > RANK_THRESHOLD:
            continue

        sector = row["gics_sector"]
        sk = f"{region}|{sector}"
        ticker = ticker_map.get(sk)
        if not ticker or ticker not in prices:
            continue

        sid = row["scan_id"]
        scan_date = scan_dates.get(sid)
        if scan_date is None:
            continue

        price_df = prices[ticker]
        excess: dict[int, float] = {}
        complete = True

        for h in horizons:
            fwd_date = _forward_date(price_df.index, scan_date, h)
            if fwd_date is None:
                complete = False
                break

            p0 = close_at(price_df, scan_date)
            p1 = close_at(price_df, fwd_date)
            if not p0 or p0 != p0 or not p1 or p1 != p1:
                complete = False
                break
            sector_ret = p1 / p0 - 1.0

            bench_ret = 0.0
            if bench_df is not None:
                bench_fwd = _forward_date(bench_df.index, scan_date, h)
                if bench_fwd is not None:
                    b0 = close_at(bench_df, scan_date)
                    b1 = close_at(bench_df, bench_fwd)
                    if b0 and b0 == b0 and b1 and b1 == b1:
                        bench_ret = b1 / b0 - 1.0

            excess[h] = sector_ret - bench_ret

        if complete and len(excess) == len(horizons):
            observations.append({
                "region": region,
                "sector": sector,
                "excess": excess,
            })

    return observations


def _aggregate_fwd_returns(
    observations: list[dict], region_label: str
) -> list[dict]:
    """Aggregate forward-return observations into per-horizon summary rows."""
    result: list[dict] = []
    for h in HORIZONS:
        label = HORIZON_LABELS[h]
        values = [obs["excess"][h] for obs in observations if h in obs.get("excess", {})]
        if not values:
            result.append({
                "region": region_label,
                "horizon": label,
                "obs": 0,
                "hit_rate": None,
                "mean_excess": None,
                "median_excess": None,
            })
        else:
            hits = sum(1 for v in values if v > 0)
            result.append({
                "region": region_label,
                "horizon": label,
                "obs": len(values),
                "hit_rate": round(hits / len(values), 3),
                "mean_excess": round(statistics.mean(values), 6),
                "median_excess": round(statistics.median(values), 6),
            })
    return result


def build_validation_context(shared: dict) -> dict:
    """Assemble forward-return and holding-period context for the sectors page."""
    all_scores_df = shared["all_scores_df"]
    universe = shared["universe"]
    project_root = shared["project_root"]

    if all_scores_df.empty:
        return {"validation_min_scans_met": False}

    scan_ids = sorted(all_scores_df["scan_id"].unique())
    if len(scan_ids) < MIN_SCANS:
        return {"validation_min_scans_met": False}

    ticker_map = _sector_ticker_map(universe)
    us_benchmark = universe.get("us_benchmark", "RSP")
    eu_benchmark = universe.get("eu_benchmark", "EXSA.DE")
    all_tickers = sorted(
        set(ticker_map.values()) | {us_benchmark, eu_benchmark}
    )

    scan_dates: dict[int, pd.Timestamp] = {}
    for sid in scan_ids:
        mask = all_scores_df["scan_id"] == sid
        run_at = pd.to_datetime(
            all_scores_df.loc[mask, "run_at"].iloc[0], utc=True
        )
        scan_dates[sid] = run_at.tz_localize(None).normalize()

    earliest = min(scan_dates.values()) - timedelta(days=10)
    latest = max(scan_dates.values()) + timedelta(days=30)
    prices = fetch_prices(
        all_tickers,
        start=earliest.strftime("%Y-%m-%d"),
        end=latest.strftime("%Y-%m-%d"),
        cache_dir=str(project_root / "data/cache"),
    )

    benchmark_prices = {
        t: prices[t] for t in [us_benchmark, eu_benchmark] if t in prices
    }

    all_fwd: list[dict] = []
    all_holding: list[dict] = []
    all_obs_combined: list[dict] = []

    for region, benchmark in [("US", us_benchmark), ("EU", eu_benchmark)]:
        obs = _compute_forward_returns(
            all_scores_df, prices, benchmark_prices,
            region, benchmark, ticker_map, HORIZONS,
        )
        all_fwd.extend(_aggregate_fwd_returns(obs, region))
        all_obs_combined.extend(obs)

        runs = _top5_runs(all_scores_df, region)
        all_holding.append(_holding_stats(runs, region))

    # "All" aggregates
    all_fwd.extend(_aggregate_fwd_returns(all_obs_combined, "All"))

    all_runs = _top5_runs(all_scores_df, "US") + _top5_runs(all_scores_df, "EU")
    all_holding.append(_holding_stats(all_runs, "All"))

    return {
        "validation_fwd_returns": all_fwd,
        "validation_holding": all_holding,
        "validation_min_scans_met": True,
    }
