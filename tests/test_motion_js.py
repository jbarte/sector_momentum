"""motion.js: the dashboard's discreet motion (spec 2026-11-08 § 3).

Runs the real file under Node with a stubbed `window`/`requestAnimationFrame`,
the way test_book_line.py runs rescore.js. Every effect must ask `allowed()`
first, so a reader with Reduce Motion gets the end state at once; that is the
property most worth pinning, since nothing fails visibly when it is wrong.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_MOTION_JS = _ROOT / "dashboard" / "assets" / "motion.js"
_TPL = _ROOT / "dashboard" / "templates"

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

# A tiny DOM: rows whose bounding top is their layout top plus whatever
# translateY is currently inlined, like a real element mid-transition.
_HARNESS = r"""
const queue = [];
global.requestAnimationFrame = (f) => { queue.push(f); };
function runFrames() { while (queue.length) { queue.shift()(); } }
function makeWindow(reduce) {
  return { matchMedia: (q) => ({ matches: reduce && q.indexOf('prefers-reduced-motion') >= 0 }) };
}
function makeRow(layoutTop, inline) {
  const listeners = {};
  const row = {
    layoutTop: layoutTop,
    style: Object.assign({ transform: '', transition: '' }, inline || {}),
    offsetWidth: 1,
    getBoundingClientRect() {
      const m = /translateY\((-?[0-9.]+)px\)/.exec(this.style.transform || '');
      return { top: this.layoutTop + (m ? parseFloat(m[1]) : 0) };
    },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    removeEventListener(t, f) { listeners[t] = (listeners[t] || []).filter((x) => x !== f); },
    fire(t, e) { (listeners[t] || []).slice().forEach((f) => f(e || {})); },
    classList: null,
  };
  return row;
}
function load(reduce) {
  global.window = makeWindow(reduce);
  delete require.cache[require.resolve(%(path)r)];
  require(%(path)r);
  return global.window.SMMotion;
}
"""


def _run(body: str):
    script = (_HARNESS % {"path": str(_MOTION_JS)}) + "\n" + body
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------------------------------------------------------------------------
# allowed
# ---------------------------------------------------------------------------

@_needs_node
def test_motion_is_allowed_unless_the_reader_asked_for_reduced_motion():
    out = _run("""
      console.log(JSON.stringify({ normal: load(false).allowed(), reduced: load(true).allowed() }));
    """)
    assert out == {"normal": True, "reduced": False}


@_needs_node
def test_a_browser_without_matchmedia_gets_motion():
    out = _run("""
      global.window = {}; delete require.cache[require.resolve(%r)]; require(%r);
      console.log(JSON.stringify(global.window.SMMotion.allowed()));
    """ % (str(_MOTION_JS), str(_MOTION_JS)))
    assert out is True


# ---------------------------------------------------------------------------
# flip
# ---------------------------------------------------------------------------

@_needs_node
def test_flip_runs_the_reorder_exactly_once_whatever_the_motion_setting():
    out = _run("""
      const res = {};
      for (const reduce of [false, true]) {
        const M = load(reduce); let calls = 0;
        M.flip([makeRow(0), makeRow(40)], () => { calls++; }); runFrames();
        res[reduce ? 'reduced' : 'normal'] = calls;
      }
      console.log(JSON.stringify(res));
    """)
    assert out == {"normal": 1, "reduced": 1}


@_needs_node
def test_flip_under_reduced_motion_touches_no_row():
    out = _run("""
      const M = load(true); const a = makeRow(0), b = makeRow(40);
      M.flip([a, b], () => { a.layoutTop = 40; b.layoutTop = 0; }); runFrames();
      console.log(JSON.stringify([a.style, b.style]));
    """)
    assert out == [{"transform": "", "transition": ""}] * 2


@_needs_node
def test_a_row_that_moved_starts_where_it_was_and_then_glides_home():
    out = _run("""
      const M = load(false); const a = makeRow(0), b = makeRow(40);
      M.flip([a, b], () => { a.layoutTop = 40; b.layoutTop = 0; });
      const start = [a.style.transform, a.style.transition, b.style.transform];
      runFrames();
      console.log(JSON.stringify({ start, end: [a.style.transform, a.style.transition] }));
    """)
    # Before the transition frame: each row sits at its old place, no transition.
    assert out["start"] == ["translateY(-40px)", "none", "translateY(40px)"]
    # Then it is released with the glide transition.
    assert out["end"][0] == ""
    assert "transform" in out["end"][1] and "--ease-glide" in out["end"][1]


@_needs_node
def test_a_row_that_did_not_move_is_left_alone():
    out = _run("""
      const M = load(false); const still = makeRow(0), mover = makeRow(10);
      M.flip([still, mover], () => { mover.layoutTop = 50; }); runFrames();
      console.log(JSON.stringify(still.style));
    """)
    assert out == {"transform": "", "transition": ""}


@_needs_node
def test_the_start_position_is_flushed_before_the_transition_frame():
    """Setting the inverted transform and clearing it in one frame, with no style
    recalculation between, makes the browser see no change and never animate. A
    layout read in between forces it; the stub counts reads of offsetWidth."""
    out = _run("""
      const M = load(false); let reads = 0;
      const a = makeRow(0);
      Object.defineProperty(a, 'offsetWidth', { get() { reads++; return 1; } });
      const b = makeRow(40);
      M.flip([a, b], () => { a.layoutTop = 40; b.layoutTop = 0; });
      console.log(JSON.stringify({ readsBeforeFrame: reads }));
    """)
    assert out["readsBeforeFrame"] >= 1


@_needs_node
def test_a_second_sort_mid_glide_measures_from_where_the_row_is_drawn_not_from_layout():
    """A row part-way through a glide is drawn away from its layout position. The
    next flip must start from the drawn position and end at the new layout
    position; reading the drawn position as "after" as well would animate by the
    wrong distance."""
    out = _run("""
      const M = load(false);
      // Layout top 0, but still drawn 10px up from an earlier glide.
      const a = makeRow(0, { transform: 'translateY(-10px)' }), b = makeRow(40);
      M.flip([a, b], () => { a.layoutTop = 40; b.layoutTop = 0; });
      console.log(JSON.stringify(a.style.transform));
    """)
    # drawn at -10, new layout 40: it must start 50px above where it will settle
    assert out == "translateY(-50px)"


@_needs_node
def test_only_the_rows_own_transform_ending_releases_the_transition():
    """transitionend bubbles from descendants and fires per property; a hover
    background fade ending first must not strip the glide's transition."""
    out = _run("""
      const M = load(false); const a = makeRow(0), b = makeRow(40);
      M.flip([a, b], () => { a.layoutTop = 40; b.layoutTop = 0; }); runFrames();
      a.fire('transitionend', { propertyName: 'background-color', target: a });
      const afterOther = a.style.transition;
      a.fire('transitionend', { propertyName: 'transform', target: a });
      console.log(JSON.stringify({ afterOther, afterTransform: a.style.transition }));
    """)
    assert "transform" in out["afterOther"]
    assert out["afterTransform"] == ""


