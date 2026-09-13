"""Only one row/card may be expanded at a time (accordion behaviour).

Requested 2026-08-25. Before this, `toggleBreakdown()` (desktop) and the
mobile card's own click handler each toggled ONLY the row/card they were
called on, so a reader could open every theme's breakdown at once and then
had to close each one by hand.

Driven in a real browser rather than asserted against source text: the
property here is "opening B closes A", which is a relationship between two
elements across two separate click events. A source scan can confirm a
collapse call EXISTS but not that it fires on the right element at the
right time -- and this same session already shipped a bug that source-text
tests waved through (see tests/test_badge_i18n_playwright.py's docstring).
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dashboard_render_coalescing import (  # noqa: E402
    _render_leaderboard_html, browser, page,          # noqa: F401
)


def _open_breakdown_ids(pg):
    """theme_ids whose desktop breakdown row is currently expanded."""
    return pg.evaluate("""() => Array.from(
        document.querySelectorAll('.breakdown-row.open')
    ).map(el => el.id.replace(/^bd-/, ''))""")


def _open_card_ids(pg):
    """theme_ids whose mobile card is currently expanded."""
    return pg.evaluate("""() => Array.from(
        document.querySelectorAll('.leaderboard-card.open')
    ).map(el => el.dataset.themeId)""")


def _two_row_ids(pg):
    ids = pg.evaluate("""() => Array.from(
        document.querySelectorAll('.leaderboard-row[data-theme-id]')
    ).map(tr => tr.dataset.themeId)""")
    assert len(ids) >= 2, f"need two expandable rows to test an accordion, got {ids}"
    return ids[0], ids[1]


def test_opening_a_second_row_closes_the_first(page):
    a, b = _two_row_ids(page)
    page.evaluate("(id) => toggleBreakdown(id)", a)
    assert _open_breakdown_ids(page) == [a]

    page.evaluate("(id) => toggleBreakdown(id)", b)
    assert _open_breakdown_ids(page) == [b], (
        "opening a second breakdown left the first one open -- a reader can "
        "still expand every theme at once"
    )


def test_a_row_still_closes_when_toggled_twice(page):
    """The accordion must not break the plain open/close toggle."""
    a, _ = _two_row_ids(page)
    page.evaluate("(id) => toggleBreakdown(id)", a)
    assert _open_breakdown_ids(page) == [a]
    page.evaluate("(id) => toggleBreakdown(id)", a)
    assert _open_breakdown_ids(page) == []


def test_collapsed_row_reports_aria_expanded_false(page):
    """The row a reader did not touch must not keep announcing itself as
    expanded once the accordion closed it."""
    a, b = _two_row_ids(page)
    page.evaluate("(id) => toggleBreakdown(id)", a)
    page.evaluate("(id) => toggleBreakdown(id)", b)
    expanded = page.evaluate(
        """(id) => document.querySelector(
             '.leaderboard-row[data-theme-id="' + id + '"]'
           ).getAttribute('aria-expanded')""", a)
    assert expanded == "false", (
        f"the auto-collapsed row still reports aria-expanded={expanded!r}"
    )


def test_opening_a_second_mobile_card_closes_the_first(page):
    """A card's expand affordance moved from the card div itself
    (role="button") to a real, dedicated <button class="card-expand-hit">
    inside it (2026-09-12, nested-interactive-content fix) -- expandable
    cards are now found and clicked via that button, not the card."""
    cards = page.evaluate("""() => Array.from(
        document.querySelectorAll('.card-expand-hit')
    ).map(b => b.closest('.leaderboard-card').dataset.themeId)""")
    if len(cards) < 2:
        pytest.skip(f"need two expandable mobile cards, got {cards}")
    a, b = cards[0], cards[1]

    page.evaluate("""(id) => document.querySelector(
        '.leaderboard-card[data-theme-id="' + id + '"] .card-expand-hit').click()""", a)
    assert _open_card_ids(page) == [a]

    page.evaluate("""(id) => document.querySelector(
        '.leaderboard-card[data-theme-id="' + id + '"] .card-expand-hit').click()""", b)
    assert _open_card_ids(page) == [b], (
        "opening a second mobile card left the first one open"
    )


def test_mobile_card_aria_expanded_lives_on_the_button_not_the_div(page):
    """aria-expanded moved from .leaderboard-card (which is no longer even
    focusable) to .card-expand-hit -- the actual trigger, per WAI-ARIA's
    disclosure pattern, and the only one of the two that is still a real
    interactive element after 2026-09-12's fix. Driven live rather than
    read from source: this is state mutated by a click handler, not a
    static attribute, so a source scan could confirm the string exists
    without confirming which element actually ends up carrying it after a
    real toggle."""
    cards = page.evaluate("""() => Array.from(
        document.querySelectorAll('.card-expand-hit')
    ).map(b => b.closest('.leaderboard-card').dataset.themeId)""")
    if not cards:
        pytest.skip("need at least one expandable mobile card")
    a = cards[0]
    sel = '.leaderboard-card[data-theme-id="' + a + '"] .card-expand-hit'

    assert page.evaluate(
        "(sel) => document.querySelector(sel).getAttribute('aria-expanded')", sel
    ) == "false"

    page.evaluate("(sel) => document.querySelector(sel).click()", sel)
    assert page.evaluate(
        "(sel) => document.querySelector(sel).getAttribute('aria-expanded')", sel
    ) == "true"
    # The card div itself carries no aria-expanded at all any more -- it is
    # not the trigger and is not even focusable.
    assert page.evaluate(
        """(id) => document.querySelector(
             '.leaderboard-card[data-theme-id="' + id + '"]'
           ).getAttribute('aria-expanded')""", a
    ) is None


def test_card_expand_hit_does_not_swallow_a_tap_on_the_star(page):
    """The whole point of moving the expand affordance to a real, sibling
    <button class="card-expand-hit"> instead of role="button" on the card
    (BACKLOG.md, 2026-09-12) is that a tap on the star still reaches the
    star. That depends on real CSS paint order (CSS2.1 Appendix E: a
    positioned z-index:auto element paints above ordinary non-positioned
    siblings regardless of DOM order, so .card-expand-hit -- positioned,
    first child -- would otherwise cover the star too), which no source
    scan can prove: it needs an actual rendered layout and real hit-testing
    at real screen coordinates.

    This fixture's rendered page has no real .position-toggle (positions.js
    fails open without a Supabase client, so it never runs here) -- one is
    injected directly onto the first expandable card, at the exact position
    _renderMobileCardsNow() would place a real one (first child of
    .card-line1, immediately after .card-rank), so this exercises the same
    CSS rules a real star would."""
    page.set_viewport_size({"width": 375, "height": 812})
    theme_id = page.evaluate("""() => {
        var hit = document.querySelector('.card-expand-hit');
        if (!hit) return null;
        var card = hit.closest('.leaderboard-card');
        var line1 = card.querySelector('.card-line1');
        var star = document.createElement('button');
        star.type = 'button';
        star.className = 'position-toggle';
        star.textContent = '\\u2606';
        var rank = line1.querySelector('.card-rank');
        line1.insertBefore(star, rank ? rank.nextSibling : line1.firstChild);
        return card.dataset.themeId;
    }""")
    if theme_id is None:
        pytest.skip("need at least one expandable mobile card")

    star_hit, card_hit = page.evaluate("""(id) => {
        var card = document.querySelector('.leaderboard-card[data-theme-id="' + id + '"]');
        var star = card.querySelector('.position-toggle');
        var hit = card.querySelector('.card-expand-hit');
        var sRect = star.getBoundingClientRect();
        var atStar = document.elementFromPoint(
            sRect.left + sRect.width / 2, sRect.top + sRect.height / 2);
        var cRect = card.getBoundingClientRect();
        var atPadding = document.elementFromPoint(cRect.left + 4, cRect.bottom - 4);
        return [atStar === star, atPadding === hit];
    }""", theme_id)
    assert star_hit, "a tap at the star's own coordinates did not hit the star"
    assert card_hit, "a tap elsewhere on the card did not reach .card-expand-hit"
