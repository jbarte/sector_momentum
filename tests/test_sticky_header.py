"""The leaderboard header sticks on wide screens (polish PR 3, spec § 2).

Static checks over the template source, the repo's convention for CSS (see
test_typography_floor.py). Two details are easy to break silently and cost the
feature without any error:

- Sticky positioning does not work inside a scroll container. `.card` used
  `overflow: hidden` to clip its corners, which makes it one; `overflow: clip`
  clips identically without becoming a scroll container.
- The same goes for `.table-wrap`, whose `overflow-x: auto` is needed on narrow
  screens (the table scrolls sideways) and has to be released where the header
  sticks.
"""
import re
from pathlib import Path

_TPL = Path(__file__).parent.parent / "dashboard" / "templates"
_CSS = _TPL / "css"


def _wide_css() -> str:
    """Width queries live in _responsive.css.j2: a partial that loads earlier
    can have its media query overridden by a later rule, and
    test_color_theme.test_no_responsive_overrides_left_earlier_in_the_cascade
    forbids them there."""
    return (_CSS / "_responsive.css.j2").read_text()


def _wide_block(css: str) -> str:
    return re.search(r"@media \(min-width: 1024px\)\s*\{(.*?)\n\}", css, re.S).group(1)


def test_card_clips_without_becoming_a_scroll_container():
    css = (_CSS / "_chrome.css.j2").read_text()
    card = re.search(r"\.card\s*\{([^}]*)\}", css).group(1)
    assert "overflow: clip" in card and "overflow: hidden" not in card


def test_leaderboard_header_is_sticky_only_on_wide_screens():
    css = _wide_css()
    block = _wide_block(css)
    assert "position: sticky" in block and "#leaderboard-table thead th" in block
    # Nothing outside the wide block may make the header sticky.
    outside = css.replace(block, "") + (_CSS / "_tables.css.j2").read_text()
    assert "position: sticky" not in outside


def test_the_leaderboards_scroll_wrapper_is_released_where_the_header_sticks():
    """overflow-x: auto on the wrapper would make it the sticky header's scroll
    container, pinning the header to the wrapper instead of the viewport."""
    block = _wide_block(_wide_css())
    assert re.search(r"\.table-wrap[^{]*\{[^}]*overflow-x:\s*visible", block)


def test_only_the_leaderboards_wrapper_is_released():
    """The scan index and validation tables share `.table-wrap` and still need
    their sideways scroll; releasing it everywhere would clip them at 1024px."""
    block = _wide_block(_wide_css())
    rule = re.search(r"([^{}]*)\{[^}]*overflow-x:\s*visible", block).group(1)
    assert "#leaderboard-table" in rule


def test_a_sentinel_sits_directly_before_the_leaderboard_table():
    html = (_TPL / "index.html.j2").read_text()
    assert re.search(
        r'<div class="sticky-sentinel" aria-hidden="true"></div>\s*'
        r'<div class="table-wrap">\s*<table id="leaderboard-table"',
        html,
    ), "sentinel must be the sibling just before the table's wrapper"


def test_the_stuck_hairline_follows_an_intersection_observer_on_the_sentinel():
    html = (_TPL / "index.html.j2").read_text()
    assert "IntersectionObserver" in html
    assert re.search(r"toggleAttribute\('data-stuck',\s*!", html)
    assert "#leaderboard-table[data-stuck] thead th" in _wide_css()


def test_the_stuck_line_is_a_shadow_because_collapsed_borders_do_not_travel():
    """#leaderboard-table is border-collapse, where a cell's border belongs to the
    table and stays behind when the cell sticks: the computed colour reads right
    and nothing paints (found by looking at pixels, 2026-11-08). The line that
    marks a stuck header must be a box-shadow, which moves with the cell."""
    block = _wide_block(_wide_css())
    stuck = re.search(r"#leaderboard-table\[data-stuck\] thead th\s*\{([^}]*)\}", block).group(1)
    assert "box-shadow" in stuck and "var(--border-soft)" in stuck
    assert "border-bottom-color" not in stuck
