"""Leaderboard row builders for sector and theme dashboards."""

from __future__ import annotations

import math

import pandas as pd


def _safe_float(v) -> float | None:
    """Return float or None for NaN/None values."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _format_raw_value(name: str, value) -> str:
    """Format a signal's raw value for human display."""
    v = _safe_float(value)
    if v is None:
        return "—"
    if name in ("rs_ratio", "rs_momentum"):
        return f"{v:.1f}"
    if name == "breadth_above_50dma":
        return f"{v * 100:.0f}%"
    if name in ("ma50_slope", "obv_slope"):
        return f"{v:+.3f}"
    # return_*, above_*dma, acceleration — stored as decimal fraction
    return f"{v * 100:+.1f}%"


def _compute_rank_trajectories(history_df) -> dict:
    """
    Compute rank slope over last 5 scans per sector.

    Returns dict keyed by "{region}|{gics_sector}" with:
        label: "↑↑" | "↑" | "→" | "↓" | "↓↓"
        state: "strong_up" | "up" | "flat" | "down" | "strong_down"
        slope: float (rank units per scan; negative = improving)
    """
    if history_df.empty:
        return {}

    df = history_df.copy()
    df["_sk"] = df["region"] + "|" + df["gics_sector"]

    scan_ids = sorted(df["scan_id"].unique())
    recent_ids = set(scan_ids[-5:])
    recent = df[df["scan_id"].isin(recent_ids)]

    result = {}
    for sk in df["_sk"].unique():
        ranks = (
            recent[recent["_sk"] == sk]
            .sort_values("scan_id")["rank"]
            .dropna()
            .tolist()
        )
        n = len(ranks)
        if n < 2:
            result[sk] = {"label": "→", "state": "flat", "slope": 0.0}
            continue

        # Pure-Python OLS slope (no numpy needed)
        x_mean = (n - 1) / 2.0
        y_mean = sum(ranks) / n
        num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ranks))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = round(num / den, 3) if den else 0.0

        if slope <= -1.5:
            state, label = "strong_up", "↑↑"
        elif slope <= -0.3:
            state, label = "up", "↑"
        elif slope < 0.3:
            state, label = "flat", "→"
        elif slope < 1.5:
            state, label = "down", "↓"
        else:
            state, label = "strong_down", "↓↓"

        result[sk] = {"label": label, "state": state, "slope": slope}

    return result


def _compute_setup(row: dict) -> None:
    """Tag a leaderboard row with 'entry' or 'exit' setup, or None."""
    comp = row.get("_raw_composite")
    change = row.get("_raw_change")
    traj = row.get("trajectory_state", "flat")
    if (comp is not None and comp > 0
            and traj in ("up", "strong_up")
            and change is not None and change > 0):
        row["setup"] = "entry"
    elif traj in ("down", "strong_down") and change is not None and change < 0:
        row["setup"] = "exit"
    else:
        row["setup"] = None


# ---------------------------------------------------------------------------
# Shared row-building helper
# ---------------------------------------------------------------------------

def _build_rows_common(
    history_df,
    *,
    merge_key_cols: list[str],
    row_iter_fn,
) -> tuple[list[dict], str]:
    """
    Core merge/format logic shared by sector and theme leaderboard builders.

    Parameters
    ----------
    history_df : DataFrame with scan history (must have scan_id, run_at, rank,
        composite, and the columns listed in *merge_key_cols*).
    merge_key_cols : columns to merge current and previous scan on
        (e.g. ["region", "gics_sector"]).
    row_iter_fn : callable(latest_df) -> Iterable[dict]
        Receives the enriched latest-scan DataFrame and yields one raw row dict
        per leaderboard row.  Each dict must already contain the row-specific
        fields; this helper adds delta_rank / arrow / arrow_class.

    Returns (rows, scan_date_str).
    """
    if history_df.empty:
        return [], "N/A"

    latest_scan_id = history_df["scan_id"].max()
    latest = history_df[history_df["scan_id"] == latest_scan_id].copy()

    scan_date = pd.to_datetime(latest["run_at"].iloc[0]).strftime("%Y-%m-%d %H:%M UTC")

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) >= 2:
        prev_id = scan_ids[-2]
        prev = history_df[history_df["scan_id"] == prev_id][
            merge_key_cols + ["rank", "composite"]
        ].rename(columns={"rank": "rank_prev", "composite": "comp_prev"})
        latest = latest.merge(prev, on=merge_key_cols, how="left")
        latest["delta_rank"] = (latest["rank_prev"] - latest["rank"]).fillna(0)
        latest["delta_composite"] = (latest["composite"] - latest["comp_prev"]).fillna(0)
    else:
        latest["delta_rank"] = 0.0
        latest["rank_prev"] = latest["rank"]
        latest["delta_composite"] = 0.0

    latest = latest.sort_values("rank", ascending=True)

    rows = list(row_iter_fn(latest))

    for row in rows:
        delta = _safe_float(row.get("delta_rank", 0)) or 0.0
        row["delta_rank"] = f"{delta:+.1f}" if delta != 0 else "—"
        row["arrow"] = "▲" if delta > 0 else ("▼" if delta < 0 else "")
        row["arrow_class"] = "up" if delta > 0 else ("down" if delta < 0 else "")

    return rows, scan_date


