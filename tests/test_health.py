"""Tests for dashboard/health.py badge logic."""
import pytest

from dashboard.health import _badge, build_health_context


class TestBadge:
    def test_coverage_green(self):
        assert _badge("coverage", 25, 25) == "green"

    def test_coverage_amber(self):
        assert _badge("coverage", 21, 25) == "amber"

    def test_coverage_red(self):
        assert _badge("coverage", 19, 25) == "red"

    def test_prices_green(self):
        assert _badge("prices", 0, None) == "green"

    def test_prices_amber(self):
        assert _badge("prices", 2, None) == "amber"

    def test_prices_red(self):
        assert _badge("prices", 3, None) == "red"

    def test_finbert_green(self):
        assert _badge("finbert", 11, 11) == "green"

    def test_finbert_amber(self):
        assert _badge("finbert", 6, 11) == "amber"

    def test_finbert_red(self):
        assert _badge("finbert", 4, 11) == "red"

    def test_finbert_none_scored(self):
        assert _badge("finbert", None, 11) is None

    def test_finbert_zero_denominator_is_red_never_none(self):
        """A 0 denominator means the scan tried and had nothing to score.
        Returning None would let _footer.html.j2's `badge-{{ ... or 'green' }}`
        fallback paint a total sentiment outage green in a collapsed panel —
        the same class of invisible failure that hid the 2026-08-05 FinBERT
        NameError for 10 days. A deliberate --no-finbert skip passes None as
        `value` and is caught above, so it never reaches this branch."""
        assert _badge("finbert", 0, 0) == "red"

    def test_finbert_zero_denominator_trips_the_panel_open(self):
        from dashboard.health import build_health_context

        ctx = build_health_context({
            "finbert_scored": 0, "finbert_total": 0, "gdelt_articles": 0,
            "sectors_produced": 18, "sectors_expected": 18, "prices_failed": 0,
        })
        assert ctx["health_badges"]["finbert"] == "red"
        assert ctx["health_any_warn"] is True

    def test_coverage_none(self):
        assert _badge("coverage", None, 25) is None


class TestBuildHealthContext:
    def test_returns_none_health_when_no_data(self):
        ctx = build_health_context(None)
        assert ctx["health"] is None
        assert ctx["health_any_warn"] is False

    def test_returns_badges_for_healthy_scan(self):
        health = {
            "run_at": "2026-07-20T06:00:00+00:00",
            "duration_s": 42.0,
            "prices_total": 27,
            "prices_cache": 20,
            "prices_stooq": 5,
            "prices_yfinance": 2,
            "prices_failed": 0,
            "sectors_expected": 25,
            "sectors_produced": 25,
            "finbert_scored": 11,
            "finbert_total": 11,
            "gdelt_articles": 847,
        }
        ctx = build_health_context(health)
        assert ctx["health"] is health
        assert ctx["health_badges"]["coverage"] == "green"
        assert ctx["health_badges"]["prices"] == "green"
        assert ctx["health_badges"]["finbert"] == "green"
        assert ctx["health_any_warn"] is False

    def test_warns_on_degraded_coverage(self):
        health = {
            "run_at": "2026-07-20T06:00:00+00:00",
            "duration_s": 42.0,
            "prices_total": 27,
            "prices_cache": 20,
            "prices_stooq": 5,
            "prices_yfinance": 2,
            "prices_failed": 3,
            "sectors_expected": 25,
            "sectors_produced": 22,
            "finbert_scored": 11,
            "finbert_total": 11,
            "gdelt_articles": 847,
        }
        ctx = build_health_context(health)
        assert ctx["health_badges"]["coverage"] == "amber"
        assert ctx["health_badges"]["prices"] == "red"
        assert ctx["health_any_warn"] is True

    def test_finbert_skipped(self):
        health = {
            "run_at": "2026-07-20T06:00:00+00:00",
            "duration_s": 42.0,
            "prices_total": 27,
            "prices_cache": 27,
            "prices_stooq": 0,
            "prices_yfinance": 0,
            "prices_failed": 0,
            "sectors_expected": 25,
            "sectors_produced": 25,
            "finbert_scored": None,
            "finbert_total": None,
            "gdelt_articles": None,
        }
        ctx = build_health_context(health)
        assert ctx["health_badges"]["finbert"] is None
        assert ctx["health_any_warn"] is False
