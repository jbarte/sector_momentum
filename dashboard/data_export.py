"""Assemble the machine-readable docs/data.json payload (pure, no I/O)."""
from __future__ import annotations

import math

import pandas as pd

SCHEMA_VERSION = 1


def _num(v):
    """Coerce to a JSON-safe float or None (never NaN, never a string)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _rank(row) -> int | None:
    r = row.get("rank")
    if isinstance(r, bool):
        return None
    if isinstance(r, int):
        return r
    if isinstance(r, float) and not math.isnan(r):
        return int(r)
    return None


def _raw_lookup(df: pd.DataFrame, key_cols: list[str]) -> dict:
    """Map tuple(key_cols) -> {composite, level, change, data, sentiment} raw floats."""
    out: dict = {}
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        key = tuple(r[c] for c in key_cols)
        out[key] = {
            "composite": _num(r.get("composite")),
            "level":     _num(r.get("level_score")),
            "change":    _num(r.get("change_score")),
            "data":      _num(r.get("data_score")),
            "sentiment": _num(r.get("sentiment_score")),
        }
    return out


def build_data_export(
    sector_rows: list[dict],
    theme_rows: list[dict],
    latest_scores_df: pd.DataFrame,
    theme_latest_df: pd.DataFrame,
    scan_id,
    scan_date: str,
    lagged: bool,
    generated_at: str,
) -> dict:
    """Build the docs/data.json dict from already-assembled rows + raw scores."""
    sec_raw = _raw_lookup(latest_scores_df, ["region", "gics_sector"])
    thm_raw = _raw_lookup(theme_latest_df, ["gics_sector"])

    def _entry(row, raw):
        return {
            "composite":  raw.get("composite", _num(row.get("_raw_composite"))),
            "level":      raw.get("level"),
            "change":     raw.get("change"),
            "data":       raw.get("data"),
            "sentiment":  raw.get("sentiment"),
            "rank":       _rank(row),
            "delta_rank": _num(row.get("delta_rank")),
            "trajectory": row.get("trajectory_state"),
            "setup":      row.get("setup"),
        }

    sectors = []
    for row in sector_rows:
        raw = sec_raw.get((row.get("region"), row.get("sector")), {})
        sectors.append({"region": row.get("region"), "sector": row.get("sector"),
                        **_entry(row, raw)})

    themes = []
    for row in theme_rows:
        raw = thm_raw.get((row.get("theme"),), {})
        themes.append({"theme": row.get("theme"), **_entry(row, raw)})

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scan_id": int(scan_id) if scan_id is not None else None,
        "scan_date": scan_date,
        "lagged": bool(lagged),
        "sectors": sectors,
        "themes": themes,
    }