# ---------------------------------------------------------------------------
# flash
# ---------------------------------------------------------------------------

_CLASS_LIST = r"""
function makeEl() {
  const set = new Set(); const log = [];
  return { offsetWidth: 1, log,
    classList: { add: (c) => { set.add(c); log.push('+' + c); }, remove: (c) => { set.delete(c); log.push('-' + c); },
                 contains: (c) => set.has(c) } };
}
"""


@_needs_node
def test_flash_removes_then_re_adds_the_class_so_the_animation_restarts():
    out = _run(_CLASS_LIST + """
      const M = load(false); const el = makeEl(); el.classList.add('flash'); el.log.length = 0;
      M.flash(el);
      console.log(JSON.stringify({ log: el.log, has: el.classList.contains('flash') }));
    """)
    assert out == {"log": ["-flash", "+flash"], "has": True}


@_needs_node
def test_flash_takes_a_class_name_and_is_silent_under_reduced_motion():
    out = _run(_CLASS_LIST + """
      const el = makeEl(); load(false).flash(el, 'num-in');
      const quiet = makeEl(); load(true).flash(quiet, 'num-in');
      load(false).flash(null);
      console.log(JSON.stringify({ named: el.classList.contains('num-in'), reduced: quiet.log }));
    """)
    assert out == {"named": True, "reduced": []}


# ---------------------------------------------------------------------------
# glideTo
# ---------------------------------------------------------------------------

