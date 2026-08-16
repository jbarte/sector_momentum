"""Tests for theme FinBERT sentiment — keyword GDELT queries + signal rows."""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

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


class TestSentimentSignalsFrameShape:
    """The frame `_compute_finbert_sentiment` hands to `save_scan` must be
    keyed the way `save_scan` reads it: region / gics_sector.

    `build_theme_news_signal_rows` emits rows keyed by `theme`, but scan.py
    seeds the accumulator frame with `region`/`gics_sector` columns and
    concatenates the theme-keyed rows straight into it. That yields a frame
    carrying BOTH `gics_sector` (all-NaN, from the seed) and `theme`, so
    `save_scan`'s `_rows_from_df(key_cols=["region","gics_sector",...])`
    reads NULLs into two NOT NULL columns.

    Latent since 1ff80d8 (2026-08-05) and never observed in production only
    because the FinBERT NameError fired first and skipped this code entirely.
    """

    def _fake_args(self):
        return SimpleNamespace(no_finbert=False)

    def _wide_df(self):
        return pd.DataFrame(
            {"x": [1.0, 2.0]},
            index=["THEME|Cybersecurity", "THEME|Clean Energy"],
        )

    def _patched_scan(self):
        """Patch the news_sentiment functions scan.py imports at call time."""
        scores = {
            "Cybersecurity": {"mean_polarity": 0.20, "count": 30,
                              "positive_pct": 0.5, "negative_pct": 0.2},
            "Clean Energy": {"mean_polarity": -0.10, "count": 25,
                             "positive_pct": 0.3, "negative_pct": 0.4},
        }
        return (
            patch("src.data.news_sentiment.fetch_headlines",
                  return_value={"Cybersecurity": ["a"] * 30,
                                "Clean Energy": ["b"] * 25}),
            patch("src.data.news_sentiment.score_headlines",
                  return_value=scores),
            patch("src.data.news_sentiment.zscore_polarity",
                  return_value={"Cybersecurity": 1.0, "Clean Energy": -1.0}),
        )

    def _run(self):
        import scan
        p1, p2, p3 = self._patched_scan()
        with p1, p2, p3:
            return scan._compute_finbert_sentiment(
                self._wide_df(), _themes_cfg(), self._fake_args()
            )

    def test_frame_has_no_duplicate_columns(self):
        _score, sent_df, _health = self._run()
        assert not sent_df.empty, "no sentiment rows produced"
        dupes = [c for c in set(sent_df.columns)
                 if list(sent_df.columns).count(c) > 1]
        assert not dupes, f"duplicate columns in sentiment frame: {dupes}"

    def test_frame_is_keyed_by_region_and_gics_sector(self):
        """save_scan reads these two columns; both are NOT NULL in the schema."""
        _score, sent_df, _health = self._run()
        for col in ("region", "gics_sector", "signal_name", "value"):
            assert col in sent_df.columns, f"missing column {col!r}"
        assert sent_df["region"].notna().all(), "region has NULLs"
        assert sent_df["gics_sector"].notna().all(), "gics_sector has NULLs"
        assert set(sent_df["region"]) == {"THEME"}
        assert set(sent_df["gics_sector"]) == {"Cybersecurity", "Clean Energy"}

    def test_rows_reaching_the_insert_are_scalars_not_series(self):
        """The concrete production failure mode: with a duplicate
        `gics_sector`, `_rows_from_df` hands psycopg2 a pandas Series instead
        of a string, which it cannot adapt."""
        from src.state import _rows_from_df

        _score, sent_df, _health = self._run()
        rows = _rows_from_df(
            sent_df, 999,
            key_cols=["region", "gics_sector", "signal_name"],
            float_cols=["value"],
            raw_cols=["text_value"],
        )
        assert rows, "no rows built"
        for row in rows:
            for value in row:
                assert not isinstance(value, pd.Series), (
                    f"psycopg2 cannot adapt a Series — got {value!r} in {row!r}"
                )
            assert isinstance(row[1], str), f"region not a str: {row[1]!r}"
            assert isinstance(row[2], str), f"gics_sector not a str: {row[2]!r}"


