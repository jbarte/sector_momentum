"""Assemble the machine-readable docs/data.json payload (pure, no I/O)."""
from __future__ import annotations

import math

import pandas as pd

# 2 since 2026-09-19: adds the optional "config" block (build_config_block).
# Additive -- every v1 key is unchanged.
SCHEMA_VERSION = 2


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


def build_config_block(themes_cfg, cohort_list, horizon_list, default_horizon,
                       review_since: str) -> dict:
    """Config for clients that derive the board themselves -- the iOS app.

    CONFIG ONLY: horizon presets, cohort regions and universe metadata, all
    already public in config/weights.yaml and config/themes.yaml. Nothing here
    comes from a scan. The Enter/Exit badge stays a signed-in tier
    (tests/test_badge_gating.py): a client computes it from v_recent_scores
    after signing in, with the presets published here.

    `cohorts` lets a client filter v_recent_scores the way the web table does
    through window.COHORTS -- that view has no region filter, and retired
    sector rows are still in it.
    """
    from src.cohorts import instrument_map
    from src.horizons import review_dates
    from src.universe import is_unbuyable

    ucits = (themes_cfg or {}).get("ucits") or {}
    fields = ("ticker", "name", "isin", "ter", "issuer", "match", "url")
    universe = []
    for key, ticker in instrument_map(cohort_list).items():
        region, name = key.split("|", 1)
        universe.append({
            "region": region,
            "theme": name,
            "ticker": ticker,
            "unbuyable": is_unbuyable(region, name, themes_cfg),
            # A list, mirroring config/themes.yaml; empty when no UCITS
            # equivalent exists (Shipping).
            "ucits": [{f: e.get(f) for f in fields}
                      for e in (ucits.get(name) or []) if isinstance(e, dict)],
        })
    return {
        "default_horizon": default_horizon.key,
        "cohorts": [c.region for c in cohort_list],
        "horizons": [
            {"key": h.key, "label": h.label, "rebalance": h.rebalance,
             "top_n": h.top_n, "buffer_frac": h.buffer_frac,
             "review_dates": review_dates(h, since=review_since)}
            for h in horizon_list
        ],
        "universe": universe,
    }


def build_data_export(
    theme_rows: list[dict],
    theme_latest_df: pd.DataFrame,
    scan_id,
    scan_date: str,
    lagged: bool,
    generated_at: str,
    config: dict | None = None,
) -> dict:
    """Build the docs/data.json dict from already-assembled rows + raw scores.

    `config` (from build_config_block) is attached verbatim when given.
    """
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

    themes = []
    for row in theme_rows:
        raw = thm_raw.get((row.get("theme"),), {})
        themes.append({"theme": row.get("theme"), **_entry(row, raw)})

    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scan_id": int(scan_id) if scan_id is not None else None,
        "scan_date": scan_date,
        "lagged": bool(lagged),
        "themes": themes,
    }
    if config is not None:
        out["config"] = config
    return out
