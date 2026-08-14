"""Alerts state which band they run on, and warn when it isn't yours.

Alerts evaluate the CONFIG DEFAULT horizon: `detect_badge_events` calls
`_compute_setup(row)` with no horizon argument, and that falls back to
`default_horizon()`. The selector's choice lives in `localStorage` and never
reaches the scan — so switching horizon changes the board without changing the
inbox, silently.

Running Medium (the configured default) there is no mismatch at all, which is
why this ships as a notice rather than the per-user horizon column the backlog
originally proposed: that is multi-user machinery for one user, on the one code
path where a bug means a missed or spurious email.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_FOOTER = _ROOT / "dashboard" / "templates" / "_footer.html.j2"
_TPL = _ROOT / "dashboard" / "templates"
_SV = _TPL / "i18n" / "_core.js.j2"


def test_alerts_really_do_use_the_default_horizon():
    """The premise the notice asserts. If `detect_badge_events` ever passes an
    explicit horizon, the modal starts lying and this must fail."""
    alerts = (_ROOT / "src" / "alerts.py").read_text()
    calls = re.findall(r"_compute_setup\(([^)]*)\)", alerts)
    assert calls, "no _compute_setup call found in src/alerts.py — did it move?"
    for args in calls:
        assert "," not in args, (
            f"_compute_setup({args}) passes an explicit horizon; alerts no longer "
            "run on the config default and the alerts modal's claim is now false"
        )


def test_both_pages_can_render_the_notice():
    """The footer is shared, so both page contexts need the horizon blobs. The
    sentiment page had neither before this."""
    build = (_ROOT / "dashboard" / "build.py").read_text()
    assert build.count('"horizons_json": _horizons_json') == 2
    assert build.count('"horizon_default_json": _horizon_default_json') == 2


def test_the_notice_has_both_states():
    text = _FOOTER.read_text()
    assert 'id="alerts-hz-agree"' in text
    assert 'id="alerts-hz-differ"' in text
    # Both start hidden; script decides which to show.
    for el in ("alerts-hz-agree", "alerts-hz-differ"):
        tag = re.search(rf'<p[^>]*id="{el}"[^>]*>', text).group(0)
        assert " hidden" in tag


def test_numbers_live_in_their_own_nodes():
    """Interpolating a whole sentence would be wiped on the first language
    switch — applyLang() rewrites textContent from the SV bundle. The words
    carry data-i18n; the figures get their own elements, the same shape
    renderHorizonStats() uses."""
    text = _FOOTER.read_text()
    for node in ("alerts-hz-alert-exit", "alerts-hz-sel-exit",
                 "alerts-hz-alert-top", "alerts-hz-sel-top"):
        assert f'id="{node}"' in text
    assert "textContent = v" in text


def test_the_notice_is_recomputed_on_open():
    """The reader can switch horizon and open the modal without a reload."""
    text = _FOOTER.read_text()
    body = text[text.index("function bindTrigger"):]
    body = body[:body.index("\n  }")]          # the function's own body
    assert 'addEventListener("click"' in body
    assert "renderHorizonNote()" in body


def test_swedish_has_every_fragment():
    sv = _SV.read_text()
    text = _FOOTER.read_text()
    keys = set(re.findall(r'data-i18n="(alerts_hz_[a-z0-9_]+)"', text))
    assert keys, "no alerts_hz_* fragments found in the footer"
    missing = sorted(k for k in keys if f"{k}:" not in sv)
    assert not missing, f"Swedish is missing: {missing}"


def test_no_jinja_comment_leaks_into_the_rendered_page():
    """A Jinja comment containing the literal close-delimiter terminates early,
    and everything after it renders as visible text. That happened here: a
    comment explaining whitespace control quoted the delimiters and leaked a
    sentence into the alerts modal.

    Scans every template rather than just this one — the failure is invisible in
    source review and looks like prose in the output.
    """
    offenders = []
    for path in _TPL.rglob("*.j2"):
        for body in re.findall(r"\{#(.*?)#\}", path.read_text(), flags=re.S):
            if "#}" in body or "{#" in body:
                offenders.append(path.name)
    assert not offenders, (
        f"Jinja comments containing comment delimiters (they close early and "
        f"leak the remainder as page text): {sorted(set(offenders))}"
    )
