"""Tests for theme FinBERT sentiment — keyword GDELT queries + signal rows."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


def _themes_cfg():
    return {
        "benchmark": "ACWI",
        "themes": {
            "Cybersecurity": {
                "ticker": "CIBR",
                "gdelt_keywords": ["cybersecurity", "data breach"],
            },
            "Clean Energy": {
                "ticker": "ICLN",
                "gdelt_keywords": ["clean energy", "renewable energy"],
            },
            "NoKeywords": {
                "ticker": "NOPE",
            },
        },
    }


class TestBuildKeywordQuery:
    def test_single_keyword(self):
        from src.data.news_sentiment import _build_keyword_query

        q = _build_keyword_query(["cybersecurity"])
        assert q == '("cybersecurity") sourcelang:english'

    def test_multiple_keywords(self):
        from src.data.news_sentiment import _build_keyword_query

        q = _build_keyword_query(["cybersecurity", "data breach"])
        assert q == '("cybersecurity" OR "data breach") sourcelang:english'


class TestFetchThemeHeadlines:
    def _mock_response(self, articles):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"articles": articles}
        return resp

    @patch("src.data.news_sentiment.requests.get")
    def test_returns_headlines_per_theme(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Cyber attack hits firm"},
            {"title": "Data breach reported"},
        ])
        from src.data.news_sentiment import fetch_theme_headlines

        result = fetch_theme_headlines(_themes_cfg(), sleep_s=0)
        assert "Cybersecurity" in result
        assert "Clean Energy" in result
        assert result["Cybersecurity"] == ["Cyber attack hits firm", "Data breach reported"]

    @patch("src.data.news_sentiment.requests.get")
    def test_skips_themes_without_keywords(self, mock_get):
        mock_get.return_value = self._mock_response([{"title": "Headline"}])
        from src.data.news_sentiment import fetch_theme_headlines

        result = fetch_theme_headlines(_themes_cfg(), sleep_s=0)
        assert "NoKeywords" not in result

    @patch("src.data.news_sentiment.requests.get")
    def test_uses_keyword_query_format(self, mock_get):
        mock_get.return_value = self._mock_response([])
        from src.data.news_sentiment import fetch_theme_headlines

        fetch_theme_headlines(_themes_cfg(), sleep_s=0)
        first_call = mock_get.call_args_list[0]
        query = first_call.kwargs.get("params", first_call[1].get("params", {}))["query"]
        assert '"cybersecurity"' in query or '"clean energy"' in query
        assert "sourcelang:english" in query

    @patch("src.data.news_sentiment.requests.get")
    def test_deduplicates_titles(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Same headline"},
            {"title": "Same headline"},
            {"title": "Different"},
        ])
        from src.data.news_sentiment import fetch_theme_headlines

        result = fetch_theme_headlines(
            {"benchmark": "ACWI", "themes": {
                "Test": {"ticker": "TST", "gdelt_keywords": ["test"]},
            }},
            sleep_s=0,
        )
        assert result["Test"] == ["Same headline", "Different"]

    @patch("src.data.news_sentiment.requests.get")
    def test_429_retry(self, mock_get):
        err = MagicMock()
        err.status_code = 429
        err.raise_for_status.side_effect = Exception("429")
        ok = self._mock_response([{"title": "Recovered"}])
        mock_get.side_effect = [err, ok]
        from src.data.news_sentiment import fetch_theme_headlines

        result = fetch_theme_headlines(
            {"benchmark": "ACWI", "themes": {
                "Test": {"ticker": "TST", "gdelt_keywords": ["test"]},
            }},
            sleep_s=0, max_retries=2,
        )
        assert result["Test"] == ["Recovered"]

    @patch("src.data.news_sentiment.requests.get")
    def test_hardened_defaults(self, mock_get):
        import inspect
        from src.data.news_sentiment import fetch_theme_headlines

        sig = inspect.signature(fetch_theme_headlines)
        assert sig.parameters["sleep_s"].default == 20.0
        assert sig.parameters["max_retries"].default == 4


class TestBuildThemeNewsSignalRows:
    def test_produces_four_signals_per_theme(self):
        from src.data.news_sentiment import build_theme_news_signal_rows

        scores = {
            "Cybersecurity": {
                "mean_polarity": 0.3, "count": 20,
                "positive_pct": 0.6, "negative_pct": 0.2,
            },
        }
        rows = build_theme_news_signal_rows(scores)
        assert len(rows) == 4
        names = {r["signal_name"] for r in rows}
        assert names == {"news_polarity", "news_count", "news_positive_pct", "news_negative_pct"}
        assert all(r["theme"] == "Cybersecurity" for r in rows)
        assert all("text_value" in r for r in rows)

    def test_multiple_themes(self):
        from src.data.news_sentiment import build_theme_news_signal_rows

        scores = {
            "Cybersecurity": {
                "mean_polarity": 0.3, "count": 20,
                "positive_pct": 0.6, "negative_pct": 0.2,
            },
            "Clean Energy": {
                "mean_polarity": -0.1, "count": 15,
                "positive_pct": 0.3, "negative_pct": 0.5,
            },
        }
        rows = build_theme_news_signal_rows(scores)
        assert len(rows) == 8
        themes = {r["theme"] for r in rows}
        assert themes == {"Cybersecurity", "Clean Energy"}

    def test_empty_input(self):
        from src.data.news_sentiment import build_theme_news_signal_rows

        rows = build_theme_news_signal_rows({})
        assert rows == []
