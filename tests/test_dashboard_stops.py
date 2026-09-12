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


def test_chip_and_bar_go_in_the_stop_cell_never_the_theme_cell():
    """Both markers moved out of the theme cell into the dedicated .stop-cell
    column (2026-09-12) so the bars align and become comparable across rows.

    This still pins the older invariant it replaces: .theme-name holds only a
    single text node -- renderReviewPanel()'s nameOf(), the mobile card
    projection and the band-cut summary strip all read its
    textContent/innerHTML directly and corrupt if anything is nested inside
    it. Targeting a different cell entirely satisfies that trivially, so the
    assertions below pin the NEW target plus the absence of the two old
    append shapes, rather than dropping the guard with the code that needed
    it."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert 'tr.querySelector(".stop-cell")' in js
    # No fallback into the name cell: a bar in the name cell on some rows and
    # in the column on others is worse than it being absent.
    assert "tr.cells[1]" not in js
    assert 'nameSpan.parentNode' not in js
    assert 'tr.querySelector(".theme-name").appendChild' not in js
    # The original bug's exact shape, kept pinned.
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


def test_marker_reads_the_distance_table_too_and_never_writes_it():
    js = Path("dashboard/assets/stops.js").read_text()
    assert 'from("position_stop_distance")' in js
    for forbidden in (".insert(", ".update(", ".upsert(", ".delete("):
        assert forbidden not in js


def test_breach_chip_takes_precedence_over_the_distance_bar_in_source_order():
    """decorate() must check for an existing breach (`stop`) BEFORE it ever
    calls into the bar-build path, and return before reaching it -- the chip
    always wins when a row has both. Anchored on the CALL site
    (`buildDistanceEl(distance)`), not the function's own definition (which
    is declared earlier in the file, before decorate() -- a plain substring
    search for "buildDistanceEl(" would match that definition first and
    give a false pass here). A source-order check rather than a live-DOM
    run, matching this file's existing convention for stops.js's
    higher-level behaviour (see the other tests in this file)."""
    js = Path("dashboard/assets/stops.js").read_text()
    chip_idx = js.index("if (stop) {")
    bar_call_idx = js.index("buildDistanceEl(distance)")
    assert chip_idx < bar_call_idx


def test_a_single_query_failure_does_not_reject_the_other():
    """Promise.all rejects (and skips BOTH results) the moment either input
    promise rejects -- a bare Promise.all over the two raw Supabase calls
    would mean a missing position_stop_distance table (e.g. mid-deploy,
    before the migration runs) could silently kill the EXISTING breach chip
    too. Each query's own promise must be caught and normalized (via
    safeQuery) BEFORE reaching Promise.all -- checked here by confirming both
    array entries are wrapped in a safeQuery(...) call, not a bare
    sb.from(...).select(...).

    This only pins the STRUCTURE (safeQuery wraps both queries); it does NOT
    prove safeQuery actually survives a rejection. A prior version of this
    test additionally asserted the literal substring "promise.catch(" was
    present -- which passed even though `.catch` does not exist on the real
    Supabase query builder (a bare thenable, not a Promise) and calling it
    there threw SYNCHRONOUSLY, disabling both queries (and the pre-existing
    breach chip with them) on every page load. The behavioral proof that
    safeQuery survives a real rejecting, catch-less thenable lives in
    tests/test_dashboard_js.py::test_safe_query_lets_the_other_query_succeed_when_one_rejects,
    which drives the real load() path under node against a stub builder that
    mimics the catch-less shape."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert "function safeQuery(promise)" in js
    all_call = js[js.index("Promise.all(["):js.index("]).then(")]
    assert all_call.count("safeQuery(") == 2


def test_tooltip_never_shows_nan_when_the_stop_frac_config_is_missing():
    """window.SM_TRAILING_STOP_FRAC can be absent on a broken deploy (Task 3's
    contract, not this file's own guarantee). computeProximity() already
    guards this defensively (returns 0 when stopFrac is falsy), but the
    tooltip text built directly from `Math.round(100 * stopFrac)` did not --
    an absent config would render "...closes NaN% below peak." Pinned as a
    source check: an isFinite-style guard must exist so the sentence naming
    the stop threshold is OMITTED rather than ever showing "NaN"."""
    js = Path("dashboard/assets/stops.js").read_text()
    build_fn = js[js.index("function buildDistanceEl"):js.index("function decorate(")]
    assert "isFinite(stopPct)" in build_fn
    # The sentence naming the stop threshold must be built inside a guard
    # keyed on that isFinite check, not concatenated unconditionally --
    # otherwise the guard variable exists but nothing actually uses it.
    assert "hasStopPct ?" in build_fn
    assert 'wrap.title = "Currently "' in build_fn


def test_bar_is_appended_beside_theme_name_not_inside_it():
    """Same invariant test_chip_is_appended_beside_theme_name_not_inside_it
    pins for the chip -- .theme-name must keep holding a single text node."""
    js = Path("dashboard/assets/stops.js").read_text()
    assert "cell.appendChild(bar)" in js


def test_distance_row_gets_a_scoped_translate_call_after_creation():
    js = Path("dashboard/assets/stops.js").read_text()
    assert "applyLangToEl(bar)" in js


def test_the_displayed_label_is_drawdown_not_proximity():
    """Load-bearing per the design spec: showing the PROPORTION as the
    number would make it visibly DROP (e.g. 78% -> 12%) at the exact instant
    a position breaches -- the number must stay in the same unit
    (drawdown-from-peak) the breach chip already uses, throughout. Pinned by
    checking `pct` (drawdown-derived) feeds the label's textContent, while
    `p` (proximity-derived, from computeProximity) feeds only the fill's
    width -- never the other way around."""
    js = Path("dashboard/assets/stops.js").read_text()
    build_fn = js[js.index("function buildDistanceEl"):js.index("function decorate(")]
    assert 'label.textContent = "-" + pct + "%";' in build_fn
    assert "fill.style.width = Math.round(p * 100)" in build_fn


def test_tooltip_names_the_as_of_date():
    """A daily reading, not a live gauge -- the tooltip must say which day
    it's from, the way the breach chip's own tooltip already names
    stopped_on, or a stale bar after a failed scan reads as current."""
    js = Path("dashboard/assets/stops.js").read_text()
    build_fn = js[js.index("function buildDistanceEl"):js.index("function decorate(")]
    assert "row.as_of" in build_fn
