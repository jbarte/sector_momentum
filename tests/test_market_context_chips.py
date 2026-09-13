"""The market-context chips: one tappable control, one shared explanation.

The chips (`Live`, `SPY`, `VIX`) render in `_header.html.j2`, which BOTH pages
include, but their explanation used to live inside `guide_body_leaderboard` —
reachable only from index.html, and only via a `title` tooltip that does not
exist on touch at all. It is now its own guide body in a shared partial, opened
by tapping the chips.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_TPL = _ROOT / "dashboard" / "templates"


def test_sentiment_page_has_no_market_context_guide_left():
    """Task 3 (2026-09-05) removed the market-context chips (and this guide)
    entirely — the sentiment page has no track-record cell to explain, so it
    gets no replacement guide either, unlike index.html.j2 (see
    test_index_page_carries_the_track_record_guide_body below)."""
    text = (_TPL / "sentiment.html.j2").read_text()
    assert '{% include "_guide_market_context.html.j2" %}' not in text
    assert not (_TPL / "_guide_market_context.html.j2").exists()


def test_index_page_carries_the_track_record_guide_body():
    """index.html.j2's Cell C now explains the 1M/12M performance chips, not
    the market-context ones -- see test_sentiment_page_still_carries_the_market_
    context_guide_body above for why the two pages now diverge."""
    text = (_TPL / "index.html.j2").read_text()
    assert '{% include "_guide_track_record.html.j2" %}' in text
    assert '{% include "_guide_market_context.html.j2" %}' not in text


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


def test_index_guide_dispatch_prefers_an_explicit_label():
    """index.html.j2's track-record trigger's visible text is live data
    ("1M +1.2pp 12M +32.2pp"), which made a nonsense dialog heading when the
    dispatch used textContent. Caught in the browser, so pin it: the dispatch
    must prefer a `.cc-label` child over the trigger's own text."""
    text = (_TPL / "index.html.j2").read_text()
    assert '.cc-label' in text, "index.html.j2's guide dispatch does not prefer .cc-label"


def test_sentiment_guide_dispatch_has_no_vacuous_cc_label_check():
    """Finding 7 (2026-09-05 track-record-chip review): sentiment.html.j2's
    copy of the dispatch used to prefer `.cc-label` too, a leftover from when
    the header's SPY/VIX chips (which did carry that class) rendered on both
    pages. Those chips are gone and no element on this page has ever carried
    `.cc-label`, so the check was vacuous — it could never actually fire.
    Simplified to a plain textContent read; this pins the simplification so a
    future copy-paste from index.html.j2 doesn't reintroduce dead code here."""
    text = (_TPL / "sentiment.html.j2").read_text()
    assert '.cc-label' not in text


def test_the_track_record_guide_body_declares_the_key_the_chips_open():
    """Analogue of the deleted test_the_guide_body_declares_the_key_the_chips_
    open (see git show 2eeb589:tests/test_market_context_chips.py), which
    pinned the same property for the now-deleted market-context guide. Cell
    C's trigger button emits data-guide="guide_body_track_record" (see
    test_the_chips_are_one_tappable_control above); if the guide partial's
    own attribute drifts from that key, SMModal opens an empty dialog and
    nothing fails."""
    body = (_TPL / "_guide_track_record.html.j2").read_text()
    assert 'data-guide-body="guide_body_track_record"' in body


def test_the_leaderboard_guide_has_no_market_context_pointer_left():
    """The leaderboard guide used to end with an inline SPY/VIX explanation,
    then (Stage 4) a "Tap them" pointer at the header chips. Task 3
    (2026-09-05) removed the chips outright, so the pointer has nothing left
    to point at and is gone too — not repointed at Cell C, which has its own
    guide trigger already."""
    index = (_TPL / "index.html.j2").read_text()
    assert "the US market's distance from its" not in index
    assert "Market context chips" not in index


def test_the_live_chip_is_explained():
    """It was a plain "Live" word with no tooltip and no explanation."""
    auth = (_ROOT / "dashboard" / "assets" / "auth.js").read_text()
    assert 'chip.setAttribute("title", "Showing the latest scan")' in auth
    # And it lands with the chips it belongs to, not loose in the meta cluster.
    # Stage 4 moved those into the summary strip; markLive()'s host lookup
    # follows them, or it silently takes its .meta-cluster fallback.
    assert 'getElementById("market-context-chips")' in auth
