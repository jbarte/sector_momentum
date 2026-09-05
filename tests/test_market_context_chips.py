"""The market-context chips: one tappable control, one shared explanation.

The chips (`Live`, `SPY`, `VIX`) render in `_header.html.j2`, which BOTH pages
include, but their explanation used to live inside `guide_body_leaderboard` —
reachable only from index.html, and only via a `title` tooltip that does not
exist on touch at all. It is now its own guide body in a shared partial, opened
by tapping the chips.

Also guards the trap this feature nearly shipped twice: an invented
`data-i18n-*` attribute is silently inert, because the i18n pass only implements
a fixed set of them.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_TPL = _ROOT / "dashboard" / "templates"

_PAGES = ["index.html.j2", "sentiment.html.j2"]


def test_sentiment_page_still_carries_the_market_context_guide_body():
    """sentiment.html.j2 has no summary strip of its own, so it still relies on
    the shared header's chips and this guide for its "what these mean" copy.
    index.html.j2 no longer does — the track-record chip (2026-09-05) replaced
    its Cell C copy of these chips with its own guide, see
    test_index_page_carries_the_track_record_guide_body below. Task 3 removes
    this include (and the header chips) from sentiment.html.j2 too; until then
    both must keep resolving."""
    text = (_TPL / "sentiment.html.j2").read_text()
    assert '{% include "_guide_market_context.html.j2" %}' in text


def test_index_page_carries_the_track_record_guide_body():
    """index.html.j2's Cell C now explains the 1M/12M performance chips, not
    the market-context ones -- see test_sentiment_page_still_carries_the_market_
    context_guide_body above for why the two pages now diverge."""
    text = (_TPL / "index.html.j2").read_text()
    assert '{% include "_guide_track_record.html.j2" %}' in text
    assert '{% include "_guide_market_context.html.j2" %}' not in text


def test_the_guide_body_declares_the_key_the_chips_open():
    body = (_TPL / "_guide_market_context.html.j2").read_text()
    assert 'data-guide-body="guide_body_market_context"' in body
    # The i18n pass needs the node present at load to swap EN/SV on it.
    assert 'data-i18n-html="guide_body_market_context"' in body


def test_the_chips_are_one_tappable_control():
    """Stage 4 (2026-08-21) moved the chips out of the command bar into the
    summary strip's Cell C — spec Screen 1 item 1. The control they hang off
    is now the cell's eyebrow label rather than a header button, but the
    property this test exists for is unchanged: the explanation is reachable
    from one real <button>, not a div with a click handler. Cell C's own
    content changed (2026-09-05, track-record chip) from the SPY/VIX chips to
    the 1M/12M performance ones, but the tappable-control shape is the same."""
    index = (_TPL / "index.html.j2").read_text()
    assert 'id="cell-track-record"' in index
    assert 'id="market-context-chips"' in index
    assert 'data-guide="guide_body_track_record"' in index
    cell = index[index.index('id="cell-track-record"'):]
    cell = cell[:cell.index("</section>")]
    assert re.search(r'<button[^>]*class="strip-eyebrow tab-guide-btn"', cell)
    # And nothing is left behind in the command bar to render them twice.
    assert 'id="context-chips"' not in (_TPL / "_header.html.j2").read_text()


@pytest.mark.parametrize("page", _PAGES)
def test_the_guide_dispatch_prefers_an_explicit_label(page):
    """The chips' visible text is live data ("SPY +10.1% VIX 14.6"), which made a
    nonsense dialog heading when the dispatch used textContent. Caught in the
    browser, so pin it: both pages carry their own copy of this dispatch and both
    must prefer `.cc-label`."""
    text = (_TPL / page).read_text()
    assert '.cc-label' in text, f"{page}'s guide dispatch does not prefer .cc-label"


def test_the_leaderboard_guide_no_longer_duplicates_the_copy():
    """It points at the chips instead. Duplicated copy is how two versions drift."""
    index = (_TPL / "index.html.j2").read_text()
    # The distinctive sentence from the old inline section.
    assert "the US market's distance from its" not in index
    assert "Tap them" in index


# ---------------------------------------------------------------------------
# The dead-attribute guard
# ---------------------------------------------------------------------------

def _implemented_i18n_attrs() -> set[str]:
    """Which data-i18n-* attributes the i18n pass actually reads."""
    text = (_TPL / "_i18n.html.j2").read_text()
    return set(re.findall(r'\[(data-i18n[a-z-]*)\]', text))


def test_the_i18n_pass_handles_aria_labels():
    assert "data-i18n-aria" in _implemented_i18n_attrs()


def test_no_template_uses_an_i18n_attribute_the_pass_ignores():
    """An invented `data-i18n-*` attribute does nothing — it is not a fallback,
    it is silently inert, so the string stays English for Swedish readers with no
    error anywhere.

    This nearly shipped twice in one change: `data-i18n-label` and
    `data-i18n-guide-label`, both made up, both dead. Enumerate what the pass
    implements and assert every attribute in use is one of them.
    """
    implemented = _implemented_i18n_attrs()
    assert implemented, "could not parse the i18n pass — did its selectors change?"

    used: dict[str, set[str]] = {}
    for path in _TPL.rglob("*.j2"):
        for attr in re.findall(r'\b(data-i18n[a-z-]*)\s*=', path.read_text()):
            used.setdefault(attr, set()).add(path.name)

    unknown = {a: sorted(f) for a, f in used.items() if a not in implemented}
    assert not unknown, (
        "these data-i18n-* attributes are not implemented by _i18n.html.j2 and "
        f"therefore do nothing: {unknown}. Implemented: {sorted(implemented)}"
    )


@pytest.mark.parametrize("key", [
    "market_context_title", "market_context_aria", "chip_live", "chip_live_tip",
])
def test_swedish_has_the_new_plain_strings(key):
    sv = (_TPL / "i18n" / "_core.js.j2").read_text()
    assert f"{key}:" in sv


def test_swedish_has_the_guide_body_in_the_html_bundle():
    """data-i18n-html reads SV_HTML, not SV — a copy in SV would never be used."""
    guides = (_TPL / "i18n" / "_guides.js.j2").read_text()
    assert "guide_body_market_context:" in guides
    body_at = guides.index("guide_body_market_context:")
    assert "Object.assign(SV_HTML" in guides[:body_at], (
        "guide_body_market_context must be assigned into SV_HTML"
    )


def test_the_live_chip_is_translatable_and_explained():
    """It was a hardcoded English word with no tooltip and no explanation."""
    auth = (_ROOT / "dashboard" / "assets" / "auth.js").read_text()
    assert '"data-i18n", "chip_live"' in auth
    assert '"data-i18n-title", "chip_live_tip"' in auth
    # And it lands with the chips it belongs to, not loose in the meta cluster.
    # Stage 4 moved those into the summary strip; markLive()'s host lookup
    # follows them, or it silently takes its .meta-cluster fallback.
    assert 'getElementById("market-context-chips")' in auth
