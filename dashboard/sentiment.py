"""Sentiment-specific data builders."""

from __future__ import annotations

import math


def _build_sentiment_signal_rows(sent_df) -> list[dict]:
    """Pivot derived sentiment signals into one display row per sector-key.

    Each row: region, sector, and the six derived metrics formatted for the
    template. Sorted by momentum descending so the leaders sit on top. Returns
    [] when no sentiment_signals rows exist (older scans / dry runs).
    """
    if sent_df is None or sent_df.empty:
        return []

    def _fmt(v, pct=False):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v * 100:.0f}%" if pct else f"{v:+.2f}"

    def _fmt_attn(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.1f}"

    rows = []
    for (region, sector), grp in sent_df.groupby(["region", "gics_sector"]):
        vals = dict(zip(grp["signal_name"], grp["value"]))
        rows.append({
            "region": region,
            "sector": sector,
            "_momentum": vals.get("momentum") or 0.0,
            "momentum": _fmt(vals.get("momentum")),
            "acceleration": _fmt(vals.get("acceleration")),
            "range_position": _fmt(vals.get("range_position"), pct=True),
            "spike": _fmt(vals.get("spike")),
            "volatility": _fmt(vals.get("volatility"), pct=True),
            "attention": _fmt_attn(vals.get("attention_level")),
        })
    rows.sort(key=lambda r: r["_momentum"], reverse=True)
    return rows
