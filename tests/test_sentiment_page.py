"""Sentiment page context builder — empty-state detection."""
import pandas as pd

from dashboard.sentiment import build_page_context


def _history(sentiment_vals):
    """Single latest scan (id=2) with one row per given sentiment value."""
    rows = []
    for i, v in enumerate(sentiment_vals):
        rows.append({
            "scan_id": 2,
            "run_at": "2026-07-20T00:00:00Z",
            "region": "US",
            "gics_sector": f"Sector{i}",
            "data_score": 0.1 * i,
            "sentiment_score": v,
            "composite": 0.1,
            "rank": i + 1,
        })
    return pd.DataFrame(rows)


def test_sentiment_available_false_when_all_null():
    shared = {"history_df": _history([None, None, float("nan")]),
              "sentiment_signals_df": pd.DataFrame()}
    assert build_page_context(shared)["sentiment_available"] is False


def test_sentiment_available_false_when_all_zero():
    shared = {"history_df": _history([0.0, 0.0]),
              "sentiment_signals_df": pd.DataFrame()}
    assert build_page_context(shared)["sentiment_available"] is False


def test_sentiment_available_true_when_any_nonzero():
    shared = {"history_df": _history([0.0, None, 0.7]),
              "sentiment_signals_df": pd.DataFrame()}
    assert build_page_context(shared)["sentiment_available"] is True


def test_sentiment_available_false_when_history_empty():
    shared = {"history_df": pd.DataFrame(), "sentiment_signals_df": pd.DataFrame()}
    assert build_page_context(shared)["sentiment_available"] is False


def test_scatter_renders_the_theme_cohort():
    """The Sentiment page used to scope itself to the sector cohorts, because
    history_df spanned every cohort while the page's FinBERT table did not.
    Both sides now read the THEME cohort, so every row in history_df belongs
    on the scatter — theme sentiment was always computed, just never shown.
    """
    import json

    rows = [
        {"scan_id": 2, "run_at": "2026-07-20T00:00:00Z", "region": "THEME",
         "gics_sector": f"Theme{i}", "data_score": 0.1 * i,
         "sentiment_score": 0.5, "composite": 0.1, "rank": i + 1}
        for i in range(4)
    ]
    shared = {"history_df": pd.DataFrame(rows), "sentiment_signals_df": pd.DataFrame()}

    ctx = build_page_context(shared)
    scatter = json.loads(ctx["sentiment_scatter_json"])
    total_points = sum(len(trace.get("x", [])) for trace in scatter["data"])

    assert total_points == 4, "theme rows missing from the sentiment scatter"


def test_scatter_falls_back_open_when_universe_missing():
    """No 'universe' in shared -> don't crash, don't filter (fail-open,
    matching the rest of the codebase's config-missing behavior)."""
    shared = {"history_df": _history([0.5, 0.5]), "sentiment_signals_df": pd.DataFrame()}
    ctx = build_page_context(shared)
    assert ctx["sentiment_available"] is True
