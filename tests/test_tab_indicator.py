"""The tab bar's single indicator element and its overflow edge fades
(polish PR 3, spec § 2).

Two pure rules live in rescore.js so they run under Node here: where the
indicator sits under the active tab, and which edges of an overflowing tab bar
are clipped. The page only paints them. Source-level checks pin the wiring
(markup, CSS, and the events that re-place the indicator), the repo's
convention for CSS and inline script (see test_sticky_header.py).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _ROOT / "dashboard" / "assets" / "rescore.js"
_TPL = _ROOT / "dashboard" / "templates"

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(expr: str):
    script = f"""
      const R = require({str(_RESCORE_JS)!r});
      console.log(JSON.stringify({expr}));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------------------------------------------------------------------------
# indicatorBox
# ---------------------------------------------------------------------------

@_needs_node
def test_the_indicator_spans_the_active_tab():
    assert _node("R.indicatorBox({offsetLeft: 120, offsetWidth: 86})") == {"x": 120, "w": 86}


@_needs_node
def test_no_active_tab_means_no_box():
    """The indicator is left where it was rather than jumping to 0 wide."""
    assert _node("R.indicatorBox(null)") is None


# ---------------------------------------------------------------------------
# overflowState
# ---------------------------------------------------------------------------

def _overflow(scroll_left, client, scroll):
    return _node(f"R.overflowState({scroll_left}, {client}, {scroll})")


@_needs_node
def test_a_bar_that_fits_has_no_fades():
    assert _overflow(0, 700, 700) == ""


@_needs_node
def test_a_bar_scrolled_to_the_start_fades_its_end():
    assert _overflow(0, 375, 640) == "end"


@_needs_node
def test_a_bar_scrolled_to_the_end_fades_its_start():
    assert _overflow(265, 375, 640) == "start"


@_needs_node
def test_a_bar_scrolled_between_fades_both_edges():
    assert _overflow(100, 375, 640) == "both"


@_needs_node
def test_one_pixel_of_rounding_is_not_overflow():
    """scrollWidth and clientWidth are rounded; a sub-pixel gap must not draw a
    fade over a bar that visibly fits, or one that has reached its end."""
    assert _overflow(0, 700, 701) == ""
    assert _overflow(0, 700, 702) == "end"
    assert _overflow(1, 375, 640) == "end"       # 1px from the start is the start
    assert _overflow(264, 375, 640) == "start"   # 1px short of the end is the end


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

def test_the_indicator_is_the_last_child_of_the_tab_bar_and_hidden_from_readers():
    html = (_TPL / "index.html.j2").read_text()
    bar = re.search(r'<div class="tabs" role="tablist">(.*?)\n</div>', html, re.S).group(1)
    assert re.search(r'<span class="tab-indicator" aria-hidden="true"></span>\s*$', bar)
    assert bar.count("tab-indicator") == 1


def test_the_active_tab_hands_its_underline_to_the_indicator():
    css = (_TPL / "css" / "_chrome.css.j2").read_text()
    tabs = re.search(r"\n\.tabs\s*\{([^}]*)\}", css).group(1)
    assert "position: relative" in tabs
    active = re.search(r"\.tab-btn\.active\s*\{([^}]*)\}", css).group(1)
    assert "border-bottom-color: transparent" in active
    ind = re.search(r"\.tab-indicator\s*\{([^}]*)\}", css).group(1)
    assert "position: absolute" in ind and "var(--brand-strong)" in ind


def test_edge_fades_cover_each_overflow_state_with_the_webkit_prefix():
    css = (_TPL / "css" / "_chrome.css.j2").read_text()
    for state in ("end", "start", "both"):
        m = re.search(r'\.tabs\[data-overflow="%s"\]\s*\{([^}]*)\}' % state, css)
        assert m, state
        assert "-webkit-mask-image" in m.group(1) and "mask-image" in m.group(1), state


def test_the_indicator_is_re_placed_on_every_event_that_moves_it():
    js = (_TPL / "_tabs.js.j2").read_text()
    assert "Rescore.indicatorBox" in js and "Rescore.overflowState" in js
    switch = re.search(r"function switchTab\(.*?\n\}", js, re.S).group(0)
    assert "placeIndicator(true)" in switch
    assert re.search(r"addEventListener\('resize',[^;]*placeIndicator", js, re.S)
    assert "document.fonts" in js and "placeIndicator" in js[js.index("document.fonts"):]
    # scroll updates the fades, and must not block scrolling
    assert re.search(r"addEventListener\('scroll',\s*updateTabOverflow,\s*\{\s*passive:\s*true\s*\}\)", js)


def test_only_a_tab_switch_glides_every_other_re_measure_jumps():
    """The indicator glides when the reader picks a tab. Page load, a resize and
    the web font landing all re-measure a layout that changed under it, so it
    jumps to the new box instead of sliding across."""
    js = (_TPL / "_tabs.js.j2").read_text()
    place = re.search(r"function placeIndicator\(glide\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "SMMotion.glideTo(ind, box)" in place
    assert re.search(r"if \(!glide\)\s*\{?\s*ind\.dataset\.placed\s*=\s*''", place)
    # nothing but switchTab passes true
    assert len(re.findall(r"placeIndicator\(true\)", js)) == 1
    for trigger in ("'load'", "'resize'", "document.fonts.ready"):
        i = js.index(trigger)
        assert "placeIndicator()" in js[i:i + 160], trigger


def test_the_indicator_still_places_when_motion_js_did_not_load():
    js = (_TPL / "_tabs.js.j2").read_text()
    place = re.search(r"function placeIndicator\(glide\)\s*\{(.*?)\n\}", js, re.S).group(1)
    assert "window.SMMotion" in place and "translateX(" in place
