"""Tests for src/data/news_sentiment.py — GDELT fetch + FinBERT scoring."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


class TestGdeltSectorThemes:
    """GDELT_SECTOR_THEMES constant must cover all 11 GICS sectors."""

    def test_all_sectors_mapped(self):
        from src.data.news_sentiment import GDELT_SECTOR_THEMES

        expected = {
            "Energy", "Materials", "Industrials", "Consumer Discretionary",
            "Consumer Staples", "Health Care", "Financials", "Technology",
            "Communication Services", "Utilities", "Real Estate",
        }
        assert set(GDELT_SECTOR_THEMES.keys()) == expected

    def test_each_sector_has_themes(self):
        from src.data.news_sentiment import GDELT_SECTOR_THEMES

        for sector, themes in GDELT_SECTOR_THEMES.items():
            assert len(themes) >= 1, f"{sector} has no theme codes"
            for t in themes:
                assert isinstance(t, str) and len(t) > 0


class TestFetchNewsHeadlines:
    """Tests for fetch_news_headlines — GDELT API interaction."""

    def _mock_response(self, articles):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"articles": articles}
        return resp

    @patch("src.data.news_sentiment.requests.get")
    def test_returns_headlines_per_sector(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Oil prices surge", "seendate": "20260717T120000Z",
             "domain": "reuters.com", "sourcecountry": "United States"},
            {"title": "Gas exports rise", "seendate": "20260717T110000Z",
             "domain": "bbc.co.uk", "sourcecountry": "United Kingdom"},
        ])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert "Energy" in result
        assert result["Energy"] == ["Oil prices surge", "Gas exports rise"]

    @patch("src.data.news_sentiment.requests.get")
    def test_empty_response(self, mock_get):
        mock_get.return_value = self._mock_response([])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_no_articles_key(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        mock_get.return_value = resp
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_http_error_retries(self, mock_get):
        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = Exception("429 Too Many Requests")
        ok_resp = self._mock_response([{"title": "Recovery", "seendate": "20260717T120000Z",
                                        "domain": "cnn.com", "sourcecountry": "United States"}])
        mock_get.side_effect = [error_resp, ok_resp]
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0, max_retries=2)
        assert result["Energy"] == ["Recovery"]

    @patch("src.data.news_sentiment.requests.get")
    def test_all_retries_exhausted(self, mock_get):
        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = Exception("429")
        mock_get.return_value = error_resp
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0, max_retries=2)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_multiple_sectors(self, mock_get):
        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            if "ENV_OIL" in query:
                return self._mock_response([
                    {"title": "Oil up", "seendate": "20260717T120000Z",
                     "domain": "a.com", "sourcecountry": "US"},
                ])
            return self._mock_response([
                {"title": "Banks rally", "seendate": "20260717T120000Z",
                 "domain": "b.com", "sourcecountry": "US"},
            ])
        mock_get.side_effect = side_effect
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy", "Financials"], sleep_s=0)
        assert "Energy" in result and "Financials" in result

    @patch("src.data.news_sentiment.requests.get")
    def test_deduplicates_titles(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Same headline", "seendate": "20260717T120000Z",
             "domain": "a.com", "sourcecountry": "US"},
            {"title": "Same headline", "seendate": "20260717T110000Z",
             "domain": "b.com", "sourcecountry": "US"},
            {"title": "Different", "seendate": "20260717T100000Z",
             "domain": "c.com", "sourcecountry": "US"},
        ])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == ["Same headline", "Different"]


class TestScoreHeadlines:
    """Tests for score_headlines — FinBERT inference + aggregation."""

    def _mock_pipeline_output(self, headlines):
        """Simulate FinBERT pipeline output."""
        results = []
        for h in headlines:
            if "surge" in h.lower() or "rally" in h.lower() or "up" in h.lower():
                results.append({"label": "positive", "score": 0.85})
            elif "crash" in h.lower() or "fall" in h.lower() or "down" in h.lower():
                results.append({"label": "negative", "score": 0.90})
            else:
                results.append({"label": "neutral", "score": 0.70})
        return results

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_basic_scoring(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: self._mock_pipeline_output(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({
            "Energy": ["Oil prices surge", "Gas exports up", "Energy news today",
                        "Prices fall hard", "Market update", "Sector news"],
        })
        assert "Energy" in result
        s = result["Energy"]
        assert s["count"] == 6
        assert isinstance(s["mean_polarity"], float)
        assert 0.0 <= s["positive_pct"] <= 1.0
        assert 0.0 <= s["negative_pct"] <= 1.0
        neutral_pct = 1 - s["positive_pct"] - s["negative_pct"]
        assert 0.0 <= neutral_pct <= 1.0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_below_min_articles(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: self._mock_pipeline_output(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines, MIN_ARTICLES

        result = score_headlines({"Energy": ["One headline"] * (MIN_ARTICLES - 1)})
        assert math.isnan(result["Energy"]["mean_polarity"])
        assert result["Energy"]["count"] == MIN_ARTICLES - 1

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_empty_headlines(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: []
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": []})
        assert math.isnan(result["Energy"]["mean_polarity"])
        assert result["Energy"]["count"] == 0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_polarity_sign(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "positive", "score": 0.9}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": ["good"] * 10})
        assert result["Energy"]["mean_polarity"] > 0
        assert result["Energy"]["positive_pct"] == 1.0
        assert result["Energy"]["negative_pct"] == 0.0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_negative_polarity(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "negative", "score": 0.8}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": ["bad"] * 10})
        assert result["Energy"]["mean_polarity"] < 0
        assert result["Energy"]["negative_pct"] == 1.0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_multiple_sectors(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "positive", "score": 0.9}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({
            "Energy": ["good"] * 10,
            "Financials": ["great"] * 10,
        })
        assert "Energy" in result and "Financials" in result


class TestZscorePolarity:
    """Tests for zscore_polarity — cross-sectional z-score of mean_polarity."""

    def test_basic_zscore(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": -0.3, "count": 40, "positive_pct": 0.2, "negative_pct": 0.6},
            "Technology": {"mean_polarity": 0.1, "count": 60, "positive_pct": 0.5, "negative_pct": 0.3},
        }
        result = zscore_polarity(scores)
        assert len(result) == 3
        assert result["Energy"] > result["Technology"] > result["Financials"]
        total = sum(result.values())
        assert abs(total) < 1e-10

    def test_nan_excluded(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": float("nan"), "count": 2, "positive_pct": 0.0, "negative_pct": 0.0},
            "Technology": {"mean_polarity": -0.5, "count": 60, "positive_pct": 0.2, "negative_pct": 0.7},
        }
        result = zscore_polarity(scores)
        assert not math.isnan(result["Energy"])
        assert math.isnan(result["Financials"])
        assert not math.isnan(result["Technology"])

    def test_single_valid_sector(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": float("nan"), "count": 0, "positive_pct": 0.0, "negative_pct": 0.0},
        }
        result = zscore_polarity(scores)
        assert result["Energy"] == 0.0

    def test_all_same_polarity(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            s: {"mean_polarity": 0.3, "count": 50, "positive_pct": 0.6, "negative_pct": 0.2}
            for s in ["Energy", "Financials", "Technology"]
        }
        result = zscore_polarity(scores)
        for v in result.values():
            assert v == 0.0


# ---------------------------------------------------------------------------
# Parent-map application (EU sub-sector split)
# ---------------------------------------------------------------------------

import pandas as pd

from src.data.news_sentiment import apply_polarity_to_keys, build_news_signal_rows

_PMAP = {"Banks": "Financials", "Insurance": "Financials", "Chemicals": "Materials"}


def test_apply_polarity_maps_subsector_to_parent_score():
    idx = ["US|Financials", "EU|Banks", "EU|Insurance", "EU|Technology"]
    base = pd.Series(0.0, index=idx)
    z = {"Financials": 1.5, "Technology": -0.5}
    out = apply_polarity_to_keys(base, z, _PMAP)
    assert out["US|Financials"] == 1.5
    assert out["EU|Banks"] == 1.5          # inherited from parent Financials
    assert out["EU|Insurance"] == 1.5
    assert out["EU|Technology"] == -0.5    # identity fallback
    assert base["EU|Banks"] == 0.0         # input not mutated


def test_apply_polarity_skips_nan_and_unscored():
    base = pd.Series(0.0, index=["EU|Banks", "EU|Chemicals"])
    z = {"Financials": float("nan")}       # Materials absent entirely
    out = apply_polarity_to_keys(base, z, _PMAP)
    assert out["EU|Banks"] == 0.0
    assert out["EU|Chemicals"] == 0.0


def test_build_news_signal_rows_emits_universe_names():
    universe = {
        "us_sectors": {"Financials": "XLF"},
        "eu_sectors": {"Banks": "EXV1.DE", "Technology": "EXV3.DE"},
    }
    scores = {
        "Financials": {"mean_polarity": 0.2, "count": 10,
                       "positive_pct": 60.0, "negative_pct": 20.0},
    }
    rows = build_news_signal_rows(scores, universe, _PMAP)
    keys = {(r["region"], r["gics_sector"]) for r in rows}
    # Financials scored: US|Financials (identity) and EU|Banks (via parent) emit;
    # EU|Technology's parent (Technology) wasn't scored -> no rows.
    assert keys == {("US", "Financials"), ("EU", "Banks")}
    banks = {r["signal_name"]: r["value"] for r in rows if r["gics_sector"] == "Banks"}
    assert banks == {"news_polarity": 0.2, "news_count": 10.0,
                     "news_positive_pct": 60.0, "news_negative_pct": 20.0}