class TestFinbertFailureIsVisible:
    """A FinBERT failure must not look like a deliberate skip.

    Both paths used to leave all three health metrics None, and
    `_footer.html.j2` renders that as a muted "Skipped" with no badge and no
    warning dot. That is why the 2026-08-05 NameError survived 10 scans
    unnoticed: the dashboard reported the outage as a user preference.
    """

    def _run(self, side_effect):
        import scan
        with patch("src.data.news_sentiment.fetch_headlines",
                   side_effect=side_effect):
            return scan._compute_finbert_sentiment(
                pd.DataFrame({"x": [1.0]}, index=["THEME|Cybersecurity"]),
                _themes_cfg(),
                SimpleNamespace(no_finbert=False),
            )

    def test_failure_records_zero_of_n_not_none(self):
        _score, _df, health = self._run(RuntimeError("boom"))
        assert health["finbert_scored"] == 0, "failure left the metric None"
        # _themes_cfg() has two themes with gdelt_keywords, one without.
        assert health["finbert_total"] == 2
        assert health["gdelt_articles"] == 0

    def test_failure_shows_a_red_badge_and_opens_the_panel(self):
        """The whole point: the footer must visibly flag it."""
        from dashboard.health import build_health_context

        _score, _df, health = self._run(RuntimeError("boom"))
        ctx = build_health_context({
            "finbert_scored": health["finbert_scored"],
            "finbert_total": health["finbert_total"],
            "gdelt_articles": health["gdelt_articles"],
            "sectors_produced": 18, "sectors_expected": 18, "prices_failed": 0,
        })
        assert ctx["health_badges"]["finbert"] == "red"
        assert ctx["health_any_warn"] is True, "health panel would stay collapsed"

    def test_deliberate_skip_still_reads_as_skipped(self):
        """--no-finbert must stay distinguishable from a failure: all-None,
        which the footer renders as 'Skipped' with no badge."""
        import scan
        from dashboard.health import build_health_context

        _score, _df, health = scan._compute_finbert_sentiment(
            pd.DataFrame({"x": [1.0]}, index=["THEME|Cybersecurity"]),
            _themes_cfg(),
            SimpleNamespace(no_finbert=True),
        )
        assert health["finbert_scored"] is None
        ctx = build_health_context({
            "finbert_scored": None, "finbert_total": None, "gdelt_articles": None,
            "sectors_produced": 18, "sectors_expected": 18, "prices_failed": 0,
        })
        assert ctx["health_badges"]["finbert"] is None
        assert ctx["health_any_warn"] is False

    def test_failure_after_a_good_gdelt_fetch_reports_the_real_article_count(self):
        """The actual 2026-08-05 shape: GDELT returns 1522 headlines, FinBERT
        then dies. Reporting `0 GDELT articles` would blame a healthy source
        for a downstream bug."""
        import scan

        with patch("src.data.news_sentiment.fetch_headlines",
                   return_value={"Cybersecurity": ["h"] * 30,
                                 "Clean Energy": ["h"] * 25}), \
             patch("src.data.news_sentiment.score_headlines",
                   side_effect=NameError("name '_finbert_pipeline' is not defined")):
            _score, _df, health = scan._compute_finbert_sentiment(
                pd.DataFrame({"x": [1.0]}, index=["THEME|Cybersecurity"]),
                _themes_cfg(),
                SimpleNamespace(no_finbert=False),
            )

        assert health["finbert_scored"] == 0
        assert health["gdelt_articles"] == 55, (
            "GDELT's real article count was discarded — the footer would blame "
            "GDELT for a FinBERT failure"
        )

    def test_zero_queryable_themes_still_flags_red_not_green(self):
        """A 0 denominator makes _badge() return None, and the footer's
        `or 'green'` fallback then paints a total outage green in a collapsed
        panel. Fall back to the full theme count so the ratio stays 0/N."""
        import scan
        from dashboard.health import build_health_context

        cfg = {"themes": {"NoKeywords": {"ticker": "NOPE"},
                          "AlsoNone": {"ticker": "NIX"}}}
        with patch("src.data.news_sentiment.fetch_headlines",
                   side_effect=RuntimeError("boom")):
            _score, _df, health = scan._compute_finbert_sentiment(
                pd.DataFrame({"x": [1.0]}, index=["THEME|NoKeywords"]),
                cfg, SimpleNamespace(no_finbert=False),
            )

        assert health["finbert_total"] == 2, "denominator collapsed to 0"
        ctx = build_health_context({
            "finbert_scored": health["finbert_scored"],
            "finbert_total": health["finbert_total"],
            "gdelt_articles": health["gdelt_articles"],
            "sectors_produced": 18, "sectors_expected": 18, "prices_failed": 0,
        })
        assert ctx["health_badges"]["finbert"] == "red"
        assert ctx["health_any_warn"] is True


