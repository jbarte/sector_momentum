"""The review panel: one prominent carrier for cadence and book state.

Replaces the inline "Next review:" chip, whose own CSS comment concedes it was
"deliberately as quiet as .horizon-note". It was quiet enough to be missed
entirely for weeks, which is the failure this panel exists to fix.
"""
import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_TPL = _ROOT / "dashboard/templates/index.html.j2"
_PANEL = _ROOT / "dashboard/templates/_review_panel.html.j2"
_I18N = _ROOT / "dashboard/templates/i18n/_core.js.j2"


def test_panel_template_exists_and_is_included():
    assert _PANEL.exists(), "the review panel template was not created"
    assert '{% include "_review_panel.html.j2" %}' in _TPL.read_text(), (
        "the panel exists but is never included, so it renders nowhere"
    )


def test_inline_chip_is_removed_not_left_alongside():
    """Two carriers for one fact is how the reader learns to ignore both."""
    text = _TPL.read_text()
    assert 'id="review-status"' not in text, (
        "the old inline chip is still present -- it must be replaced by the "
        "panel, not left competing with it"
    )


def test_panel_states_the_consequence_not_only_the_date():
    """Root cause (1): the chip named a date and never said what it meant."""
    panel = _PANEL.read_text()
    assert "rp_no_action" in panel, (
        "the panel never states 'no action until then' -- naming a date alone "
        "is what already failed"
    )


def test_panel_has_all_four_states():
    js = _TPL.read_text()
    for marker in ("rp-headline", "rp-actions", "rp-count"):
        assert marker in js, f"panel is missing its {marker} region"
    assert "renderReviewPanel" in js, "no render function for the panel"


def test_panel_explains_an_unbuyable_blocked_slot():
    """Otherwise the panel instructs a buy the reader cannot execute."""
    js = _TPL.read_text()
    assert "blocked" in js and "rp_slot_empty" in js, (
        "the panel does not handle selectBook's `blocked` list, so an "
        "unbuyable candidate would either be named as a buy or vanish silently"
    )


def test_panel_names_the_no_changes_state_explicitly():
    """A common state; an unexplained empty panel reads as a bug."""
    assert "rp_no_changes" in _TPL.read_text() + _PANEL.read_text()


def test_panel_handles_an_exhausted_review_calendar():
    """review_dates bakes only 6 dates from build time. CI rebuilds daily so
    this should not fire in production, but without the guard a long-unbuilt
    page renders "Next review · " with an empty date -- which reads as broken
    rather than stale. Flagged in the spec's Risks section."""
    assert "rp_calendar_stale" in _TPL.read_text(), (
        "no guard for an exhausted review calendar"
    )


def test_every_panel_string_has_swedish():
    sv = _I18N.read_text()
    for key in ("rp_next_review", "rp_no_action", "rp_review_due",
                "rp_no_changes", "rp_sell", "rp_buy", "rp_slot_empty",
                "rp_book", "rp_too_many", "rp_will_refill",
                "rp_calendar_stale"):
        assert f"{key}:" in sv, f"{key} has no Swedish translation"