# ---------------------------------------------------------------------------
# Sector leaderboard
# ---------------------------------------------------------------------------

def _build_leaderboard_rows(history_df) -> tuple[list[dict], str]:
    """
    Return leaderboard rows from the most recent scan and the scan date string.
    """
    def _fv(v):
        f = _safe_float(v)
        return f"{f:.3f}" if f is not None else "—"

    def _iter(latest):
        for _, row in latest.iterrows():
            composite = _safe_float(row.get("composite"))
            rank = _safe_float(row.get("rank"))
            yield {
                "rank": int(rank) if rank is not None else "—",
                "sector": row["gics_sector"],
                "region": row["region"],
                "composite": f"{composite:.3f}" if composite is not None else "—",
                "level_score": _fv(row.get("level_score")),
                "change_score": _fv(row.get("change_score")),
                "data_score": _fv(row.get("data_score")),
                "sentiment_score": _fv(row.get("sentiment_score")),
                "delta_rank": _safe_float(row.get("delta_rank", 0)) or 0.0,
                "_raw_composite": composite,
                "_raw_change": _safe_float(row.get("change_score")),
            }

    return _build_rows_common(
        history_df,
        merge_key_cols=["region", "gics_sector"],
        row_iter_fn=_iter,
    )


# ---------------------------------------------------------------------------
# Theme leaderboard
# ---------------------------------------------------------------------------

def _build_theme_leaderboard_rows(
    history_df,
    signals_df,
    themes_cfg: dict,
    weights: dict,
    trajectories: dict,
) -> list[dict]:
    """Themes leaderboard rows with build-time deltas + trajectory, sorted by rank."""
    from .breakdown import _build_breakdown_html

    if history_df is None or history_df.empty:
        return []

    def _fv(v):
        f = _safe_float(v)
        return f"{f:.3f}" if f is not None else "—"

    def _iter(latest):
        for _, s in latest.iterrows():
            theme = s["gics_sector"]
            key = f"THEME|{theme}"
            row_signals = (
                signals_df[signals_df["theme"] == theme].to_dict("records")
                if signals_df is not None and not signals_df.empty else []
            )
            breakdown = _build_breakdown_html(
                key, s.to_dict(), row_signals, universe={}, weights=weights,
                sector_etfs=None, themes_cfg=themes_cfg,
            )
            traj = trajectories.get(key, {"label": "→", "state": "flat"})
            rank = _safe_float(s.get("rank"))
            yield {
                "rank": int(rank) if rank is not None else "—",
                "theme": theme,
                "sector_id": key.replace("|", "-").replace(" ", "_"),
                "composite": _fv(s["composite"]),
                "level_score": _fv(s["level_score"]),
                "change_score": _fv(s["change_score"]),
                "data_score": _fv(s["data_score"]),
                "delta_rank": _safe_float(s.get("delta_rank", 0)) or 0.0,
                "trajectory_label": traj["label"],
                "trajectory_state": traj["state"],
                "_raw_composite": _safe_float(s.get("composite")),
                "_raw_change": _safe_float(s.get("change_score")),
                "breakdown_html": breakdown,
            }

    rows, _ = _build_rows_common(
        history_df,
        merge_key_cols=["region", "gics_sector"],
        row_iter_fn=_iter,
    )

    for row in rows:
        _compute_setup(row)

    return rows
