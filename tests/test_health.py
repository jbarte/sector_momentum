"""Tests for dashboard/health.py badge logic."""
from pathlib import Path

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


class TestFooterNeverGuessesHealthy:
    """`_badge()` returning None means "cannot judge this metric".

    The footer used to render that as `badge-green`, asserting health the data
    does not support — a 0/0 sentiment outage showed a green tick in a
    collapsed panel. It now falls back to a neutral `badge-unknown`. Guarded
    for all three metrics, not just the one that bit: the fallback is shared
    infrastructure and the next metric added inherits it.
    """

    _TPL = Path(__file__).parent.parent / "dashboard" / "templates"

    def test_no_metric_falls_back_to_green(self):
        footer = (self._TPL / "_footer.html.j2").read_text()
        assert "or 'green'" not in footer, (
            "a health badge still defaults to green when _badge() cannot "
            "judge the metric — that asserts health the data does not support"
        )

    @pytest.mark.parametrize("metric", ["prices", "coverage", "finbert"])
    def test_each_metric_falls_back_to_unknown(self, metric):
        footer = (self._TPL / "_footer.html.j2").read_text()
        assert f"badge-{{{{ health_badges.{metric} or 'unknown' }}}}" in footer

    def test_the_unknown_class_is_actually_styled(self):
        """An unstyled class silently renders as inherited body text — the
        badge would lose its weight and read as ordinary prose."""
        css = (self._TPL / "css" / "_health.css.j2").read_text()
        assert ".badge-unknown" in css, "badge-unknown has no CSS rule"


class TestPricesAsofDisplay:
    """The health panel's Prices row now shows when the snapshot was
    actually scored (2026-08-23) -- persisted from align_cohort_asof's
    stats_out (see src/state.py, scan.py). Real Jinja renders, not source
    scans: the guard has to handle None correctly (old scan rows predate
    these two columns), which a substring check on the template source
    can't verify.
    """

    _TPL = Path(__file__).parent.parent / "dashboard" / "templates"

    def _render(self, health, health_badges=None, health_any_warn=False):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(self._TPL)))
        return env.get_template("_footer.html.j2").render(
            health=health,
            health_badges=health_badges or {},
            health_any_warn=health_any_warn,
        )

    def _base_health(self, **overrides):
        h = {
            "run_at": "2026-08-09T06:00:00+00:00",
            "duration_s": 12.0,
            "prices_yfinance": 18, "prices_cache": 0, "prices_failed": 0,
            "sectors_produced": 18, "sectors_expected": 18,
            "finbert_scored": None, "finbert_total": None, "gdelt_articles": None,
            "prices_asof": None, "asof_spread_days": None,
        }
        h.update(overrides)
        return h

    def test_shows_the_asof_date_when_present(self):
        html = self._render(self._base_health(prices_asof="2026-08-06"))
        assert "2026-08-06" in html

    def test_shows_the_spread_when_nonzero(self):
        html = self._render(self._base_health(
            prices_asof="2026-08-06", asof_spread_days=2,
        ))
        assert "2" in html.split("Prices</span>", 1)[1].split("</div>", 1)[0]

    def test_renders_without_crashing_on_old_scan_rows_missing_asof(self):
        """Old scan rows predate `prices_asof`/`asof_spread_days` -- both
        None, not absent keys, since get_latest_health's own NaN->None
        conversion runs over the full health-columns list regardless of scan
        age. Must render clean, not `as of None`."""
        html = self._render(self._base_health())  # both None
        prices_row = html.split('class="health-label">Prices', 1)[1].split("</div>", 1)[0]
        assert "None" not in prices_row, (
            f"the Prices row renders the literal word 'None': {prices_row!r}"
        )


class TestHealthPanelGating:
    """The lag-gating block re-caps every other per-scan data source
    (all_scores_df, history_df, rrg_df, signals_df, sentiment_signals_df) to
    the lagged scan a guest actually sees. health_row was never touched by
    it -- get_latest_health(conn) ran once, unconditionally, before the gate
    -- so a guest's health panel showed the TRUE latest scan's run_at (and,
    once prices_asof/asof_spread_days were added, the true price as-of date)
    regardless of the 7-day lag. Found in code review, 2026-08-23; same class
    of leak the sentiment_signals_df re-fetch two lines below already exists
    to prevent, with an explicit comment saying so.

    Source-scan, not a real build.py run: build.py's main body is a
    procedural script with a live DB connection, not a unit-testable
    function -- the same reason no other part of this gating block (the
    sentiment_signals_df re-fetch it mirrors) has a behavioural test either.
    """

    _BUILD_PY = Path(__file__).parent.parent / "dashboard" / "build.py"

    def _gating_block(self) -> str:
        """The `if lag_active and lb_scan_id is not None:` block, bounded by
        INDENTATION rather than a fixed marker string. A first attempt used
        "next line that doesn't start with whitespace" as the end -- but
        this `if` sits inside a function, so almost every line for the rest
        of that function is indented, and the match ran all the way to a
        `print()` call near the very end of the file. Tracking the `if`
        line's own indentation and stopping at the first non-blank line at
        or below it is what actually bounds a Python block."""
        lines = self._BUILD_PY.read_text().splitlines()
        start = next(
            i for i, l in enumerate(lines)
            if l.strip() == "if lag_active and lb_scan_id is not None:"
        )
        if_indent = len(lines[start]) - len(lines[start].lstrip())
        end = len(lines)
        for i in range(start + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped:
                continue
            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent <= if_indent:
                end = i
                break
        return "\n".join(lines[start:end])

    def test_get_health_for_scan_is_imported(self):
        """Scoped to the `from src.state import (...)` block specifically —
        a substring check against the WHOLE file would still pass with the
        import removed, because the function is also called later in the
        gating block; confirmed by sabotage."""
        text = self._BUILD_PY.read_text()
        block = text.split("from src.state import (", 1)[1].split(")", 1)[0]
        assert "get_health_for_scan" in block, (
            "dashboard/build.py never imports get_health_for_scan — the "
            "gated build has no way to ask for a specific scan's health"
        )

    def test_health_row_is_recapped_inside_the_gating_block(self):
        block = self._gating_block()
        assert "health_row" in block and "get_health_for_scan(conn, lb_scan_id)" in block, (
            "health_row is not re-fetched for the lagged scan inside the "
            "gating block — a guest's health panel would still show the "
            "true latest scan's data regardless of the 7-day lag"
        )
