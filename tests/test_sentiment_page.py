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
