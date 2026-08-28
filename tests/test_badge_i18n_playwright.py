"""Runtime regression test for the badge-suffix / Swedish i18n interaction bug.

Task 2 ("Self-describing badge suffixes") added a " · <date>" / " · no slot"
suffix to entry/exit badges in applyHorizonBadges() (index.html.j2). It self-
disclosed a Critical bug: _i18n.html.j2's apply() -- the function every
language toggle (and every re-render, since applyHorizonBadges() itself calls
window.applyLang() at its own tail) runs -- rewrote a [data-i18n] element's
textContent from SV[key] alone on the Swedish branch, discarding whatever
suffix applyHorizonBadges() had just written a few lines earlier in the same
call. English readers saw the suffix; Swedish readers never did.

None of Task 2's existing tests (in tests/test_dashboard_js.py) would have
caught this: they only assert that identifiers/strings are *referenced* in
source text, never that Swedish output actually contains the suffix. This
test drives a real rendered page in headless Chromium (same pattern as
tests/test_dashboard_render_coalescing.py) and reads the live DOM instead.

See sector_momentum-notes' plan for review-cadence-and-book-lock, Task 2,
"Fix round 1" -- and .superpowers/sdd/2026-08-27-review-cadence-and-book-lock/
task-2-report.md for the original trace.
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import _build_leaderboard_rows, _render
from dashboard.figures import _build_scan_history_data
from src.cohorts import Cohort
from src.horizons import (horizons as _horizons, default_horizon as _default_horizon,
                           round_trip_bps as _round_trip_bps, review_dates as _review_dates)

from tests.test_dashboard_js import _grouped_rows_for, _make_mock_plotly_json, _TEMPLATE

_ASSETS_DIR = Path(__file__).parent.parent / "dashboard" / "assets"
_REQUIRED_ASSETS = ["rescore.js", "theme.js", "scan-history.js", "scan-digest.js"]


def _horizon_ctx_far_future(dumps=json.dumps):
    """Like test_dashboard_js._horizon_ctx, but every preset's review_dates
    are generated from a `since` far in the future (2099) instead of a fixed
    2026 date.

    This test needs an entry badge that carries the "next review date" suffix,
    which currentReviewStatus() only adds when today is NOT itself a review
    day (reviewDue === false). review_dates all sitting ahead of "since" makes
    that true deterministically, however far in the future the real calendar
    has drifted by the time this test actually runs -- unlike a fixed 2026
    date, which a 2026-01-15-anchored test would eventually run past.
    """
    hs = _horizons()
    d = _default_horizon()
    return dict(
        horizon_list=hs,
        round_trip_bps=_round_trip_bps(),
        horizons_json=dumps([
            {"key": h.key, "label": h.label, "rebalance": h.rebalance,
             "top_n": h.top_n, "buffer": h.buffer,
             "exit_rank": h.exit_rank,
             "trades_per_year": h.trades_per_year,
             "median_holding_days": h.median_holding_days,
             "review_dates": _review_dates(h, since="2099-01-01")} for h in hs
        ]),
        horizon_default_json=dumps({
            "key": d.key, "label": d.label, "top_n": d.top_n, "buffer": d.buffer}),
        unbuyable_json=dumps([]),
        theme_tickers_json=dumps({}),
        chart_dark_json=dumps({}),
    )


def _render_badge_suffix_html(tmp_path):
    """Render a real dashboard page with an ungated leaderboard (badges_gated
    =False, so no sign-in is needed) and one row ranked #1 -- inside every
    configured horizon's top_n, so window.Rescore.badgeFor() classifies it
    'entry' with holdings 'unknown' (the plain-band path, positions.js is not
    loaded). Combined with the far-future review calendar above, this row's
    badge gets the " · <next review date>" suffix on every render.
    """
    df = pd.DataFrame([
        dict(scan_id=1, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Robotics", level_score=0.7, change_score=0.4,
             data_score=0.55, sentiment_score=0.2, composite=0.55, rank=1.0),
        dict(scan_id=1, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Semiconductors", level_score=-0.3, change_score=-0.1,
             data_score=-0.2, sentiment_score=0.0, composite=-0.2, rank=2.0),
    ])
    lb_rows, scan_date = _build_leaderboard_rows(df)

    for r in lb_rows:
        key = f"{r['region']}|{r['sector']}"
        r["key"] = key
        r["sector_id"] = key.replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "->"
        r["trajectory_state"] = "flat"
        r["setup"] = "entry" if r["rank"] == 1.0 else None
        r["breakdown_html"] = "<div>breakdown</div>"

    grouped_rows = _grouped_rows_for(lb_rows)
    scan_history = _build_scan_history_data(df)
    scan_index = [
        {"scan_id": 1, "run_at_display": "2026-08-24 06:00 UTC",
         "run_at_raw": "2026-08-24T06:00:00", "sector_count": 2,
         "top_sector": "Robotics", "top_region": "THEME"},
    ]

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        us_leaderboard_rows=[], eu_leaderboard_rows=[],
        cohort_list=[c for c, _ in grouped_rows], grouped_rows=grouped_rows,
        has_any_rows=any(rs for _, rs in grouped_rows),
        cohorts_json=json.dumps([{"region": "THEME", "label": "Themes"}]),
        **_horizon_ctx_far_future(), cohort_charts_json=json.dumps({}),
        sentiment_scatter_json=_make_mock_plotly_json(),
        rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=json.dumps(scan_history),
        scan_index=scan_index, active_scan_id=1,
        signals_list=[], plotly_bundle="assets/plotly.min.js",
        backtest_json=json.dumps({}), backtest_metrics=[], has_backtest=False,
        rotation_json=json.dumps([]), has_rotations=False,
        badges_gated=False,
    ))

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    for name in _REQUIRED_ASSETS:
        src = _ASSETS_DIR / name
        if src.exists():
            shutil.copy2(src, assets_dir / name)

    return out


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment-dependent
            pytest.skip(f"Chromium not available (run `playwright install "
                        f"chromium`): {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, tmp_path):
    html_path = _render_badge_suffix_html(tmp_path)
    pg = browser.new_page()
    pg.goto(html_path.as_uri())
    yield pg
    pg.close()


def _entry_badge_text(page):
    return page.evaluate(
        "() => { var b = document.querySelector('.setup-badge.entry'); "
        "return b ? b.textContent : null; }"
    )


def _entry_badge_suffix_attr(page):
    return page.evaluate(
        "() => { var b = document.querySelector('.setup-badge.entry'); "
        "return b ? b.getAttribute('data-suffix') : null; }"
    )


def test_entry_badge_carries_date_suffix_in_english(page):
    """Sanity/regression-guard: the English path must still show the suffix
    after the fix, so a Swedish-path fix can't silently regress English."""
    page.evaluate("() => localStorage.setItem('lang', 'en')")
    page.evaluate("() => window.applyHorizonBadges()")
    text = _entry_badge_text(page)
    suffix = _entry_badge_suffix_attr(page)
    assert suffix, "expected the row ranked #1 to render an entry badge with a suffix"
    assert text is not None and text.endswith(suffix), (
        f"English badge text {text!r} does not end with its own data-suffix {suffix!r}"
    )
    assert text.startswith("▲ Enter"), f"unexpected English badge text: {text!r}"


