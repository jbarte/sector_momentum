"""Runtime regression test for the entry-badge date suffix and the review
panel's initial English render.

Task 2 ("Self-describing badge suffixes") added a " · <date>" / " · no slot"
suffix to entry/exit badges in applyHorizonBadges() (index.html.j2). None of
Task 2's existing tests (in tests/test_dashboard_js.py) would catch a
regression here: they only assert that identifiers/strings are *referenced*
in source text, never that the rendered page actually contains the suffix.
This test drives a real rendered page in headless Chromium (same pattern as
tests/test_dashboard_render_coalescing.py) and reads the live DOM instead.

See notes/plans/ (review-cadence-and-book-lock), Task 2,
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
_REQUIRED_ASSETS = ["rescore.js", "motion.js", "theme.js", "scan-history.js"]


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
             "top_n": h.top_n, "buffer_frac": h.buffer_frac,
             # No longer read from Horizon (removed 2026-08-31) -- see the
             # matching comment in tests/test_dashboard_js.py's _horizon_ctx.
             "trades_per_year": None,
             "median_holding_days": None,
             "review_dates": _review_dates(h, since="2099-01-01")} for h in hs
        ]),
        horizon_default_json=dumps({
            "key": d.key, "label": d.label, "top_n": d.top_n, "buffer_frac": d.buffer_frac}),
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
        r["theme_id"] = key.replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "->"
        r["trajectory_state"] = "flat"
        r["setup"] = "entry" if r["rank"] == 1.0 else None
        r["breakdown_html"] = "<div>breakdown</div>"

    grouped_rows = _grouped_rows_for(lb_rows)
    scan_history = _build_scan_history_data(df)
    scan_index = [
        {"scan_id": 1, "run_at_display": "2026-08-24 06:00 UTC",
         "run_at_raw": "2026-08-24T06:00:00", "theme_count": 2,
         "top_theme": "Robotics", "top_region": "THEME"},
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
    """Regression guard for the suffix added by Task 2: the row ranked #1
    must render an entry badge whose text ends with its own data-suffix."""
    page.evaluate("() => window.applyHorizonBadges()")
    text = _entry_badge_text(page)
    suffix = _entry_badge_suffix_attr(page)
    assert suffix, "expected the row ranked #1 to render an entry badge with a suffix"
    assert text is not None and text.endswith(suffix), (
        f"badge text {text!r} does not end with its own data-suffix {suffix!r}"
    )
    assert text.startswith("▲ Enter"), f"unexpected badge text: {text!r}"


# ---------------------------------------------------------------------------
# The review panel's initial render.
# ---------------------------------------------------------------------------

def _rp_headline_text(page):
    return page.evaluate(
        "() => { var h = document.getElementById('rp-headline'); "
        "return h ? h.textContent : null; }"
    )


def _rp_note_text(page):
    return page.evaluate(
        "() => { var n = document.querySelector('#rp-actions .rp-note'); "
        "return n ? n.textContent : null; }"
    )


def _set_fake_book_state(page):
    """A minimal truthy window.SM_BOOK_STATE, set directly rather than
    simulating a full signed-in Supabase session -- renderReviewPanel() only
    ever READS this global (applyHorizonBadges() is what normally writes it),
    so setting it directly exercises the exact same render path with far
    less fixture machinery. Combined with the page's far-future review
    calendar (status.due === false), this puts renderReviewPanel() on its
    "no action" branch, which has real dynamic text (the headline's
    date-qualified "Next review" and the "No action until then." note) --
    unlike the guest (book === null) branch, which shows only the date."""
    page.evaluate(
        "() => { window.SM_BOOK_STATE = { picks: [], buys: [], sells: [], "
        "blocked: [], surplus: [], freeSlots: 0, overHeld: 0 }; }"
    )


def test_review_panel_renders_in_english(page):
    """Regression guard: renderReviewPanel()'s "no action" branch must render
    the expected headline/note text."""
    _set_fake_book_state(page)
    page.evaluate("() => window.renderReviewPanel()")
    headline = _rp_headline_text(page)
    note = _rp_note_text(page)
    assert headline is not None and headline.startswith("Next review"), (
        f"unexpected headline: {headline!r}"
    )
    assert note == "No action until then.", f"unexpected note: {note!r}"


def _rp_count(page):
    return page.evaluate(
        "() => { var c = document.getElementById('rp-count'); "
        "return c ? [c.textContent, c.className] : null; }"
    )


def test_review_panel_book_line_states_what_is_held_not_the_post_review_book(page):
    """The count used to be `book.picks.length` (the book AFTER the planned
    buys), so a reader holding 1 of 4 read "Book: 4 / 4 — refills at the next
    review". Drives the real page: with 1 held, 3 fillable free slots, no
    review due, the line must say 1 held and that 3 slots fill later. The pure
    rule is pinned in tests/test_book_line.py; this proves the panel is wired
    to it (book.held in, Rescore.bookLine out)."""
    page.evaluate(
        "() => { window.SM_BOOK_STATE = { picks: ['a','b','c','d'], "
        "buys: ['b','c','d'], sells: [], blocked: [], surplus: [], "
        "freeSlots: 3, overHeld: 0, held: ['a'] }; }"
    )
    page.evaluate("() => window.renderReviewPanel()")
    text, cls = _rp_count(page)
    target = page.evaluate("() => currentHorizon().top_n")
    assert text == f"Book: 1 / {target} — 3 free slots, they fill at the next review", text
    assert "under" in cls.split()


def test_review_panel_book_line_does_not_promise_a_refill_for_an_unbuyable_slot(page):
    page.evaluate(
        "() => { window.SM_BOOK_STATE = { picks: ['a','b','c'], buys: [], "
        "sells: [], blocked: ['x'], surplus: [], freeSlots: 1, overHeld: 0, "
        "held: ['a','b','c'] }; }"
    )
    page.evaluate("() => window.renderReviewPanel()")
    text, _ = _rp_count(page)
    assert "not buyable, so it stays empty" in text, text
    assert "fills at the next review" not in text, text


def test_book_state_carries_the_held_themes_from_applyHorizonBadges():
    """The browser tests above set book.held by hand; the only place it is
    really computed is applyHorizonBadges(). Pin that it is attached to the
    SAME object the panel reads, not a second global that can fall out of step."""
    from tests.test_dashboard_js import _TEMPLATE
    src = _TEMPLATE.read_text()
    assert "_bookState.held = _heldKeys" in src
    assert "SM_BOOK_HELD" not in src


def _theme_color_tags(page):
    return page.evaluate(
        "() => Array.prototype.map.call(document.querySelectorAll('meta[name=\"theme-color\"]'), "
        "m => [m.getAttribute('media'), m.getAttribute('content')])"
    )


def test_the_browser_chrome_colour_follows_the_manual_theme_toggle(page):
    """Click the real Light/Dark/Auto control and read the theme-color tags.
    On Auto each tag keeps its own scheme's colour; an explicit choice makes
    both say that colour (a light OS + a hand-picked Dark used to leave a light
    address bar under a dark page)."""
    light, dark = "#efe9dc", "#17150F"
    by_media = lambda tags: {("dark" in (m or "")): c for m, c in tags}   # noqa: E731
    assert by_media(_theme_color_tags(page)) == {False: light, True: dark}

    page.click('.theme-toggle .theme-btn[data-theme-choice="dark"]')
    assert {c for _, c in _theme_color_tags(page)} == {dark}
    page.click('.theme-toggle .theme-btn[data-theme-choice="light"]')
    assert {c for _, c in _theme_color_tags(page)} == {light}
    page.click('.theme-toggle .theme-btn[data-theme-choice="auto"]')
    assert by_media(_theme_color_tags(page)) == {False: light, True: dark}


def test_a_saved_blend_preference_from_the_discontinued_control_is_cleared(page):
    """Readers who once enabled the (removed) sentiment blend still hold its
    localStorage keys; the page clears them on load rather than leaving a stale
    preference lying around for any future reuse of the names."""
    page.evaluate("() => { localStorage.setItem('sentimentEnabled', 'true'); "
                  "localStorage.setItem('sentimentWeight', '30'); }")
    page.reload()
    left = page.evaluate("() => [localStorage.getItem('sentimentEnabled'), "
                         "localStorage.getItem('sentimentWeight')]")
    assert left == [None, None]


# ---------------------------------------------------------------------------
# The review panel's dots and done check (polish PR 3).
# ---------------------------------------------------------------------------

def _dot_classes(page):
    return page.evaluate(
        "() => Array.prototype.map.call(document.querySelectorAll('#rp-dots .rp-dot'), "
        "d => d.className.replace('rp-dot', '').trim())"
    )


def _render_with_held(page, n_held):
    page.evaluate(
        "(n) => { var held = []; for (var i = 0; i < n; i++) { held.push('T' + i); } "
        "window.SM_BOOK_STATE = { picks: [], buys: [], sells: [], blocked: [], "
        "surplus: [], freeSlots: 0, overHeld: Math.max(0, n - currentHorizon().top_n), "
        "held: held }; window.renderReviewPanel(); }",
        n_held,
    )
    return page.evaluate("() => currentHorizon().top_n")


def test_review_panel_draws_one_dot_per_slot_with_its_state(page):
    target = _render_with_held(page, 3)
    assert _dot_classes(page) == ["held"] * 3 + ["free"] * (target - 3)
    _render_with_held(page, target)
    assert _dot_classes(page) == ["held"] * target
    _render_with_held(page, target + 1)
    assert _dot_classes(page) == ["held"] * target + ["over"]


def test_the_dots_are_hidden_from_screen_readers_and_the_text_stays(page):
    target = _render_with_held(page, 3)
    assert page.evaluate("() => document.getElementById('rp-dots').getAttribute('aria-hidden')") == "true"
    text, _ = _rp_count(page)
    assert text.startswith(f"Book: 3 / {target}"), text


def test_the_dots_clear_when_there_is_no_book(page):
    _render_with_held(page, 3)
    page.evaluate("() => { window.SM_BOOK_STATE = null; window.renderReviewPanel(); }")
    assert _dot_classes(page) == []


def _set_review_status(page, due, due_date, next_date):
    page.evaluate(
        "([due, dueDate, nextDate]) => { window.currentReviewStatus = function () { "
        "return { due: due, dueDate: dueDate, nextDate: nextDate }; }; }",
        [due, due_date, next_date],
    )


def test_a_review_marked_done_shows_a_check_before_the_headline(page):
    """Done = a review date has come due and been acknowledged: not due now, but
    a due date exists. The check is decoration; the headline text is unchanged
    and starts at its first character."""
    _set_review_status(page, False, "2026-01-15", "2099-02-15")
    _render_with_held(page, 3)
    assert page.evaluate("() => !!document.querySelector('#rp-headline svg.rp-check')")
    assert page.evaluate("() => document.querySelector('#rp-headline svg.rp-check')"
                         ".getAttribute('aria-hidden')") == "true"
    assert _rp_headline_text(page).startswith("Next review")


def test_no_check_before_a_review_has_come_due_or_while_one_is_due(page):
    _set_review_status(page, False, None, "2099-02-15")       # between reviews, none yet due
    _render_with_held(page, 3)
    assert not page.evaluate("() => !!document.querySelector('#rp-headline .rp-check')")
    _set_review_status(page, True, "2026-01-15", "2099-02-15")  # due, not acknowledged
    _render_with_held(page, 3)
    assert not page.evaluate("() => !!document.querySelector('#rp-headline .rp-check')")


def test_the_check_does_not_stack_across_re_renders(page):
    _set_review_status(page, False, "2026-01-15", "2099-02-15")
    _render_with_held(page, 3)
    _render_with_held(page, 3)
    assert page.evaluate("() => document.querySelectorAll('#rp-headline .rp-check').length") == 1


# ---------------------------------------------------------------------------
# Gliding indicators (polish PR 4).
# ---------------------------------------------------------------------------

def _tab_indicator_state(page):
    return page.evaluate(
        "() => { var t = document.querySelector('.tabs'), i = t.querySelector('.tab-indicator'), "
        "a = t.querySelector('.tab-btn.active'); var ri = i.getBoundingClientRect(), "
        "ra = a.getBoundingClientRect(); return { left: ri.left, width: ri.width, "
        "aLeft: ra.left, aWidth: ra.width, transition: i.style.transition }; }"
    )


def _horizon_thumb_state(page):
    return page.evaluate(
        "() => { var th = document.querySelector('.horizon-thumb'), "
        "b = document.querySelector('.horizon-btn[aria-pressed=\"true\"]'); "
        "var rt = th.getBoundingClientRect(), rb = b.getBoundingClientRect(); "
        "return { left: rt.left, width: rt.width, bLeft: rb.left, bWidth: rb.width, "
        "transition: th.style.transition }; }"
    )


def _settle(page):
    page.wait_for_timeout(400)   # past --duration-base, so a glide has landed


def _aligned(st, a, b):
    return abs(st[a[0]] - st[b[0]]) < 1 and abs(st[a[1]] - st[b[1]]) < 1


def test_the_tab_indicator_follows_the_active_tab_and_glides_there(page):
    st = _tab_indicator_state(page)
    assert _aligned(st, ("left", "width"), ("aLeft", "aWidth")), st
    assert st["transition"] == "none", "the first placement must jump, not glide"
    page.evaluate("() => document.querySelectorAll('.tab-btn')[2].click()")
    mid = _tab_indicator_state(page)
    assert "transform" in mid["transition"], mid
    _settle(page)
    assert _aligned(_tab_indicator_state(page), ("left", "width"), ("aLeft", "aWidth"))


def test_the_horizon_thumb_sits_under_the_pressed_button_and_glides_between_buttons(page):
    st = _horizon_thumb_state(page)
    assert _aligned(st, ("left", "width"), ("bLeft", "bWidth")), st
    assert st["transition"] == "none"
    page.evaluate("() => switchHorizon('long')")
    assert "transform" in _horizon_thumb_state(page)["transition"]
    _settle(page)
    st = _horizon_thumb_state(page)
    assert _aligned(st, ("left", "width"), ("bLeft", "bWidth")), st
    assert page.evaluate("() => document.querySelector('.horizon-btn[aria-pressed=\"true\"]')"
                         ".getAttribute('data-horizon-choice')") == "long"


def test_a_resize_re_measures_both_indicators_without_gliding(page):
    page.evaluate("() => document.querySelectorAll('.tab-btn')[1].click()")
    page.evaluate("() => switchHorizon('long')")
    _settle(page)
    page.set_viewport_size({"width": 1100, "height": 800})
    page.evaluate("() => window.dispatchEvent(new Event('resize'))")
    assert _tab_indicator_state(page)["transition"] == "none"
    assert _horizon_thumb_state(page)["transition"] == "none"
    _settle(page)
    assert _aligned(_tab_indicator_state(page), ("left", "width"), ("aLeft", "aWidth"))
    assert _aligned(_horizon_thumb_state(page), ("left", "width"), ("bLeft", "bWidth"))


def test_reduced_motion_jumps_both_indicators_straight_to_the_end(page):
    page.emulate_media(reduced_motion="reduce")
    page.reload()
    page.evaluate("() => document.querySelectorAll('.tab-btn')[3].click()")
    page.evaluate("() => switchHorizon('long')")
    assert _tab_indicator_state(page)["transition"] == "none"
    assert _horizon_thumb_state(page)["transition"] == "none"
    assert _aligned(_tab_indicator_state(page), ("left", "width"), ("aLeft", "aWidth"))
    assert _aligned(_horizon_thumb_state(page), ("left", "width"), ("bLeft", "bWidth"))


# ---------------------------------------------------------------------------
# Row glide on a reader's own sort (polish PR 4).
# ---------------------------------------------------------------------------

_SPY_FLIP = (
    "() => { window.__flips = []; var real = window.SMMotion.flip; "
    "window.SMMotion.flip = function (rows, mutate) { window.__flips.push(rows.length); "
    "return real(rows, mutate); }; }"
)


def _first_rank(page):
    return page.evaluate(
        "() => document.querySelector('#leaderboard-table tbody tr.leaderboard-row').dataset.rank")


def test_a_readers_own_sort_glides_the_rows_through_flip_once(page):
    page.evaluate(_SPY_FLIP)
    # Each row travels with its breakdown row (see the test below), so flip is
    # given both.
    rows = page.evaluate("() => document.querySelectorAll('#leaderboard-table tbody tr.leaderboard-row, "
                         "#leaderboard-table tbody tr.breakdown-row').length")
    before = _first_rank(page)
    # The table starts on rank, ascending, so one more click flips it to descending.
    page.evaluate("() => sortTable(0)")
    assert page.evaluate("() => window.__flips") == [rows]
    assert _first_rank(page) != before, "the sort itself must still happen"
    page.evaluate("() => sortTable(2)")
    assert page.evaluate("() => window.__flips") == [rows, rows]


def test_the_signed_in_rebuild_re_applying_a_sort_does_not_glide(page):
    """reapplyLeaderboardSort puts back a sort the reader already chose after the
    rows were rebuilt; animating rows the reader never moved would be noise."""
    page.evaluate("() => sortTable(2)")
    page.evaluate(_SPY_FLIP)
    page.evaluate("() => window.reapplyLeaderboardSort()")
    assert page.evaluate("() => window.__flips") == []


def test_sorting_still_works_if_motion_js_did_not_load(page):
    page.evaluate("() => { window.SMMotion = undefined; }")
    before = _first_rank(page)
    page.evaluate("() => sortTable(0)")           # starts ascending on rank; this flips it
    assert _first_rank(page) != before


# ---------------------------------------------------------------------------
# Number and badge crossfades, check draw-in (polish PR 4).
# ---------------------------------------------------------------------------

_SPY_FLASH = (
    "() => { window.__flashes = []; var real = window.SMMotion.flash; "
    "window.SMMotion.flash = function (el, cls) { window.__flashes.push([el && (el.id || el.className), cls]); "
    "return real(el, cls); }; }"
)
_STAT_IDS = ["hz-held", "hz-exit", "hz-hold", "hz-trades", "tr-m1", "tr-m12"]


def _stat_texts(page):
    return page.evaluate(
        "(ids) => { var o = {}; ids.forEach(function (i) { var e = document.getElementById(i); "
        "o[i] = e ? e.textContent : null; }); return o; }", _STAT_IDS)


def test_nothing_crossfades_on_first_load(page):
    assert page.evaluate("() => document.querySelectorAll('.num-in, .badge-in').length") == 0


def test_a_horizon_switch_crossfades_exactly_the_numbers_that_changed(page):
    before = _stat_texts(page)
    page.evaluate(_SPY_FLASH)
    page.evaluate("() => switchHorizon('long')")
    after = _stat_texts(page)
    changed = sorted(k for k in before if before[k] != after[k])
    assert changed, "the fixture's horizons differ in top_n at least"
    flashed = sorted(i for i, cls in page.evaluate("() => window.__flashes") if cls == "num-in" and i in _STAT_IDS)
    assert flashed == changed, (flashed, changed)


def test_re_rendering_the_same_horizon_flashes_nothing(page):
    page.evaluate(_SPY_FLASH)
    page.evaluate("() => renderHorizonStats(activeHorizon)")
    page.evaluate("() => switchHorizon(activeHorizon)")
    assert [f for f in page.evaluate("() => window.__flashes") if f[1] == "num-in"] == []


def test_a_re_render_that_is_not_the_readers_switch_never_crossfades(page):
    """The signed-in upgrade re-renders the stats from the rebuilt board; the
    numbers may legitimately change then, and the spec says never to animate it."""
    page.evaluate(_SPY_FLASH)
    page.evaluate("() => { activeHorizon = 'long'; renderHorizonStats('long'); }")
    assert [f for f in page.evaluate("() => window.__flashes") if f[1] == "num-in"] == []


def test_the_review_count_crossfades_when_it_changes_and_not_when_it_does_not(page):
    _render_with_held(page, 3)
    page.evaluate(_SPY_FLASH)
    _render_with_held(page, 3)
    assert [f for f in page.evaluate("() => window.__flashes") if f[0] == "rp-count"] == []
    _render_with_held(page, 2)
    assert [f for f in page.evaluate("() => window.__flashes") if f[0] == "rp-count"] == [["rp-count", "num-in"]]


_APPLY_AS_SWITCH = "() => { window._animateBadges = true; window.applyHorizonBadges(); window._animateBadges = false; }"


def _badge_flashes(page):
    return [f for f in page.evaluate("() => window.__flashes") if f[1] == "badge-in"]


def test_a_badge_changed_by_a_horizon_switch_crossfades_and_nothing_else_does(page):
    page.evaluate("() => window.applyHorizonBadges()")
    page.evaluate(_SPY_FLASH)
    page.evaluate(_APPLY_AS_SWITCH)                                    # nothing changed
    assert _badge_flashes(page) == []
    page.evaluate("() => { document.querySelector('.setup-badge').className = 'setup-badge exit'; }")
    page.evaluate(_APPLY_AS_SWITCH)                                    # put back to entry: a change
    assert len(_badge_flashes(page)) == 1
    # the same change on any other pass (holdings arriving, the signed-in
    # rebuild) is rewritten in place, never animated
    page.evaluate("() => { document.querySelector('.setup-badge').className = 'setup-badge exit'; }")
    page.evaluate(_SPY_FLASH)
    page.evaluate("() => window.applyHorizonBadges()")
    assert _badge_flashes(page) == []
    # and a badge that did not exist is created, not crossfaded
    page.evaluate("() => document.querySelector('.setup-badge').remove()")
    page.evaluate(_APPLY_AS_SWITCH)
    assert _badge_flashes(page) == []


_DONE_STATE = (
    "(startDone) => { window.__acked = !!startDone; "
    "window.currentReviewStatus = function () { return window.__acked "
    "? { due: false, dueDate: '2026-01-15', nextDate: '2099-02-15' } "
    ": { due: true, dueDate: '2026-01-15', nextDate: '2099-02-15' }; }; "
    "window.ackReview = function () { window.__acked = true; }; }"
)


def test_a_check_already_there_at_load_is_drawn_static(page):
    page.evaluate(_DONE_STATE, True)
    _render_with_held(page, 3)
    assert page.evaluate("() => !!document.querySelector('#rp-headline .rp-check')")
    assert not page.evaluate("() => !!document.querySelector('#rp-headline .rp-check.draw')")


def test_marking_the_review_done_draws_the_check_in_once(page):
    page.evaluate(_DONE_STATE, False)
    _render_with_held(page, 3)
    assert not page.evaluate("() => !!document.querySelector('#rp-headline .rp-check')")
    page.evaluate("() => document.getElementById('review-done-btn').click()")
    page.wait_for_timeout(50)
    assert page.evaluate("() => !!document.querySelector('#rp-headline .rp-check.draw')")
    # A later re-render (the lock write resolving, a rank change) must keep the
    # same element, or the draw-in would restart or be cut off part-way.
    page.evaluate("() => { window.__chk = document.querySelector('#rp-headline .rp-check'); }")
    _render_with_held(page, 3)
    assert page.evaluate("() => document.querySelector('#rp-headline .rp-check') === window.__chk")
    # ...and a later render after the draw does not replay it.
    page.evaluate("() => document.querySelector('#rp-headline .rp-check').classList.remove('draw')")
    _render_with_held(page, 3)
    assert not page.evaluate("() => !!document.querySelector('#rp-headline .rp-check.draw')")


def test_the_first_paint_class_comes_off_after_the_fade_up_has_run(page):
    """With JS off it never comes off and the rows just end visible; with JS on it
    must, or a scan-history switch or a signed-in rebuild would replay the fade."""
    page.wait_for_timeout(900)
    assert not page.evaluate("() => document.documentElement.classList.contains('first-paint')")


def test_only_the_rows_that_carry_an_index_take_part_in_the_first_paint_fade(page):
    """The template sets --i on the top ten rows. The animation must key on that,
    so a row without it never animates (found by counting running animations:
    all 18 rows were fading up, not ten)."""
    out = page.evaluate(
        "() => { var rows = document.querySelectorAll('#leaderboard-table tr.leaderboard-row'); "
        "var el = rows[0]; var cs = getComputedStyle(el); "
        "var plain = document.createElement('tr'); plain.className = 'leaderboard-row'; "
        "el.parentNode.appendChild(plain); document.documentElement.classList.add('first-paint'); "
        "var withI = getComputedStyle(el).animationName, withoutI = getComputedStyle(plain).animationName; "
        "document.documentElement.classList.remove('first-paint'); plain.remove(); "
        "return { hasIndex: el.style.getPropertyValue('--i') !== '', withI: withI, withoutI: withoutI }; }"
    )
    assert out == {"hasIndex": True, "withI": "rows-in", "withoutI": "none"}


def test_an_open_breakdown_panel_glides_with_its_row_instead_of_jumping(page):
    """Each breakdown row follows its leaderboard row in the DOM, so a sort moves
    it too. Gliding only the row left an open panel jumping to its final place
    while its row was still en route: measured 249px above its own row mid-glide."""
    page.evaluate(
        "() => { var row = document.querySelector('#leaderboard-table tr.leaderboard-row[data-rank=\"1\"]'); "
        "var bd = document.getElementById('bd-' + row.dataset.themeId); "
        "bd.style.display = 'table-row'; bd.style.height = '60px'; "
        "window.__row = row; window.__bd = bd; }")
    # Start from a settled page: the first-paint intro offsets rows (not panels)
    # by a few px while it runs, which would skew the "before" reading.
    page.evaluate("() => { document.documentElement.style.scrollBehavior = 'auto'; "
                  "document.documentElement.classList.remove('first-paint'); }")
    gap_before = page.evaluate("() => __bd.getBoundingClientRect().top - __row.getBoundingClientRect().top")
    page.evaluate("() => sortTable(0)")               # starts ascending on rank: reverses the order
    gap_during = page.evaluate("() => __bd.getBoundingClientRect().top - __row.getBoundingClientRect().top")
    assert abs(gap_during - gap_before) < 1, (gap_before, gap_during)
    page.wait_for_timeout(400)
    gap_after = page.evaluate("() => __bd.getBoundingClientRect().top - __row.getBoundingClientRect().top")
    assert abs(gap_after - gap_before) < 1, (gap_before, gap_after)


def test_a_readers_own_sort_ends_the_first_paint_intro_at_once(page):
    """An intro animation still running on the rows shadows the glide on them but
    not on their breakdown panels, so a quick sort right after load would have
    rows jump while panels glide. The sort ends the intro first."""
    page.evaluate("() => document.documentElement.classList.add('first-paint')")
    page.evaluate("() => sortTable(2)")
    assert not page.evaluate("() => document.documentElement.classList.contains('first-paint')")
