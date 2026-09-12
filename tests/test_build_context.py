"""The two context values the beginner deck reads must exist, on both pages,
and must be genuinely live (not typed-out copies) -- see
sector_momentum-notes/specs/2026-09-09-beginner-walkthrough-deck-design.md."""
import re
from pathlib import Path

_BUILD_PY = Path(__file__).parent.parent / "dashboard" / "build.py"


def _source() -> str:
    return _BUILD_PY.read_text()


def test_default_horizon_top_n_is_in_both_page_contexts():
    src = _source()
    # Anchor: both dicts already carry this exact key as a sibling to it.
    assert src.count('"round_trip_bps": _round_trip_bps') == 2, (
        "this test's anchor assumption changed -- re-check both context dicts by hand"
    )
    assert src.count('"default_horizon_top_n"') == 2


def test_trailing_stop_pct_is_in_both_page_contexts():
    src = _source()
    assert src.count('"trailing_stop_pct"') == 2


def test_trailing_stop_pct_is_computed_from_the_live_reader_not_hardcoded():
    """Must call trailing_stop_frac(), not type out 12 -- that's the whole
    point of this value existing as a context key at all."""
    src = _source()
    assert "trailing_stop_frac()" in src


def test_default_horizon_top_n_is_the_live_attribute_not_hardcoded():
    src = _source()
    assert "_default_horizon.top_n" in src


def test_trailing_stop_frac_is_in_both_page_contexts():
    src = _source()
    assert src.count('"trailing_stop_frac"') == 2


def test_trailing_stop_frac_is_the_raw_fraction_not_the_rounded_percent():
    """Dividing a live drawdown by a ROUNDED percent (e.g. 12 instead of
    0.117) would skew every bar slightly -- this must be the bare float
    trailing_stop_frac() returns, not round(... * 100)."""
    src = _source()
    for line in src.splitlines():
        if '"trailing_stop_frac"' in line:
            assert "round(" not in line
            assert "trailing_stop_frac()" in line


def test_footer_exposes_the_stop_frac_as_a_js_global_before_stops_js():
    footer = Path(__file__).parent.parent / "dashboard" / "templates" / "_footer.html.j2"
    text = footer.read_text()
    assert "window.SM_TRAILING_STOP_FRAC = {{ trailing_stop_frac }};" in text
    # Must be BEFORE stops.js loads, or the value is undefined when that
    # script's own top-level code runs (script tags execute in document order).
    assert text.index("window.SM_TRAILING_STOP_FRAC") < text.index('asset_url(\'stops.js\')')
