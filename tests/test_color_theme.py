"""Dark theme: the data-theme attribute, its flash-prevention script, the
manual control, and (in later tasks) the token layer and chart substitution.
"""
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_INIT = _PROJECT_ROOT / "dashboard" / "templates" / "_theme_init.html.j2"


# --- WCAG relative luminance / contrast ratio helper (pure Python, no deps) ---
# Reusable for any future test that needs to check rendered text-on-background
# contrast rather than just the absence of hardcoded colours.

def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _srgb_channel_to_linear(c: float) -> float:
    c = c / 255.0
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    r, g, b = (_srgb_channel_to_linear(c) for c in _hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two colours, order-independent."""
    l1 = _relative_luminance(hex_a)
    l2 = _relative_luminance(hex_b)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _mix_over(fg_hex: str, fg_pct: float, bg_hex: str) -> str:
    """Approximate CSS `color-mix(in srgb, fg_hex fg_pct%, transparent)`
    composited over an opaque `bg_hex` container background: linear
    interpolation per channel in gamma-encoded sRGB space, weighted by
    fg_pct — matching what `color-mix(in srgb, ...)` does."""
    fg = _hex_to_rgb(fg_hex)
    bg = _hex_to_rgb(bg_hex)
    w = fg_pct / 100.0
    mixed = tuple(fg[i] * w + bg[i] * (1 - w) for i in range(3))
    return "#%02X%02X%02X" % tuple(round(c) for c in mixed)


def test_theme_init_partial_exists_and_is_synchronous():
    """No defer/async — the whole point is running before first paint."""
    src = _INIT.read_text()
    assert "<script" in src
    assert "defer" not in src
    assert "async" not in src
    assert "localStorage.getItem(\"theme\")" in src
    assert "setAttribute(\"data-theme\"" in src


def _head_prefix(template_name: str, n_lines: int = 3) -> str:
    text = (_PROJECT_ROOT / "dashboard" / "templates" / template_name).read_text()
    lines = text.splitlines()
    head_idx = next(i for i, l in enumerate(lines) if "<head>" in l)
    return "\n".join(lines[head_idx + 1: head_idx + 1 + n_lines])


def test_theme_init_is_first_thing_in_head_on_both_pages():
    """Neither page has a <link rel="stylesheet"> to anchor against — CSS is
    inlined directly into <head> via {% include "_style.html.j2" %}. So
    "before the styles" means literally first, ahead of <title>, the
    plotly/rescore <script src> tags, and the inline <style> block. A script
    moved even one line down (e.g. below <title>) reintroduces the flash for
    anyone on a manual override that disagrees with their OS."""
    for tpl in ("index.html.j2", "sentiment.html.j2"):
        prefix = _head_prefix(tpl, n_lines=2)
        assert '{% include "_theme_init.html.j2" %}' in prefix, (
            f"{tpl}: _theme_init.html.j2 is not the first include in <head>"
        )


_HEADER = _PROJECT_ROOT / "dashboard" / "templates" / "_header.html.j2"


def test_theme_control_markup():
    src = _HEADER.read_text()
    assert 'class="theme-toggle"' in src
    assert 'role="group"' in src
    assert 'aria-label="Colour theme"' in src
    for choice in ("auto", "light", "dark"):
        assert f'data-theme-choice="{choice}"' in src
    # role="tablist" already suppresses the page's only navigation landmark
    # (2026-08-09 audit) — this control must not repeat that mistake.
    assert 'role="tablist"' not in src.split('class="theme-toggle"')[1][:400]


import json
import shutil
import subprocess

_THEME_JS = _PROJECT_ROOT / "dashboard" / "assets" / "theme.js"

pytestmark_node = __import__("pytest").mark.skipif(
    shutil.which("node") is None, reason="node not available")


def _node_eval(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return out.stdout


@pytestmark_node
def test_get_resolves_attribute_before_media_query():
    """A DOM-free smoke test: SMTheme.get() must read data-theme first and
    only fall back to the media query when the attribute is absent. Since
    this file has no real DOM under plain Node, this test stubs the two
    inputs SMTheme.get() reads and asserts the precedence via the exported
    pure resolver, not the full window-attached function (see Step 13 for why
    the resolver is split out as its own testable piece)."""
    script = f"""
      const api = require({json.dumps(str(_THEME_JS))});
      // explicit attribute wins over a disagreeing media query
      console.log(api.resolveTheme("dark", false));   // -> "dark"
      console.log(api.resolveTheme("light", true));    // -> "light"
      // no attribute -> media query decides
      console.log(api.resolveTheme(null, true));        // -> "dark"
      console.log(api.resolveTheme(null, false));       // -> "light"
    """
    out = _node_eval(script).strip().splitlines()
    assert out == ["dark", "light", "dark", "light"]


@pytestmark_node
def test_control_reflects_stored_choice_on_load():
    """Spec's testing table lists this explicitly: given each of
    localStorage["theme"] unset / "light" / "dark", the rendered
    aria-pressed states must match. pressedStateFor is the pure piece of
    that — updateControlUI/initControl just apply its result to the DOM,
    which is why this is checkable under plain Node rather than only by
    reloading a real browser three times."""
    script = f"""
      const api = require({json.dumps(str(_THEME_JS))});
      console.log(JSON.stringify(api.pressedStateFor(undefined)));  // unset -> auto
      console.log(JSON.stringify(api.pressedStateFor("auto")));
      console.log(JSON.stringify(api.pressedStateFor("light")));
      console.log(JSON.stringify(api.pressedStateFor("dark")));
      console.log(JSON.stringify(api.pressedStateFor("garbage")));  // unknown -> auto
    """
    out = [json.loads(l) for l in _node_eval(script).strip().splitlines()]
    assert out[0] == {"auto": True, "light": False, "dark": False}
    assert out[1] == {"auto": True, "light": False, "dark": False}
    assert out[2] == {"auto": False, "light": True, "dark": False}
    assert out[3] == {"auto": False, "light": False, "dark": True}
    assert out[4] == {"auto": True, "light": False, "dark": False}


import re

_FOUNDATION = _PROJECT_ROOT / "dashboard" / "templates" / "css" / "_foundation.css.j2"

_SEMANTIC_TOKENS = [
    "--canvas", "--surface", "--bg-raised", "--bg-sunken",
    "--fg1", "--fg2", "--fg3", "--fg4",
    "--border", "--border-soft", "--up", "--down", "--brand-strong",
    "--ok", "--warn", "--err",
]


def _block(text: str, start_marker: str) -> str:
    """Extract one `{ ... }` block's body, matched by brace depth so nested
    color-mix()/rgba() parens don't confuse the boundary."""
    start = text.index(start_marker) + len(start_marker)
    depth = 1
    i = start
    while depth:
        if text[i] == "{": depth += 1
        elif text[i] == "}": depth -= 1
        i += 1
    return text[start:i - 1]


def _tokens_defined_in(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


def test_dark_tokens_are_three_way_consistent():
    """:root (light), [data-theme="dark"], and the @media fallback must all
    define exactly the same semantic tokens with exactly the same values —
    this is the manual-toggle-specific risk the spec calls out: an explicit
    Dark choice and a dark OS with no override must render identically."""
    text = _FOUNDATION.read_text()
    light = _block(text, ":root {")
    dark_attr = _block(text, ':root[data-theme="dark"] {')
    media_start = text.index("@media (prefers-color-scheme: dark)")
    dark_media = _block(text[media_start:], "{")
    # Strip one more layer — the media block wraps a nested selector block.
    dark_media = _block(dark_media, "{")

    for name in _SEMANTIC_TOKENS:
        assert name in light, f"{name} missing from :root"
        assert name in dark_attr, f"{name} missing from [data-theme=dark]"
        assert name in dark_media, f"{name} missing from the @media fallback"

    def _value(block, name):
        m = re.search(re.escape(name) + r"\s*:\s*([^;]+);", block)
        return m.group(1).strip() if m else None

    for name in _SEMANTIC_TOKENS:
        v_attr = _value(dark_attr, name)
        v_media = _value(dark_media, name)
        assert v_attr == v_media, (
            f"{name}: [data-theme=dark] says {v_attr!r}, "
            f"@media fallback says {v_media!r} — they must match exactly"
        )


def test_raw_ramps_untouched_in_dark_blocks():
    """A dark theme that reassigns --green-600 breaks every consumer that
    meant 'the brand green', not 'the foreground colour'."""
    text = _FOUNDATION.read_text()
    dark_attr = _block(text, ':root[data-theme="dark"] {')
    media_start = text.index("@media (prefers-color-scheme: dark)")
    dark_media = _block(_block(text[media_start:], "{"), "{")
    for block, name in ((dark_attr, "[data-theme=dark]"), (dark_media, "@media")):
        for prefix in ("--beige-", "--green-", "--terra-"):
            assert prefix not in block, f"{name} redefines a raw ramp ({prefix}*)"


_CSS_DIR = _PROJECT_ROOT / "dashboard" / "templates" / "css"
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
_RAW_RAMP_RE = re.compile(r"var\(--(?:beige|green|terra)-\d")


def _css_files_excluding_foundation():
    return [p for p in _CSS_DIR.glob("*.j2") if p.name != "_foundation.css.j2"]


def test_no_hardcoded_hex_outside_foundation():
    for path in _css_files_excluding_foundation():
        text = path.read_text()
        hits = _HEX_RE.findall(text)
        assert not hits, f"{path.name} has hardcoded hex: {hits}"


def test_no_raw_ramp_references_outside_foundation():
    """A component that reads --beige-200 directly instead of --bg-sunken
    (its semantic alias) will not theme — the raw ramps are fixed by design
    (test_raw_ramps_untouched_in_dark_blocks), so anything reading them
    directly is stuck in the light palette forever."""
    for path in _css_files_excluding_foundation():
        text = path.read_text()
        hits = _RAW_RAMP_RE.findall(text)
        assert not hits, f"{path.name} references a raw ramp directly: {hits}"


# Hardcoded hex values for the tokens `.rank-badge.top3` resolves against, in
# both themes. These MUST be kept in sync with `_foundation.css.j2` if
# `--bg-raised` or `--up` ever change — a CSS-parsing test would track the
# source of truth automatically and be more robust, but is out of scope for
# this one-line fix.
_BG_RAISED_LIGHT = "#FAF7F0"
_BG_RAISED_DARK = "#262218"
_UP_LIGHT = "#5A6F49"
_UP_DARK = "#A9C48E"

def _rank_badge_top3_tint_pct() -> int:
    """`.rank-badge.top3`'s tint percentage in the color-mix idiom, read
    straight from the source CSS so this test tracks the real rule instead
    of a copy-pasted number:
      background: color-mix(in srgb, var(--up) <PCT>%, transparent);
      color: var(--up);
    `.rank-badge` sits inside `.table-wrap`, which sets
    `background: var(--bg-raised)` with no intervening tbody/tr/td
    background override, so the tint composites against `--bg-raised` in
    practice."""
    text = _CSS_DIR.joinpath("_tables.css.j2").read_text()
    block = _block(text, ".rank-badge.top3 {")
    m = re.search(r"color-mix\(in srgb, var\(--up\) (\d+)%, transparent\)", block)
    assert m, ".rank-badge.top3 background is not the expected color-mix(var(--up) N%) idiom"
    return int(m.group(1))


def test_rank_badge_top3_contrast_meets_wcag_aa():
    """`.rank-badge.top3` text (var(--up), opaque) on its tinted background
    (color-mix(in srgb, var(--up) N%, transparent) composited over the
    table's real container background, --bg-raised) must clear the 4.5:1
    WCAG AA minimum for normal-size text, in both themes."""
    pct = _rank_badge_top3_tint_pct()
    light_bg = _mix_over(_UP_LIGHT, pct, _BG_RAISED_LIGHT)
    dark_bg = _mix_over(_UP_DARK, pct, _BG_RAISED_DARK)

    light_ratio = _contrast_ratio(_UP_LIGHT, light_bg)
    dark_ratio = _contrast_ratio(_UP_DARK, dark_bg)

    assert light_ratio >= 4.5, (
        f".rank-badge.top3 light-theme contrast is {light_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )
    assert dark_ratio >= 4.5, (
        f".rank-badge.top3 dark-theme contrast is {dark_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )


_ILLOS = [
    _PROJECT_ROOT / "dashboard" / "templates" / "_rotation_illo.html.j2",
    _PROJECT_ROOT / "dashboard" / "templates" / "_guide_illo.html.j2",
]
_SVG_HEX_ATTR_RE = re.compile(r'(?:fill|stroke)="#[0-9A-Fa-f]{3,8}"')


def test_illustrations_have_no_hardcoded_fill_or_stroke():
    for path in _ILLOS:
        hits = _SVG_HEX_ATTR_RE.findall(path.read_text())
        assert not hits, f"{path.name} has hardcoded fill/stroke: {hits}"
