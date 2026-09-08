"""The stop marker's contract with the page it decorates."""
from pathlib import Path


def test_stops_js_is_shipped_and_loaded():
    assert Path("dashboard/assets/stops.js").exists()
    footer = Path("dashboard/templates/_footer.html.j2").read_text()
    assert "stops.js" in footer


def test_marker_key_has_a_swedish_entry():
    """stop_chip_tip is the ONLY key this file requests. Do not add a
    `stop_chip_label` alongside it "for symmetry": the chip's visible text is
    a number built at runtime, not a translated string, and an SV entry no
    data-i18n attribute ever requests is dead the day it lands. This repo has
    already lost six LIVE keys to a dead-code sweep that could not tell the
    difference -- see tests/test_i18n_coverage.py's docstring."""
    sv = Path("dashboard/templates/i18n/_core.js.j2").read_text()
    assert "stop_chip_tip:" in sv
    assert "stop_chip_label:" not in sv


def test_marker_reads_the_latch_table_and_never_writes_it():
    """The client has SELECT only; a write here would be a silent 403 and,
    worse, would imply the page can fabricate a stop."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert 'from("position_stops")' in js
    for forbidden in (".insert(", ".update(", ".upsert(", ".delete("):
        assert forbidden not in js


def test_chip_is_appended_beside_theme_name_not_inside_it():
    """.theme-name holds only a single text node (index.html.j2's documented
    invariant) -- renderReviewPanel()'s nameOf(), the mobile card projection,
    and the band-cut summary strip all read its textContent/innerHTML
    directly and corrupt if a chip is nested inside it. Every other badge is
    inserted as a sibling within the containing cell; this pins that the chip
    follows the same convention, not `tr.querySelector(".theme-name")` as the
    append target."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert 'nameSpan.parentNode' in js
    assert 'tr.querySelector(".theme-name").appendChild' not in js
    # The old bug's exact shape: appending directly to whatever
    # tr.querySelector(".theme-name") returns.
    assert 'querySelector(".theme-name") ||' not in js


def test_chip_gets_a_scoped_translate_call_after_creation():
    """Page-wide applyLang() already ran by the time this file's listeners
    fire (auth.js runs it before dispatching the events stops.js listens
    for), so a newly-created chip is never swept up by a later full pass.
    Without a scoped call here, a Swedish-language reader sees the English
    tooltip forever. positions.js's applyRowState() solves the identical
    problem with window.applyLangToEl(el) -- this must call the same
    function on the chip it just created."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert "applyLangToEl(chip)" in js
