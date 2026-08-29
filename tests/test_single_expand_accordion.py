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
    """sector_ids whose desktop breakdown row is currently expanded."""
    return pg.evaluate("""() => Array.from(
        document.querySelectorAll('.breakdown-row.open')
    ).map(el => el.id.replace(/^bd-/, ''))""")


def _open_card_ids(pg):
    """sector_ids whose mobile card is currently expanded."""
    return pg.evaluate("""() => Array.from(
        document.querySelectorAll('.leaderboard-card.open')
    ).map(el => el.dataset.sectorId)""")


def _two_row_ids(pg):
    ids = pg.evaluate("""() => Array.from(
        document.querySelectorAll('.leaderboard-row[data-sector-id]')
    ).map(tr => tr.dataset.sectorId)""")
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
             '.leaderboard-row[data-sector-id="' + id + '"]'
           ).getAttribute('aria-expanded')""", a)
    assert expanded == "false", (
        f"the auto-collapsed row still reports aria-expanded={expanded!r}"
    )


def test_opening_a_second_mobile_card_closes_the_first(page):
    cards = page.evaluate("""() => Array.from(
        document.querySelectorAll('.leaderboard-card[role="button"]')
    ).map(c => c.dataset.sectorId)""")
    if len(cards) < 2:
        pytest.skip(f"need two expandable mobile cards, got {cards}")
    a, b = cards[0], cards[1]

    page.evaluate("""(id) => document.querySelector(
        '.leaderboard-card[data-sector-id="' + id + '"]').click()""", a)
    assert _open_card_ids(page) == [a]

    page.evaluate("""(id) => document.querySelector(
        '.leaderboard-card[data-sector-id="' + id + '"]').click()""", b)
    assert _open_card_ids(page) == [b], (
        "opening a second mobile card left the first one open"
    )
