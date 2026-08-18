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
    if name == "max_dd_1y":
        return f"{v * 100:.1f}%"
    # return_*, above_*dma, acceleration — stored as decimal fraction
    return f"{v * 100:+.1f}%"


# A run of duplicate scans longer than this means the pipeline is stuck, not
# that the market was closed — a long weekend is 3. Past it, comparing against
# the last distinct scan would silently present week-old data as "yesterday",
# so callers fall back to showing no change at all, which is the honest answer.
MAX_DUPLICATE_RUN = 7

_TRAJECTORY_SCANS = 5


def _scan_fingerprint(scan_df, key_cols: list[str]) -> tuple:
    """A hashable snapshot of one scan's scores, for spotting duplicate scans.

    Covers rank *and* composite, not composite alone. Declaring two scans
    identical when they are not is the dangerous direction — it would skip a
    real observation — so the fingerprint is deliberately the wider one.

    NaN is mapped to a sentinel because NaN != NaN would make a scan differ
    from itself, defeating the check exactly when scores are missing.
    """
    cols = [c for c in ("rank", "composite") if c in scan_df.columns]
    sub = scan_df[key_cols + cols].copy()
    for c in cols:
        sub[c] = pd.to_numeric(sub[c], errors="coerce").round(10)
    sub = sub.sort_values(key_cols)

    def _clean(v):
        return "nan" if (v is None or (isinstance(v, float) and math.isnan(v))) else v

    return tuple(
        tuple(_clean(v) for v in row)
        for row in sub.itertuples(index=False, name=None)
    )


def distinct_scan_ids(
    history_df,
    key_cols: list[str] | None = None,
) -> list[int]:
    """Scan ids in ascending order with consecutive duplicates collapsed.

    The daily cron runs seven days a week against a five-day market, so a
    Saturday and Sunday scan are byte-identical replays of Friday's close —
    correctly so, nothing moved. Anything that reads "the previous scan" or
    "the last N scans" therefore sees repeats, not observations:

    - the rank delta compared against the previous `scan_id`, so it read `—`
      for every row on Saturday, Sunday *and* Monday (Monday's predecessor is
      Sunday, which is still Friday's data)
    - the Trend slope averaged over the last 5 scan ids, of which only 3 were
      distinct on 2026-08-09 — diluting the slope toward flat, in the column
      the guide tells the reader to trust for exits

    Market holidays produce the identical duplicate on a weekday, which is why
    this keys off the data rather than the calendar.

    The representative of a duplicate run is its **last** id, so the newest
    scan is always the one rendered.
    """
    if history_df is None or history_df.empty:
        return []
    keys = key_cols or ["region", "gics_sector"]
    if "scan_id" not in history_df.columns or any(c not in history_df.columns for c in keys):
        # Not enough to fingerprint — degrade to every scan rather than raise.
        return sorted(history_df["scan_id"].unique()) if "scan_id" in history_df else []

    out: list[int] = []
    prev_fp = None
    for sid in sorted(history_df["scan_id"].unique()):
        fp = _scan_fingerprint(history_df[history_df["scan_id"] == sid], keys)
        if prev_fp is not None and fp == prev_fp:
            out[-1] = sid          # same data — the run's representative moves forward
        else:
            out.append(sid)
        prev_fp = fp
    return out


# Word alongside each trend glyph, per the 2026-08-18 leaderboard redesign
# spec's exact wording. Kept as data (not embedded in _compute_rank_trajectories'
# state/label pairs) so build.py's enrichment loop and the JS mirror can both
# read the same source without duplicating the strings.
TRAJECTORY_WORDS = {
    "strong_up": "surging",
    "up": "rising",
    "flat": "flat",
    "down": "falling",
    "strong_down": "sliding",
}


def _compute_rank_trajectories(history_df) -> dict:
    """
    Compute rank slope over the last 5 *distinct* scans per sector.

    Returns dict keyed by "{region}|{gics_sector}" with:
        label: "↑↑" | "↑" | "→" | "↓" | "↓↓"
        state: "strong_up" | "up" | "flat" | "down" | "strong_down"
        slope: float (rank units per scan; negative = improving)
    """
    if history_df.empty:
        return {}

    df = history_df.copy()
    df["_sk"] = df["region"] + "|" + df["gics_sector"]

    # Distinct scans, not raw scan ids: a weekend contributes two byte-identical
    # replays of Friday, which would flatten the slope toward "→" rather than
    # leave it unchanged.
    scan_ids = distinct_scan_ids(df)
    recent_ids = set(scan_ids[-_TRAJECTORY_SCANS:])
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