@_needs_node
def test_the_first_placement_jumps_and_later_ones_glide():
    out = _run("""
      const M = load(false); const ind = { style: {}, dataset: {} };
      M.glideTo(ind, { x: 10, w: 80 }); const first = [ind.style.transition, ind.style.transform];
      M.glideTo(ind, { x: 120, w: 60 }); const second = [ind.style.transition, ind.style.transform];
      console.log(JSON.stringify({ first, second }));
    """)
    assert out["first"] == ["none", "translateX(10px) scaleX(80)"]
    assert "transform" in out["second"][0] and out["second"][1] == "translateX(120px) scaleX(60)"


@_needs_node
def test_reduced_motion_always_jumps():
    out = _run("""
      const M = load(true); const ind = { style: {}, dataset: {} };
      M.glideTo(ind, { x: 1, w: 2 }); M.glideTo(ind, { x: 5, w: 6 });
      console.log(JSON.stringify([ind.style.transition, ind.style.transform]));
    """)
    assert out == ["none", "translateX(5px) scaleX(6)"]


@_needs_node
def test_clearing_the_placed_flag_makes_the_next_placement_jump_again():
    """A resize re-measures every tab, so the indicator should jump to the new
    width rather than glide across a layout that just changed under it."""
    out = _run("""
      const M = load(false); const ind = { style: {}, dataset: {} };
      M.glideTo(ind, { x: 10, w: 80 }); ind.dataset.placed = '';
      M.glideTo(ind, { x: 30, w: 90 });
      console.log(JSON.stringify(ind.style.transition));
    """)
    assert out == "none"


@_needs_node
def test_a_thumb_is_sized_by_width_not_stretched_by_scale():
    """scaleX would distort a rounded, shadowed thumb as it resizes; width sets it
    once and only translateX moves."""
    out = _run("""
      const M = load(false); const th = { style: {}, dataset: {} };
      M.glideTo(th, { x: 40, w: 62 }, { sizeByWidth: true });
      console.log(JSON.stringify([th.style.width, th.style.transform]));
    """)
    assert out == ["62px", "translateX(40px)"]


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

def test_the_page_loads_motion_js_before_its_inline_scripts():
    html = (_TPL / "index.html.j2").read_text()
    tag = "{{ asset_url('motion.js') }}"
    assert tag in html
    assert html.index(tag) < html.index("<script>\n{% include '_tabs.js.j2' %}")


# ---------------------------------------------------------------------------
# CSS: the one reduced-motion switch, and what may animate
# ---------------------------------------------------------------------------

import re  # noqa: E402

_CSS = _TPL / "css"


def _all_css() -> str:
    return "\n".join(p.read_text() for p in sorted(_CSS.glob("*.j2")))


def test_one_global_rule_stops_everything_that_moves_under_reduced_motion():
    css = (_CSS / "_foundation.css.j2").read_text()
    m = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert m, "no reduced-motion block in _foundation.css.j2"
    block = m.group(1)
    assert re.search(r"\*,\s*\*::before,\s*\*::after", block)
    for decl in ("animation-duration: .01ms !important",
                 "animation-iteration-count: 1 !important",
                 "transition-duration: .01ms !important"):
        assert decl in block, decl


def test_the_three_older_reduced_motion_rules_stay():
    """They also set illustrations' final state, which the global rule does not."""
    found = {n: (_CSS / n).read_text().count("@media (prefers-reduced-motion: reduce)")
             for n in ("_chrome.css.j2", "_guides.css.j2")}
    assert found == {"_chrome.css.j2": 2, "_guides.css.j2": 1}


_KEYFRAMES_ADDED = ("rows-in", "num-in", "badge-in", "check-draw")
# transform and opacity run on the compositor; stroke-dashoffset is the check's
# draw-in, on one small path. Nothing else may animate: width, top, margin and
# the like would re-lay out the table on every frame.
_ANIMATABLE = {"opacity", "transform", "stroke-dashoffset"}


def _keyframe_props(css: str, name: str) -> set[str]:
    m = re.search(r"@keyframes\s+%s\s*\{" % re.escape(name), css)
    assert m, f"@keyframes {name} not found"
    depth, i = 1, m.end()
    while depth:                      # match the closing brace, one-liner or not
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    body = css[m.end():i - 1]
    # declarations inside the keyframe blocks, not the percentage/from/to labels
    return set(re.findall(r"([a-z-]+)\s*:", body))