class TestFetchHeadlinesOrchestration:
    """Bulk first, API only for what bulk under-serves.

    The point of the whole change: the API is a rate-limited last resort, not
    the default path. These tests pin the request count, because "we stopped
    hammering the API" is the property that matters and it is invisible in
    the returned data.
    """

    def _cfg(self):
        return {
            "themes": {
                "Semiconductors": {"ticker": "SOXX", "gdelt_keywords": ["chip"]},
                "Clean Energy": {"ticker": "ICLN", "gdelt_keywords": ["solar"]},
            }
        }

    def test_no_api_call_when_bulk_covers_every_theme(self):
        from src.data.news_sentiment import fetch_headlines, MIN_ARTICLES

        bulk = {
            "Semiconductors": [f"chip story {i}" for i in range(MIN_ARTICLES)],
            "Clean Energy": [f"solar story {i}" for i in range(MIN_ARTICLES)],
        }
        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk", return_value=bulk), \
             patch("src.data.news_sentiment.fetch_theme_headlines") as api:
            out = fetch_headlines(self._cfg())

        api.assert_not_called()
        assert len(out["Semiconductors"]) == MIN_ARTICLES

    def test_api_is_called_only_for_themes_below_the_floor(self):
        from src.data.news_sentiment import fetch_headlines, MIN_ARTICLES

        bulk = {
            "Semiconductors": [f"chip story {i}" for i in range(MIN_ARTICLES)],
            "Clean Energy": ["solar story"],          # 1 < MIN_ARTICLES
        }
        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk", return_value=bulk), \
             patch("src.data.news_sentiment.fetch_theme_headlines",
                   return_value={"Clean Energy": ["a", "b", "c", "d", "e", "f"]}) as api:
            out = fetch_headlines(self._cfg())

        api.assert_called_once()
        requested = api.call_args.args[0]["themes"]
        assert set(requested) == {"Clean Energy"}, "API asked for a covered theme"
        assert len(out["Clean Energy"]) == 6
        assert len(out["Semiconductors"]) == MIN_ARTICLES

    def test_api_result_is_kept_only_when_it_beats_bulk(self):
        """A throttled API returning less than bulk must not overwrite bulk."""
        from src.data.news_sentiment import fetch_headlines

        bulk = {"Semiconductors": ["a", "b", "c"], "Clean Energy": []}
        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk", return_value=bulk), \
             patch("src.data.news_sentiment.fetch_theme_headlines",
                   return_value={"Semiconductors": ["z"], "Clean Energy": []}):
            out = fetch_headlines(self._cfg())

        assert out["Semiconductors"] == ["a", "b", "c"]

    def test_bulk_failure_falls_back_to_the_api_for_everything(self):
        from src.data.news_sentiment import fetch_headlines

        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk",
                   side_effect=RuntimeError("gdelt file host down")), \
             patch("src.data.news_sentiment.fetch_theme_headlines",
                   return_value={"Semiconductors": ["a"], "Clean Energy": ["b"]}) as api:
            out = fetch_headlines(self._cfg())

        api.assert_called_once()
        assert set(api.call_args.args[0]["themes"]) == {"Semiconductors", "Clean Energy"}
        assert out["Semiconductors"] == ["a"]

    def test_total_failure_of_both_paths_returns_empty_lists_not_an_exception(self):
        from src.data.news_sentiment import fetch_headlines

        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk",
                   side_effect=RuntimeError("down")), \
             patch("src.data.news_sentiment.fetch_theme_headlines",
                   side_effect=RuntimeError("also down")):
            out = fetch_headlines(self._cfg())

        assert out == {"Semiconductors": [], "Clean Energy": []}

    def test_output_feeds_score_headlines_unchanged(self):
        """Contract check: the orchestrator's shape is what FinBERT consumes."""
        from src.data.news_sentiment import fetch_headlines, score_headlines, MIN_ARTICLES

        bulk = {
            "Semiconductors": [f"chip {i}" for i in range(MIN_ARTICLES + 1)],
            "Clean Energy": [f"solar {i}" for i in range(MIN_ARTICLES + 1)],
        }
        with patch("src.data.gdelt_gkg.fetch_theme_headlines_bulk", return_value=bulk), \
             patch("src.data.news_sentiment.fetch_theme_headlines", return_value={}), \
             patch("src.data.news_sentiment._load_finbert_pipeline") as loader:
            pipe = MagicMock()
            pipe.side_effect = lambda texts, **kw: [
                {"label": "positive", "score": 0.8} for _ in texts
            ]
            loader.return_value = pipe
            scored = score_headlines(fetch_headlines(self._cfg()))

        assert scored["Semiconductors"]["count"] == MIN_ARTICLES + 1
        assert scored["Semiconductors"]["mean_polarity"] == pytest.approx(0.8)
