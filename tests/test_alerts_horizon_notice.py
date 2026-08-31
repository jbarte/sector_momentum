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
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_FOOTER = _ROOT / "dashboard" / "templates" / "_footer.html.j2"
_TPL = _ROOT / "dashboard" / "templates"
_SV = _TPL / "i18n" / "_core.js.j2"

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _call_args(text: str, name: str) -> list[str]:
    """Raw argument text of each `name(...)` call in `text`, matched by
    tracking paren depth rather than stopping at the first `)` — a naive
    `[^)]*` regex truncates on a nested call like `universe_size=len(latest)`."""
    calls = []
    start = 0
    while True:
        idx = text.find(name + "(", start)
        if idx == -1:
            break
        open_idx = idx + len(name)
        depth = 0
        end = open_idx
        for end in range(open_idx, len(text)):
            if text[end] == "(":
                depth += 1
            elif text[end] == ")":
                depth -= 1
                if depth == 0:
                    break
        calls.append(text[open_idx + 1:end])
        start = end + 1
    return calls


def _top_level_args(args: str) -> list[str]:
    """Split a call's argument text on commas, ignoring commas nested inside
    parens (e.g. the `latest` in `universe_size=len(latest)`)."""
    parts, depth, current = [], 0, ""
    for ch in args:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def test_alerts_really_do_use_the_default_horizon():
    """The premise the notice asserts. If `detect_badge_events` ever passes an
    explicit horizon, the modal starts lying and this must fail.

    `_compute_setup`'s row-dict arg is always first; `universe_size=...` is a
    required keyword arg, not a horizon override, so it doesn't trip this —
    only a second bare *positional* arg (an explicit horizon) should.
    """
    alerts = (_ROOT / "src" / "alerts.py").read_text()
    calls = _call_args(alerts, "_compute_setup")
    assert calls, "no _compute_setup call found in src/alerts.py — did it move?"
    for args in calls:
        extra_positional = [p for p in _top_level_args(args)[1:] if "=" not in p]
        assert not extra_positional, (
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


def _alerts_modal_script() -> str:
    """The alerts modal's own `<script>...</script>` body (the one containing
    `renderHorizonNote`), with its two Jinja interpolations swapped for literal
    JSON — everything else in the block is plain JS, no other template
    expressions appear in it.

    Extracted by tag position rather than a full Jinja render: the surrounding
    partial needs a large, unrelated context (health, auth, alert-prefs) just
    to render at all, none of which this test cares about.
    """
    text = _FOOTER.read_text()
    script = text.split("<script>", 2)[1].split("</script>", 1)[0]
    assert "renderHorizonNote" in script, (
        "the alerts modal's <script> block moved or was restructured -- "
        "update the extraction to match"
    )
    # Strip Jinja comments (`{#- ... -#}`) — plain JS otherwise, but these
    # aren't valid JS syntax and node would choke on them.
    script = re.sub(r"\{#.*?#\}", "", script, flags=re.S)

    alert_hz = {
        "key": "medium", "label": "Medium", "top_n": 4, "buffer_frac": 0.15,
        "exit_rank_today": 7,
    }
    all_hz = [
        alert_hz,
        {"key": "long", "label": "Long", "top_n": 4, "buffer_frac": 0.3,
         "exit_rank_today": 10},
    ]
    script = script.replace(
        "{{ horizon_default_json | js_json }}", json.dumps(alert_hz))
    script = script.replace(
        "{{ horizons_json | js_json }}", json.dumps(all_hz))
    assert "{{" not in script, "unresolved Jinja expression left in the script"
    return script


@_needs_node
def test_renderhorizonnote_shows_real_numbers_not_nan():
    """Executes the actual `renderHorizonNote()` under node with a minimal DOM
    stub, a saved `sm_horizon` selection that disagrees with the alerts
    default, and asserts the two exit-rank nodes it fills in are real numbers.

    The existing tests in this file only check markup strings -- none of them
    ever ran this script, which is exactly how `ALERT_HZ.top_n +
    ALERT_HZ.buffer` (reading a field this branch renamed to `buffer_frac`)
    shipped: both nodes silently rendered "NaN" and nothing caught it.
    """
    script = _alerts_modal_script()
    node_script = f"""
    var textStore = {{}};
    var hiddenStore = {{}};
    function makeEl(id) {{
      var el = {{}};
      Object.defineProperty(el, "textContent", {{
        get: function () {{ return textStore[id]; }},
        set: function (v) {{ textStore[id] = v; }}
      }});
      Object.defineProperty(el, "hidden", {{
        get: function () {{ return hiddenStore[id]; }},
        set: function (v) {{ hiddenStore[id] = v; }}
      }});
      return el;
    }}
    var ids = ["alerts-modal", "alerts-close", "alerts-hz-agree", "alerts-hz-differ",
      "alerts-hz-sel", "alerts-hz-alert", "alerts-hz-alert-exit", "alerts-hz-sel-exit",
      "alerts-hz-alert-top", "alerts-hz-sel-top", "alerts-hz-name"];
    var elements = {{}};
    ids.forEach(function (id) {{ elements[id] = makeEl(id); }});

    global.document = {{
      readyState: "complete",
      getElementById: function (id) {{ return elements[id] || null; }},
      addEventListener: function () {{}}
    }};
    global.localStorage = {{
      getItem: function (k) {{ return k === "sm_horizon" ? "long" : null; }}
    }};
    global.window = {{
      SMModal: {{ bind: function () {{ return {{ open: function () {{}} }}; }} }}
    }};

    {script}

    process.stdout.write(JSON.stringify({{
      alertExit: elements["alerts-hz-alert-exit"].textContent,
      selExit: elements["alerts-hz-sel-exit"].textContent
    }}));
    """
    res = subprocess.run(
        ["node", "-e", node_script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout)
    assert out["alertExit"] == 7, f"alerts-hz-alert-exit rendered {out['alertExit']!r}, not 7"
    assert out["selExit"] == 10, f"alerts-hz-sel-exit rendered {out['selExit']!r}, not 10"