@pytest.mark.parametrize("name", _KEYFRAMES_ADDED)
def test_new_keyframes_animate_only_opacity_transform_and_the_checks_dash(name):
    props = _keyframe_props(_all_css(), name)
    assert props and props <= _ANIMATABLE, (name, props - _ANIMATABLE)


def test_the_sort_chevron_glides_its_flip_and_fades_in():
    css = (_CSS / "_tables.css.j2").read_text()
    base = re.search(r'thead th\[tabindex="0"\]::after\s*\{([^}]*)\}', css).group(1)
    assert re.search(r"transition:\s*transform var\(--duration-base\) var\(--ease-glide\),"
                     r"\s*opacity var\(--duration-fast\) var\(--ease-standard\)", base)


# ---------------------------------------------------------------------------
# CSS and markup for the remaining effects
# ---------------------------------------------------------------------------

_PRESS = {
    ".pill-button": "_chrome.css.j2", ".tab-btn": "_chrome.css.j2",
    ".review-done-btn": "_tables.css.j2", ".filter-chip": "_tables.css.j2",
    ".control-chip": "_tables.css.j2", ".horizon-btn": "_tables.css.j2",
}


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?m)^%s\s*\{([^}]*)\}" % re.escape(selector), css)
    assert m, f"{selector} rule not found"
    return m.group(1)


@pytest.mark.parametrize("selector", sorted(_PRESS))
def test_buttons_press_down_and_keep_their_existing_transitions(selector):
    css = (_CSS / _PRESS[selector]).read_text()
    assert re.search(r"%s:active[^{]*\{[^}]*transform:\s*scale\(\.97\)" % re.escape(selector), css), selector
    base = _rule(css, selector)
    assert "transform 100ms var(--ease-standard)" in base, selector
    # appended to the existing list, not replacing it: a colour transition that
    # was there before is still there
    if selector in (".pill-button", ".tab-btn", ".filter-chip", ".horizon-btn"):
        assert "color var(--duration-fast)" in base, selector


def test_press_does_not_reach_table_rows():
    assert not re.search(r"leaderboard-row[^{]*:active", _all_css())


def test_the_check_draws_in_only_when_told_to():
    css = (_CSS / "_tables.css.j2").read_text()
    assert re.search(r"\.rp-check path\s*\{[^}]*stroke-dasharray:\s*14[^}]*stroke-dashoffset:\s*0", css)
    assert re.search(r"\.rp-check\.draw path\s*\{[^}]*animation:\s*check-draw var\(--duration-slow\) var\(--ease-glide\)", css)
    assert not re.search(r"\.rp-check path\s*\{[^}]*animation", css), "the draw must be opt-in via .draw"


def test_numbers_and_badges_crossfade_with_token_durations():
    css = _all_css()
    assert re.search(r"\.num-in\s*\{[^}]*animation:\s*num-in var\(--duration-base\) var\(--ease-glide\)", css)
    assert re.search(r"\.badge-in\s*\{[^}]*animation:\s*badge-in var\(--duration-fast\)", css)


def test_first_paint_fades_the_top_ten_rows_up_and_only_under_the_class():
    css = _all_css()
    m = re.search(r'\.first-paint #leaderboard-table tbody tr\.leaderboard-row\[style\*="--i"\]\s*\{([^}]*)\}', css)
    assert m, "first-paint rule missing, or it no longer keys on --i (top ten rows only)"
    body = m.group(1)
    # backwards, not both: the animation must not keep ownership of transform after
    # it ends, or it shadows the row glide for as long as the class stays on
    assert "animation: rows-in var(--duration-slow) var(--ease-glide) backwards" in body
    assert "animation-delay: calc(var(--i, 0) * 20ms)" in body
    # nothing hides a row outside the animation: with JS off the class never
    # comes off, and the rows must still end visible
    base = re.search(r"(?m)^tbody tr\.leaderboard-row[^{]*\{([^}]*)\}", css)
    assert not base or "opacity: 0" not in base.group(1)
    html = (_TPL / "index.html.j2").read_text()
    assert re.search(r'<html lang="en" class="first-paint">', html)
    assert "classList.remove('first-paint')" in html
    # the top ten rows carry their index; later rows carry none
    assert re.search(r"\{% if loop\.index0 < 10 %\} style=\"--i:\{\{ loop\.index0 \}\}\"\{% endif %\}", html)
