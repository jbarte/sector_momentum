"""Tests for src/data/news_sentiment.py — GDELT fetch + FinBERT scoring."""

from __future__ import annotations

import contextlib
import math
import sys
import types
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



@contextlib.contextmanager
def _stub_transformers(pipeline_factory):
    """Install a fake `transformers` module exposing `pipeline`.

    `_load_finbert_pipeline` does `from transformers import pipeline` inside
    the function body, so a stub in sys.modules is picked up at call time.
    Stubbing the module (rather than `@patch("transformers.pipeline")`) keeps
    these tests runnable whether or not the real 400MB dependency is
    installed — it is in CI, it usually is not on a dev machine — while still
    executing our own loader code.
    """
    import src.data.news_sentiment as ns

    fake = types.ModuleType("transformers")
    fake.pipeline = pipeline_factory
    saved = sys.modules.get("transformers")
    sys.modules["transformers"] = fake

    # Force a cold load WITHOUT creating the module global as a side effect.
    # Assigning `ns._finbert_pipeline = None` unconditionally here would
    # define the very global whose absence is the bug under test, so a broken
    # module would silently pass. Reset it only when it genuinely exists.
    _ABSENT = object()
    saved_cache = getattr(ns, "_finbert_pipeline", _ABSENT)
    if saved_cache is not _ABSENT:
        ns._finbert_pipeline = None
    try:
        yield fake
    finally:
        if saved is None:
            sys.modules.pop("transformers", None)
        else:
            sys.modules["transformers"] = saved
        if saved_cache is _ABSENT:
            if hasattr(ns, "_finbert_pipeline"):
                del ns._finbert_pipeline
        else:
            ns._finbert_pipeline = saved_cache


class TestFinbertPipelineLoadsForReal:
    """The pipeline loader itself, exercised rather than mocked away.

    Every other test in this file patches `_load_finbert_pipeline` — which is
    exactly why this feature could be dead in production for 10 days with a
    green suite. On 2026-08-05 the sector-cohort retirement (1ff80d8) deleted
    the module-level `_finbert_pipeline = None` while leaving the function's
    `global _finbert_pipeline` / `if _finbert_pipeline is None:` in place, so
    every real call raised `NameError: name '_finbert_pipeline' is not
    defined`. scan.py's broad `except Exception` swallowed it into a WARNING
    and wrote NULL sentiment for every scan from 153 onward.

    These tests stub the EXTERNAL boundary (the `transformers` module) so our
    own loader code actually runs: mock what you don't own, run what you do.
    """

    def test_module_defines_the_pipeline_cache_global(self):
        """`global _finbert_pipeline` makes the name a module global; reading
        it before assignment is a NameError. It must exist at module scope."""
        import src.data.news_sentiment as ns

        assert hasattr(ns, "_finbert_pipeline"), (
            "src.data.news_sentiment must define `_finbert_pipeline` at module "
            "level — _load_finbert_pipeline() reads it before assigning, so a "
            "missing global raises NameError on every real call"
        )

    def test_load_finbert_pipeline_runs_without_nameerror(self):
        """The regression itself: calling the real loader must not raise."""
        from src.data.news_sentiment import _load_finbert_pipeline

        sentinel = MagicMock(name="finbert-pipeline")
        calls = []

        def factory(*args, **kwargs):
            calls.append((args, kwargs))
            return sentinel

        with _stub_transformers(factory):
            result = _load_finbert_pipeline()

        assert result is sentinel
        assert len(calls) == 1
        assert calls[0][1].get("model") == "ProsusAI/finbert"

    def test_pipeline_is_memoised_across_calls(self):
        """The second call must reuse the cached object rather than reload the
        model — that caching is the entire reason the global exists."""
        from src.data.news_sentiment import _load_finbert_pipeline

        calls = []

        def factory(*args, **kwargs):
            calls.append(1)
            return MagicMock(name="finbert-pipeline")

        with _stub_transformers(factory):
            first = _load_finbert_pipeline()
            second = _load_finbert_pipeline()

        assert first is second
        assert len(calls) == 1, (
            "model reloaded on the second call — the memo global is not working"
        )

    def test_score_headlines_end_to_end_through_the_real_loader(self):
        """score_headlines -> _load_finbert_pipeline -> transformers.pipeline,
        with only the external library stubbed. This is the exact path that
        was broken in production; the TestScoreHeadlines cases above cannot
        catch it because they replace the loader itself."""
        from src.data.news_sentiment import score_headlines, MIN_ARTICLES

        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [
            {"label": "positive", "score": 0.9} for _ in texts
        ]

        with _stub_transformers(lambda *a, **kw: pipe):
            result = score_headlines({"Space": ["headline"] * (MIN_ARTICLES + 1)})

        assert result["Space"]["count"] == MIN_ARTICLES + 1
        assert result["Space"]["mean_polarity"] == pytest.approx(0.9)
