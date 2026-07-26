"""Sentiment-specific data builders (FinBERT news sentiment only)."""

from __future__ import annotations

import math


def _build_sentiment_signal_rows(sent_df) -> list[dict]:
    """One display row per sector-key with FinBERT news columns.

    Returns [] when no sentiment_signals rows exist (older scans / dry runs).
    """
    if sent_df is None or sent_df.empty:
        return []

    def _fmt(v, pct=False):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v * 100:.0f}%" if pct else f"{v:+.2f}"

    rows = []
    for (region, sector), grp in sent_df.groupby(["region", "gics_sector"]):
        vals = dict(zip(grp["signal_name"], grp["value"]))
        news_count = vals.get("news_count")
        has_count = news_count is not None and not (
            isinstance(news_count, float) and math.isnan(news_count)
        )
        rows.append({
            "region": region,
            "sector": sector,
            "_polarity": vals.get("news_polarity") or 0.0,
            "news_polarity": _fmt(vals.get("news_polarity")),
            "news_count": str(int(news_count)) if has_count else "—",
            "news_positive_pct": _fmt(vals.get("news_positive_pct"), pct=True),
            "news_negative_pct": _fmt(vals.get("news_negative_pct"), pct=True),
        })
    rows.sort(key=lambda r: r["_polarity"], reverse=True)
    return rows


def _latest_has_sentiment(history_df) -> bool:
    """True iff the latest scan has at least one real (non-null, non-zero)
    sentiment_score. Mirrors the scatter's own solid/faded split so the page
    only hides the chart when it would be an all-hollow flat line."""
    if history_df is None or history_df.empty or "sentiment_score" not in history_df:
        return False
    latest_id = history_df["scan_id"].max()
    s = history_df[history_df["scan_id"] == latest_id]["sentiment_score"]
    return bool((s.notna() & (s != 0.0)).any())


def build_page_context(shared: dict) -> dict:
    """Assemble sentiment page context (sectors only; FinBERT)."""
    from dashboard.figures import _build_sentiment_scatter_figure

    return {
        "sentiment_scatter_json": _build_sentiment_scatter_figure(shared["history_df"]),
        "sentiment_signal_rows": _build_sentiment_signal_rows(shared["sentiment_signals_df"]),
        "sentiment_available": _latest_has_sentiment(shared["history_df"]),
    }