def test_entry_badge_carries_suffix_in_swedish(page):
    """The actual regression: toggling to Swedish must NOT discard the
    suffix. Before the fix, _i18n.html.j2's apply() took SV[key] verbatim on
    the Swedish branch and ignored data-en (and therefore the suffix) --
    Swedish readers never saw it. This drives the real code path
    (applyHorizonBadges() -> window.applyLang('sv') at its own tail, exactly
    like a real horizon switch or positions change would) rather than
    hand-setting attributes."""
    page.evaluate("() => localStorage.setItem('lang', 'sv')")
    page.evaluate("() => window.applyHorizonBadges()")
    text = _entry_badge_text(page)
    suffix = _entry_badge_suffix_attr(page)
    assert suffix, "expected the row ranked #1 to render an entry badge with a suffix"
    assert text is not None and text.endswith(suffix), (
        f"Swedish badge text {text!r} does not end with its own data-suffix {suffix!r} "
        f"-- the Swedish [data-i18n] branch is discarding the suffix again"
    )
    assert text.startswith("▲ Gå in"), f"unexpected Swedish badge text: {text!r}"


def test_toggle_lang_after_render_preserves_suffix(page):
    """A second real-world path into the same bug: the badge renders once
    (English, at page load), then the reader toggles language via
    window.toggleLang() -- exactly the lang-toggle button's own handler --
    without applyHorizonBadges() running again. The suffix must survive."""
    en_text = _entry_badge_text(page)
    suffix = _entry_badge_suffix_attr(page)
    assert suffix, "expected the row ranked #1 to render an entry badge with a suffix"
    assert en_text is not None and en_text.endswith(suffix)

    page.evaluate("() => window.toggleLang()")
    sv_text = _entry_badge_text(page)
    assert sv_text is not None and sv_text.endswith(suffix), (
        f"badge text after toggleLang() ({sv_text!r}) lost its suffix {suffix!r}"
    )
    assert sv_text.startswith("▲ Gå in"), f"unexpected text after toggle: {sv_text!r}"
