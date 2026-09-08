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