def _compute_setup(row: dict, horizon=None) -> None:
    """Tag a leaderboard row with 'entry' or 'exit' setup, or None.

    This is the *position band* of the chosen horizon, not a momentum reading:

        Entry   rank <= top_n              inside the buy band
        Exit    rank >  top_n + buffer     left the hold band
        None    in between                 the hold zone, deliberately silent

    It replaces a trajectory + change-score heuristic that answered a different
    question ("is this accelerating?") and recomputed on every scan, so badges
    churned daily no matter what cadence the strategy was validated at. The band
    rule is the same one `strategy.simulate` uses, so the dashboard and the
    backtest finally describe one strategy.

    The Trend column still carries the trajectory reading — that is a descriptor
    and should keep describing.
    """
    from src.horizons import default_horizon

    h = horizon or default_horizon()
    rank = _safe_float(row.get("rank"))
    if rank is None:
        row["setup"] = None
    elif rank <= h.top_n:
        row["setup"] = "entry"
    elif rank > h.exit_rank:
        row["setup"] = "exit"
    else:
        row["setup"] = None

    # The highlighted rank badge. Unlike `setup` this is NOT gated — it says
    # where a rank sits, not what to do about it, and top_n is already public in
    # the page's HORIZONS. Baked here so the first paint is right for the default
    # horizon; applyHorizonBadges() rewrites it whenever the reader switches.
    # Mirrors Rescore.inBuyBand, which is the one rule the four client paths
    # share after this replaced their hardcoded `rank <= 3`.
    row["in_buy_band"] = rank is not None and rank <= h.top_n


# ---------------------------------------------------------------------------
# Shared row-building helper
# ---------------------------------------------------------------------------

# Composite cell rendering. Mirrors `compositeBar` in dashboard/assets/rescore.js
# — the signed-in upgrade and the scan-history view build the same markup in JS,
# so the two implementations must stay in step (guarded by
# test_composite_bar_python_and_js_agree).
COMPOSITE_FULL_SCALE = 1.6


def _signed_fmt(v: float) -> str:
    """2 decimals, explicit sign, U+2212 (minus sign) instead of ASCII hyphen —
    matches the redesign spec's number formatting for composite/level/change."""
    return f"{v:+.2f}".replace("-", "−")


def _composite_bar(value) -> str:
    """A centre-origin diverging bar plus the number.

    The composite is an average of z-scores: signed and centred on zero, so a
    left-filled bar would render -0.7 and +0.7 identically. The scale is fixed
    rather than per-scan, so bar lengths stay comparable between scans; values
    beyond it clamp. The value's ink follows its own sign (positive/negative/
    exactly zero), independent of the bar's fill side.
    """
    v = _safe_float(value)
    if v is None:
        return '<span class="cbar-wrap"></span><span class="cbar-val">—</span>'
    frac = min(abs(v) / COMPOSITE_FULL_SCALE, 1.0)
    pct = f"{frac * 50:.1f}"
    side = "left:50%" if v >= 0 else "right:50%"
    cls = "cbar pos" if v >= 0 else "cbar neg"
    if v > 0:
        val_cls = "cbar-val pos"
    elif v < 0:
        val_cls = "cbar-val neg"
    else:
        val_cls = "cbar-val"
    return (
        f'<span class="cbar-wrap">'
        f'<span class="{cls}" style="{side};width:{pct}%"></span>'
        f"</span>"
        f'<span class="{val_cls}">{_signed_fmt(v)}</span>'
    )


def _level_change_bars(level, change) -> str:
    """Two stacked centre-origin bars (Level, Change) in one cell — replaces
    the two separate bare-number columns. Same scale and colour rule as
    _composite_bar (COMPOSITE_FULL_SCALE), per the redesign spec."""
    def _row(label: str, value) -> str:
        v = _safe_float(value)
        if v is None:
            return (
                f'<div class="lc-row">'
                f'<span class="lc-label">{label}</span>'
                f'<span class="lc-track"></span>'
                f'<span class="lc-val">—</span>'
                f"</div>"
            )
        frac = min(abs(v) / COMPOSITE_FULL_SCALE, 1.0)
        pct = f"{frac * 50:.1f}"
        side = "left:50%" if v >= 0 else "right:50%"
        cls = "lc-bar pos" if v >= 0 else "lc-bar neg"
        return (
            f'<div class="lc-row">'
            f'<span class="lc-label">{label}</span>'
            f'<span class="lc-track">'
            f'<span class="{cls}" style="{side};width:{pct}%"></span>'
            f"</span>"
            f'<span class="lc-val">{_signed_fmt(v)}</span>'
            f"</div>"
        )

    return (
        '<div class="lc-cell">'
        + _row("LEVEL", level)
        + _row("CHANGE", change)
        + "</div>"
    )


def _build_rows_common(
    history_df,
    *,
    merge_key_cols: list[str],
    row_iter_fn,
) -> tuple[list[dict], str]:
    """
    Core merge/format logic used by the leaderboard row builder.

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

    # Compare against the last scan whose *data* differs, not simply the
    # previous scan_id. Weekend and holiday scans replay the prior close
    # unchanged, so `scan_ids[-2]` is routinely a duplicate of the scan being
    # rendered and every delta collapses to "—".
    distinct_ids = distinct_scan_ids(history_df, merge_key_cols)
    raw_ids = sorted(history_df["scan_id"].unique())
    duplicate_run = len(raw_ids) - len(distinct_ids)

    prev_id = distinct_ids[-2] if len(distinct_ids) >= 2 else None
    if prev_id is not None and duplicate_run > MAX_DUPLICATE_RUN:
        # The pipeline looks stuck rather than merely idle over a weekend.
        # Showing a delta here would present stale data as a fresh move.
        prev_id = None

    if prev_id is not None:
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
                "composite_bar": _composite_bar(composite),
                "level_score": _fv(row.get("level_score")),
                "change_score": _fv(row.get("change_score")),
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
