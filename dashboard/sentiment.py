"""Sentiment-specific data builders."""

from __future__ import annotations

import json
import math


def _build_sentiment_signal_rows(sent_df) -> list[dict]:
    """Pivot derived sentiment signals into one display row per sector-key.

    Each row: region, sector, and the derived metrics formatted for the
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

    def _fmt_seasonal(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.2f}x"

    rows = []
    for (region, sector), grp in sent_df.groupby(["region", "gics_sector"]):
        vals = dict(zip(grp["signal_name"], grp["value"]))
        # Parse rising_queries from text_value column
        rising = []
        if "text_value" in grp.columns:
            rq_rows = grp[grp["signal_name"] == "rising_queries"]
            if not rq_rows.empty:
                tv = rq_rows.iloc[0].get("text_value")
                if tv and isinstance(tv, str):
                    try:
                        rising = json.loads(tv)
                    except (json.JSONDecodeError, TypeError):
                        pass

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
            "seasonal_ratio": _fmt_seasonal(vals.get("seasonal_ratio")),
            "rising_queries": rising,
        })
    rows.sort(key=lambda r: r["_momentum"], reverse=True)
    return rows


def build_page_context(shared: dict) -> dict:
    """Assemble sentiment page context (both sector and theme cohorts)."""
    from dashboard.figures import _build_sentiment_scatter_figure

    return {
        "sentiment_scatter_json": _build_sentiment_scatter_figure(shared["history_df"]),
        "sentiment_signal_rows": _build_sentiment_signal_rows(shared["sentiment_signals_df"]),
        "theme_sentiment_scatter_json": _build_sentiment_scatter_figure(shared["theme_history_df"]),
        "theme_sentiment_signal_rows": _build_sentiment_signal_rows(shared["theme_sentiment_signals_df"]),
    }
