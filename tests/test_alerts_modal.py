"""The alerts panel is a modal opened from the footer, not a block below it.

It used to render as a permanently-present section appended AFTER the footer —
below the disclaimer and Methodology link — which is an odd reading order and an
odd home for a control only signed-in readers can use.

Most of these pin the two traps the work had to avoid, both of which are silent
if they regress:

1. `alert-prefs.js` owns `#alert-prefs`'s `hidden` attribute to mean "alerts are
   available to this reader". A modal overlay uses `hidden` to mean "closed".
   Collapsing them into one element would make opening the dialog claim alerts
   are available, and hiding an unavailable panel would look like a closed
   dialog. The overlay must be a separate wrapper.
2. The trigger lives inside `{% if auth %}`, or guests get a footer link to a
   dialog that cannot exist.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_FOOTER = _ROOT / "dashboard" / "templates" / "_footer.html.j2"
_JS = _ROOT / "dashboard" / "assets" / "alert-prefs.js"


def _footer() -> str:
    return _FOOTER.read_text()


def _footer_without_comments() -> str:
    """Footer source with Jinja comments stripped.

    Necessary, not tidiness: the comment explaining the auth gate contains the
    literal string `{% if auth %}`, so a naive search finds the COMMENT and the
    gate test passes even when the real gate is gone — verified by mutation, the
    comment defeated the test that guards what it describes.
    """
    return re.sub(r"\{#.*?#\}", "", _FOOTER.read_text(), flags=re.S)


def test_the_overlay_is_a_separate_element_from_the_prefs_section():
    """Trap 1. Two different meanings of `hidden` need two different elements."""
    text = _footer()
    overlay_at = text.index('id="alerts-modal"')
    prefs_at = text.index('id="alert-prefs"')
    assert overlay_at < prefs_at, "#alert-prefs must be nested inside the overlay"
    assert 'id="alerts-modal"' not in text[prefs_at:prefs_at + 200], (
        "the overlay and the prefs section must not be the same element"
    )


def test_the_prefs_section_is_inside_the_dialog():
    text = _footer()
    dialog_at = text.index('role="dialog"')
    prefs_at = text.index('id="alert-prefs"')
    close_at = text.index("</section>", prefs_at)
    assert dialog_at < prefs_at < close_at


def test_the_footer_link_is_gated_on_auth():
    """Trap 2. A guest must not get a link to a dialog that cannot exist."""
    text = _footer_without_comments()
    link_at = text.index('id="alerts-link"')
    # Walk back to the nearest {% if %} / {% endif %} and check we are inside an
    # auth gate rather than trusting line proximity.
    before = text[:link_at]
    gate = before.rfind("{% if auth %}")
    assert gate != -1, "no {% if auth %} precedes the alerts footer link"
    # Nesting, not counting: an earlier unrelated {% if %}…{% endif %} pair makes
    # a naive open/close tally wrong. The nearest preceding auth gate simply must
    # not be closed before the link.
    assert "{% endif %}" not in before[gate:], (
        "the alerts footer link sits after its {% if auth %} block closed"
    )


def test_the_footer_link_starts_hidden():
    """Availability is only known after an auth round-trip; showing the link
    before that would offer an empty dialog to a signed-in reader whose
    alert_prefs table is missing."""
    text = _footer()
    tag = re.search(r'<button[^>]*id="alerts-link"[^>]*>', text).group(0)
    assert " hidden" in tag


def test_availability_is_written_in_one_place():
    """The panel and the footer link must not drift apart. Every former
    `root.hidden = …` now goes through setAvailable(), which owns both."""
    js = _JS.read_text()
    assert "function setAvailable(" in js
    # The only direct assignment left is the one inside setAvailable itself.
    assert js.count("root.hidden =") == 1
    body_at = js.index("function setAvailable(")
    assert "root.hidden =" in js[body_at:body_at + 400]
    assert 'getElementById("alerts-link")' in js[body_at:body_at + 400]


def test_the_modal_uses_the_shared_helper_rather_than_a_hand_rolled_one():
    """The 2026-08-09 audit's P1 finding was a modal that declared
    aria-modal="true" and implemented none of it. Focus trap, Escape,
    backdrop-close and focus restore come from _modal.js.j2.

    _footer.html.j2 no longer includes _modal.js.j2 itself (2026-08-23 sweep
    — it was the third of three copies inlined into every page; see
    test_every_aria_modal_dialog_is_bound_to_the_helper in test_dashboard_js.py
    for the guarantee that the pages that include this partial always include
    _modal.js.j2 earlier in the document). window.SMModal.bind(...) being
    called here is still the guarantee this test exists to pin."""
    text = _footer()
    assert "window.SMModal.bind(" in text
    # Nothing hand-rolled alongside it.
    assert "keydown" not in text.split("SMModal.bind(")[1][:600]


def test_the_dialog_declares_its_accessible_name():
    text = _footer()
    assert 'aria-modal="true"' in text
    assert 'aria-labelledby="alerts-title"' in text
    assert 'id="alerts-title"' in text


def test_the_deep_link_waits_for_availability():
    """`#alerts` on a cold load arrives long before the auth round-trip that
    decides whether there is anything to show, so the open has to happen in
    alert-prefs.js, not next to the other trigger binding."""
    js = _JS.read_text()
    assert 'location.hash === "#alerts"' in js
    assert "SMAlertsModal" in js
    assert 'location.hash === "#alerts"' not in _footer()
