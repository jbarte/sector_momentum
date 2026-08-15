"""Landmarks and heading outline — the P2 remainder of the 2026-08-09 design
review audit (BACKLOG.md, "Design review findings").

Re-verified against the code on 2026-08-15 rather than trusted from the
audit's wording: the "13 icon-only ↗ links" finding no longer matches
anything in the codebase (grepped for the glyph across templates/assets/i18n
— zero hits, resolved by an unrelated redesign since 2026-08-09) and is not
re-tested here. The "474 elements under 12px" finding was re-measured in a
live browser (92 elements on the default leaderboard view alone) and found to
require a typography/density redesign across the whole product, not a
mechanical patch — deliberately left for its own backlog item rather than
"fixed" here as a blanket font-size bump.

What *is* fixed and guarded here: `<main>` + skip link (were entirely
missing), `<nav role="tablist">` suppressing the page's only navigation
landmark (an explicit ARIA role always wins over an element's implicit one,
so a tablist inside <nav> can never expose a navigation landmark — the fix is
a plain <div>, not reconciling the two roles), and the "h2 → h4, no h3"
heading skip inside the tab-guide modals. Two more skip instances were found
during this work that the audit didn't name (h1 → h3, no h2, for "Badge
scorecard" and "News sentiment") and are fixed the same way.

The heading-skip checker resolves `{% include %}` directives before scanning
(`_resolve_includes`) — a page's own raw text alone misses headings that live
in a partial it pulls in (_header.html.j2's <h1>, _footer.html.j2's Alerts
<h2>, _methodology.html.j2's <h2>/<h3> sequence, _validation.html.j2's two
<h3>s), so a version of this test that skipped that step could not have
caught a skip introduced inside any of them.
"""
import re
from pathlib import Path

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"
_PAGES = ["index.html.j2", "sentiment.html.j2"]
_HEADING_RE = re.compile(r"<h([1-6])[ >]")
_INCLUDE_RE = re.compile(r"""\{%-?\s*include\s+["']([^"']+)["']\s*-?%\}""")


def _resolve_includes(text: str, _depth: int = 0) -> str:
    """Inline every `{% include "X" %}`/`{% include 'X' %}` with X's raw
    text, recursively. A page's OWN raw text misses headings that live in an
    included partial (_header.html.j2's <h1>, _footer.html.j2's Alerts <h2>,
    _methodology.html.j2's <h2>/<h3> sequence, _validation.html.j2's two
    <h3>s) — code review caught this on the first version of this file,
    which scanned only the page's own text and so could not have caught a
    skip introduced inside any of those partials. Capped recursion depth
    guards against a future circular include hanging the test suite; 6
    levels comfortably exceeds this codebase's actual include depth (2)."""
    if _depth > 6:
        return text

    def _sub(m: re.Match) -> str:
        target = _TPL_DIR / m.group(1)
        if not target.exists():
            return m.group(0)  # e.g. a non-template include name; leave as-is
        return _resolve_includes(target.read_text(), _depth + 1)

    return _INCLUDE_RE.sub(_sub, text)


def _text(name: str) -> str:
    return (_TPL_DIR / name).read_text()


# ---------------------------------------------------------------------------
# <main> landmark + skip link
# ---------------------------------------------------------------------------

def test_both_pages_have_exactly_one_main_landmark():
    for page in _PAGES:
        text = _text(page)
        assert text.count("<main") == 1, f"{page} should have exactly one <main>"
        assert "</main>" in text, f"{page} is missing its </main>"


def test_main_is_a_valid_skip_link_target():
    """`tabindex="-1"` is required, not decorative: without it, clicking a
    `<a href="#main-content">` scrolls the viewport but does NOT move keyboard
    focus there (verified in-browser — activeElement fell back to <body>,
    meaning a keyboard user would have to tab through the header again,
    defeating the skip link's purpose). `tabindex="-1"` makes the element a
    valid focus target without adding it to the normal Tab sequence."""
    for page in _PAGES:
        text = _text(page)
        assert re.search(r'<main\s+id="main-content"\s+tabindex="-1"', text), (
            f'{page}: <main> must be `<main id="main-content" tabindex="-1">`'
        )


def test_both_pages_have_a_skip_link_targeting_main():
    for page in _PAGES:
        text = _text(page)
        assert '<a href="#main-content" class="skip-link"' in text, (
            f"{page} is missing the skip-link"
        )


def test_skip_link_is_the_first_thing_after_body_open():
    """Order matters — a skip link buried after other focusable chrome (the
    header's sign-in button, theme toggle, etc.) makes a keyboard user tab
    through all of it before reaching the thing meant to let them skip past
    exactly that."""
    for page in _PAGES:
        text = _text(page)
        body_idx = text.index("<body>")
        skip_idx = text.index('class="skip-link"')
        header_include_idx = text.index("_header.html.j2")
        assert body_idx < skip_idx < header_include_idx, (
            f"{page}: skip-link must appear between <body> and the header include"
        )


def test_skip_link_css_hides_it_until_focused():
    css = _text("css/_chrome.css.j2")
    assert ".skip-link {" in css
    assert re.search(r"\.skip-link\s*\{[^}]*top:\s*-40px", css), (
        "skip-link must be off-screen by default"
    )
    assert re.search(r"\.skip-link:focus-visible\s*\{[^}]*top:\s*8px", css), (
        "skip-link must become visible on keyboard focus"
    )


# ---------------------------------------------------------------------------
# Header landmark
# ---------------------------------------------------------------------------

def test_header_partial_is_a_semantic_header_not_a_div():
    """A `<div class="command-bar">` as a direct child of <body> carries no
    landmark role at all — this was the concrete cause of "footer is the
    page's sole landmark" (2026-08-09 audit). `<header>` at the top level of
    <body> (not nested in <article>/<section>) is the `banner` landmark."""
    header = _text("_header.html.j2")
    assert header.startswith("<header class=\"command-bar\">"), (
        "_header.html.j2 must open with <header>, not <div>"
    )
    assert header.rstrip().endswith("</header>"), (
        "_header.html.j2 must close with </header>, not </div>"
    )


# ---------------------------------------------------------------------------
# Tab bar: role="tablist" must not sit on the page's only <nav>
# ---------------------------------------------------------------------------

def test_tabs_widget_is_not_a_nav_element():
    """An explicit ARIA role always overrides an element's implicit one, so
    `<nav role="tablist">` can never expose a navigation landmark — and this
    was the page's ONLY <nav>, so the whole page lost its nav landmark to a
    role conflict that a `<div role="tablist">` doesn't have in the first
    place (ARIA's own authoring practices don't nest a tablist in <nav>
    anyway — it isn't page navigation in the landmark sense)."""
    text = _text("index.html.j2")
    # Strip HTML comments before searching: the fix's own explanatory comment
    # mentions "<nav>" (and "<nav role=\"tablist\">") in prose, which both a
    # bare substring check and a tag-boundary regex wrongly matched in turn
    # before this stripping was added.
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    assert not re.search(r"<nav[ >]", without_comments), (
        "index.html.j2 must not contain a <nav> element — the tab bar is a "
        'plain <div class="tabs" role="tablist">'
    )
    assert re.search(r'<div class="tabs" role="tablist">', text), (
        'the tab bar\'s role="tablist" must still be present, just on a div'
    )


# ---------------------------------------------------------------------------
# Heading outline — no level skips
# ---------------------------------------------------------------------------

def _heading_levels(text: str) -> list[int]:
    return [int(m.group(1)) for m in _HEADING_RE.finditer(text)]


def test_no_heading_level_skips_within_each_page():
    """The audit's literal finding ('h2 → h4, no h3') was a skip inside the
    tab-guide modal. Includes are resolved first (see `_resolve_includes`) so
    this checks the heading sequence actually rendered on the page — <h1>
    from _header.html.j2, <h2>/<h3> from _footer.html.j2 and
    _methodology.html.j2, the two <h3>s from _validation.html.j2 — not just
    what happens to be typed directly into index.html.j2/sentiment.html.j2.
    Decreasing levels (h3 -> h2, starting a new subsection) are always fine;
    only an *increase* of more than one level between consecutive headings is
    a skip."""
    for page in _PAGES:
        resolved = _resolve_includes(_text(page))
        levels = _heading_levels(resolved)
        assert len(levels) >= 5, (
            f"{page}: only found {len(levels)} headings after resolving "
            f"includes — include resolution is likely broken, not the page"
        )
        for prev, cur in zip(levels, levels[1:]):
            assert cur <= prev + 1, (
                f"{page}: heading level jumps from h{prev} to h{cur} — "
                f"skips h{prev + 1}. Full sequence: {levels}"
            )


def test_guide_body_headings_are_h3_everywhere_including_the_swedish_copy():
    """`.tab-guide-body`'s subsection headings must be h3 (the modal's own
    title is h2), and this must hold in the Swedish translation too —
    `_guides.js.j2` is swapped in wholesale via `data-i18n-html` on language
    switch, so an h4 left behind there would reintroduce the skip only in
    Swedish."""
    for name in ("index.html.j2", "sentiment.html.j2", "i18n/_guides.js.j2"):
        text = _text(name)
        assert "<h4>" not in text and "</h4>" not in text, (
            f"{name} still has an h4 inside a tab-guide-body block"
        )

    css = _text("css/_guides.css.j2")
    assert ".tab-guide-body h3 {" in css
    assert ".tab-guide-body h4" not in css


def test_promoted_standalone_headings_kept_their_styling():
    """badge_scorecard_title and sent_news_heading moved from h3 to h2 to
    close an h1 -> h3 skip (found while verifying the audit's literal claim,
    not itself named in it). Pure tag rename — asserts the inline styles
    survived unchanged, since a tag-name edit has no business touching
    presentation."""
    index = _text("index.html.j2")
    assert re.search(
        r'<h2 style="margin:22px 0 6px;font-family:var\(--font-display\);'
        r'font-size:15px;color:var\(--fg1\)" data-i18n="badge_scorecard_title">',
        index,
    ), "badge_scorecard_title must be an h2 with its original inline style"

    sentiment = _text("sentiment.html.j2")
    assert re.search(
        r'<h2 style="margin:24px 0 8px 4px" data-i18n="sent_news_heading">',
        sentiment,
    ), "sent_news_heading must be an h2 with its original inline style"
