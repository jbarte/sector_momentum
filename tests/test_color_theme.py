"""Dark theme: the data-theme attribute, its flash-prevention script, the
manual control, and (in later tasks) the token layer and chart substitution.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard.figures import (
    build_chart_dark_map, _WARM_PALETTE, _SCORE_SIGNAL_COLORS, _base_layout,
    _build_rrg_figure, _build_sentiment_scatter_figure, _build_movers_figure,
    _build_history_figure, _build_drilldown_data, _build_backtest_figures,
)
from dashboard.correlation import _build_heatmap_figure, _order_labels

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
    """No defer/async on the <script> tag itself — the whole point is running
    before first paint. Scoped to just the opening tag (not the whole file,
    which also contains an explanatory comment free to use those words in
    plain English) — see the comment above the tag in _theme_init.html.j2."""
    src = _INIT.read_text()
    tag_match = re.search(r"<script[^>]*>", src)
    assert tag_match, "no <script> tag found in _theme_init.html.j2"
    tag = tag_match.group(0)
    assert "defer" not in tag
    assert "async" not in tag
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


_FOUNDATION = _PROJECT_ROOT / "dashboard" / "templates" / "css" / "_foundation.css.j2"

_SEMANTIC_TOKENS = [
    "--canvas", "--surface", "--bg-raised", "--bg-sunken",
    "--fg1", "--fg2", "--fg3", "--fg4", "--fg5",
    "--border", "--border-soft", "--border-hair",
    "--up", "--down", "--up-ink", "--down-ink", "--zero-line", "--brand-strong",
    "--ok", "--warn", "--err",
    "--shadow-xs", "--shadow-sm", "--shadow-card",
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


# Hardcoded hex values for the tokens `.rank-badge.in-buy-band` resolves against, in
# both themes. These MUST be kept in sync with `_foundation.css.j2` if
# `--bg-raised` or `--up` ever change — a CSS-parsing test would track the
# source of truth automatically and be more robust, but is out of scope for
# this one-line fix.
_BG_RAISED_LIGHT = "#FAF7F0"
_BG_RAISED_DARK = "#262218"
_UP_LIGHT = "#5A6F49"
_UP_DARK = "#A9C48E"

def _rank_badge_in_buy_band_tint_pct() -> int:
    """`.rank-badge.in-buy-band`'s tint percentage in the color-mix idiom, read
    straight from the source CSS so this test tracks the real rule instead
    of a copy-pasted number:
      background: color-mix(in srgb, var(--up) <PCT>%, transparent);
      color: var(--up);
    `.rank-badge` sits inside `.table-wrap`, which sets
    `background: var(--bg-raised)` with no intervening tbody/tr/td
    background override, so the tint composites against `--bg-raised` in
    practice."""
    text = _CSS_DIR.joinpath("_tables.css.j2").read_text()
    block = _block(text, ".rank-badge.in-buy-band {")
    m = re.search(r"color-mix\(in srgb, var\(--up\) (\d+)%, transparent\)", block)
    assert m, ".rank-badge.in-buy-band background is not the expected color-mix(var(--up) N%) idiom"
    return int(m.group(1))


def test_rank_badge_in_buy_band_contrast_meets_wcag_aa():
    """`.rank-badge.in-buy-band` text (var(--up), opaque) on its tinted background
    (color-mix(in srgb, var(--up) N%, transparent) composited over the
    table's real container background, --bg-raised) must clear the 4.5:1
    WCAG AA minimum for normal-size text, in both themes."""
    pct = _rank_badge_in_buy_band_tint_pct()
    light_bg = _mix_over(_UP_LIGHT, pct, _BG_RAISED_LIGHT)
    dark_bg = _mix_over(_UP_DARK, pct, _BG_RAISED_DARK)

    light_ratio = _contrast_ratio(_UP_LIGHT, light_bg)
    dark_ratio = _contrast_ratio(_UP_DARK, dark_bg)

    assert light_ratio >= 4.5, (
        f".rank-badge.in-buy-band light-theme contrast is {light_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )
    assert dark_ratio >= 4.5, (
        f".rank-badge.in-buy-band dark-theme contrast is {dark_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )


# Hardcoded hex values for `--fg4`, in both themes. Same trade-off as
# _UP_LIGHT/_UP_DARK above: must be kept in sync with _foundation.css.j2 by
# hand if --fg4 or --bg-raised ever change.
_FG4_LIGHT = "#6F674F"
_FG4_DARK = "#A59A80"


def _traj_flat_tint_pct() -> int:
    """`.traj-badge.traj-flat`'s tint percentage in the color-mix idiom, read
    from the source CSS rather than copy-pasted, mirroring
    `_rank_badge_in_buy_band_tint_pct`:
      color: var(--fg4);
      background: color-mix(in srgb, var(--fg4) <PCT>%, transparent);
    Sits in the same `.table-wrap` (`--bg-raised`) container as
    `.rank-badge.in-buy-band`, so the tint composites against `--bg-raised` too."""
    text = _CSS_DIR.joinpath("_tables.css.j2").read_text()
    m = re.search(
        r"\.traj-badge\.traj-flat\s*\{[^}]*color-mix\(in srgb, var\(--fg4\) (\d+)%, transparent\)",
        text,
    )
    assert m, ".traj-badge.traj-flat background is not the expected color-mix(var(--fg4) N%) idiom"
    return int(m.group(1))


def test_traj_flat_contrast_meets_wcag_aa():
    """`.traj-badge.traj-flat` text (var(--fg4), opaque) on its tinted
    background (color-mix(in srgb, var(--fg4) N%, transparent) composited
    over --bg-raised) must clear the 4.5:1 WCAG AA minimum in both themes.
    Regression guard for the dark-theme failure (4.33:1) Task 9's
    verification pass found and fixed by brightening dark --fg4."""
    pct = _traj_flat_tint_pct()
    light_bg = _mix_over(_FG4_LIGHT, pct, _BG_RAISED_LIGHT)
    dark_bg = _mix_over(_FG4_DARK, pct, _BG_RAISED_DARK)

    light_ratio = _contrast_ratio(_FG4_LIGHT, light_bg)
    dark_ratio = _contrast_ratio(_FG4_DARK, dark_bg)

    assert light_ratio >= 4.5, (
        f".traj-badge.traj-flat light-theme contrast is {light_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )
    assert dark_ratio >= 4.5, (
        f".traj-badge.traj-flat dark-theme contrast is {dark_ratio:.2f}:1 "
        f"at {pct}% tint, below the 4.5:1 AA minimum"
    )


# Hardcoded hex for `--fg5` and surface backgrounds, in both themes.
# Must be kept in sync with _foundation.css.j2 if --fg5, --surface, or
# --bg-raised ever change.
_FG5_LIGHT = "#8A8068"
_FG5_DARK = "#9E9179"
_SURFACE_DARK = "#201D15"


def test_fg5_contrast_meets_wcag_aa_dark_mode():
    """--fg5 dark-mode value (#9E9179, used for 10-11px eyebrow/micro-row labels)
    must clear the 4.5:1 WCAG AA minimum against both dark-mode backgrounds
    (--surface and --bg-raised). Regression guard for the dark-theme failure
    (3.52:1 with brief's candidate #7C7256) Task 1's verification pass found
    and fixed by brightening --fg5."""
    # Check against --surface (primary background)
    ratio_vs_surface = _contrast_ratio(_FG5_DARK, _SURFACE_DARK)
    assert ratio_vs_surface >= 4.5, (
        f"--fg5 ({_FG5_DARK}) on --surface ({_SURFACE_DARK}) is "
        f"{ratio_vs_surface:.2f}:1 in dark mode, below the 4.5:1 AA minimum"
    )

    # Check against --bg-raised (secondary/card background)
    ratio_vs_bg_raised = _contrast_ratio(_FG5_DARK, _BG_RAISED_DARK)
    assert ratio_vs_bg_raised >= 4.5, (
        f"--fg5 ({_FG5_DARK}) on --bg-raised ({_BG_RAISED_DARK}) is "
        f"{ratio_vs_bg_raised:.2f}:1 in dark mode, below the 4.5:1 AA minimum"
    )


# Hardcoded hex for `--down` (terra-500), in both themes — same trade-off as
# _UP_LIGHT/_UP_DARK above.
_DOWN_LIGHT = "#A55A3C"
_DOWN_DARK = "#D98E6B"


def _tint_pct(css_filename: str, selector: str, var_name: str) -> int:
    """Read a badge's tint percentage straight from its source CSS, for any
    rule shaped `color: var(<var_name>); background: color-mix(in srgb,
    var(<var_name>) <PCT>%, transparent);` — generalizes
    `_rank_badge_in_buy_band_tint_pct`/`_traj_flat_tint_pct` above across the five
    same-idiom badges the 2026-08-11 audit flagged, so each test tracks its
    real rule instead of a copy-pasted number."""
    text = _CSS_DIR.joinpath(css_filename).read_text()
    pattern = (
        # Non-greedy [^}]*?: some of these rules (setup-badge.entry/.exit)
        # have a second color-mix() for their border later in the same
        # block, and a greedy match would find that one instead of the
        # background's — the one the badge text actually renders against.
        re.escape(selector) + r"\s*\{[^}]*?background:\s*color-mix\(in srgb, var\("
        + re.escape(var_name) + r"\) (\d+)%, transparent\)"
    )
    m = re.search(pattern, text)
    assert m, f"{selector} background is not the expected color-mix(var({var_name}) N%) idiom"
    return int(m.group(1))


def _assert_badge_contrast(name: str, fg_light: str, fg_dark: str, pct: int) -> None:
    """Shared assertion body for the five same-idiom badge contrast tests
    below: text (fully opaque `fg_*`) on its own colour tinted over
    `--bg-raised`, in both themes."""
    light_bg = _mix_over(fg_light, pct, _BG_RAISED_LIGHT)
    dark_bg = _mix_over(fg_dark, pct, _BG_RAISED_DARK)
    light_ratio = _contrast_ratio(fg_light, light_bg)
    dark_ratio = _contrast_ratio(fg_dark, dark_bg)
    assert light_ratio >= 4.5, (
        f"{name} light-theme contrast is {light_ratio:.2f}:1 at {pct}% tint, "
        f"below the 4.5:1 AA minimum"
    )
    assert dark_ratio >= 4.5, (
        f"{name} dark-theme contrast is {dark_ratio:.2f}:1 at {pct}% tint, "
        f"below the 4.5:1 AA minimum"
    )


def test_traj_strong_up_contrast_meets_wcag_aa():
    """Regression guard for the pre-existing failure the 2026-08-11 audit
    found: `.traj-badge.traj-strong_up` at its original 15% tint was 4.26:1
    in light mode. Dropped to 10% — the largest step in the 8/10/12/15%
    family already used across these badges that still clears 4.5:1 in both
    themes."""
    pct = _tint_pct("_tables.css.j2", ".traj-badge.traj-strong_up", "--up")
    _assert_badge_contrast(".traj-badge.traj-strong_up", _UP_LIGHT, _UP_DARK, pct)


def test_setup_badge_entry_contrast_meets_wcag_aa():
    """Same pre-existing failure pattern as traj-strong_up (same `--up`
    colour, 12% tint, 4.43:1 light). Dropped to 10% to match."""
    pct = _tint_pct("_tables.css.j2", ".setup-badge.entry", "--up")
    _assert_badge_contrast(".setup-badge.entry", _UP_LIGHT, _UP_DARK, pct)


def test_traj_down_contrast_meets_wcag_aa():
    """`--down` (terra-500) needs a materially lower tint than `--up` for the
    same contrast target: 8% — already the smallest step in the
    8/10/12/15% family other badges share — still failed at 4.28:1 light, so
    this one needed a tint below any step already in use elsewhere rather
    than a swap among them."""
    pct = _tint_pct("_tables.css.j2", ".traj-badge.traj-down", "--down")
    _assert_badge_contrast(".traj-badge.traj-down", _DOWN_LIGHT, _DOWN_DARK, pct)


def test_traj_strong_down_contrast_meets_wcag_aa():
    """Same `--down` ceiling as traj-down above, at the badge's own original
    15% tint (3.90:1 light) rather than 8%."""
    pct = _tint_pct("_tables.css.j2", ".traj-badge.traj-strong_down", "--down")
    _assert_badge_contrast(".traj-badge.traj-strong_down", _DOWN_LIGHT, _DOWN_DARK, pct)


def test_setup_badge_exit_contrast_meets_wcag_aa():
    """Same `--down` ceiling again, at setup-badge.exit's original 12% tint
    (4.07:1 light)."""
    pct = _tint_pct("_tables.css.j2", ".setup-badge.exit", "--down")
    _assert_badge_contrast(".setup-badge.exit", _DOWN_LIGHT, _DOWN_DARK, pct)


def test_muted_badges_have_more_contrast_margin_than_active_ones():
    """`.muted` drops the tinted background to fully transparent (0% tint) —
    text renders straight on --bg-raised. Tinting toward the text's own
    colour always REDUCES contrast (established by every fix above: dropping
    tint pct raised the ratio each time), so 0% tint must beat every active
    tint pct already verified. Regression guard: if a future change gives
    `.muted` its own background colour instead of `transparent`, this is the
    test that would catch a contrast regression the active-state tests can't
    see, since they never exercise the muted rule at all."""
    assert _contrast_ratio(_UP_LIGHT, _BG_RAISED_LIGHT) >= 4.5
    assert _contrast_ratio(_UP_LIGHT, _BG_RAISED_LIGHT) > _contrast_ratio(
        _UP_LIGHT, _mix_over(_UP_LIGHT, _tint_pct("_tables.css.j2", ".setup-badge.entry", "--up"), _BG_RAISED_LIGHT))
    assert _contrast_ratio(_DOWN_LIGHT, _BG_RAISED_LIGHT) >= 4.5
    assert _contrast_ratio(_DOWN_LIGHT, _BG_RAISED_LIGHT) > _contrast_ratio(
        _DOWN_LIGHT, _mix_over(_DOWN_LIGHT, _tint_pct("_tables.css.j2", ".setup-badge.exit", "--down"), _BG_RAISED_LIGHT))
    text = _CSS_DIR.joinpath("_tables.css.j2").read_text()
    assert re.search(r"\.setup-badge\.entry\.muted,\s*\n\s*\.setup-badge\.exit\.muted\s*\{\s*background:\s*transparent;",
                      text), ".muted rule no longer sets background: transparent — update this test's assumption"


# Hardcoded hex for `--canvas`, in both themes — the container `--ok`/`--warn`/
# `--err` render directly against (`.health-panel` sets no background of its
# own, so it inherits `body`'s `--bg` == `--canvas`).
_CANVAS_LIGHT = "#f1ecdf"
_CANVAS_DARK = "#17150F"


def _status_token_hex(token: str, block_marker: str) -> str:
    """Read `--ok`/`--warn`/`--err`'s hex value out of a given `_foundation.css.j2`
    block (`:root {` for light, `:root[data-theme="dark"] {` for dark),
    tracking the source instead of a copy-pasted value."""
    text = _FOUNDATION.read_text()
    block = _block(text, block_marker)
    m = re.search(re.escape(token) + r":\s*(#[0-9A-Fa-f]{6})", block)
    assert m, f"{token} not found as a plain hex value in block starting {block_marker!r}"
    return m.group(1)


def test_status_tokens_contrast_meets_wcag_aa_in_light_mode():
    """`--ok`/`--warn` render as plain text directly on `--canvas` in
    `.badge-green`/`.badge-amber`/`health-warn-dot` (`_health.css.j2`) with no
    tint idiom involved, unlike the five badges above. The 2026-08-12 final
    whole-branch review of the dark theme found both under 4.5:1 in light
    mode (3.64:1, 3.72:1) — `--err` already passed (5.03:1) and is included
    here only as a regression guard, not a fix."""
    for token in ("--ok", "--warn", "--err"):
        hex_value = _status_token_hex(token, ":root {")
        ratio = _contrast_ratio(hex_value, _CANVAS_LIGHT)
        assert ratio >= 4.5, (
            f"{token} ({hex_value}) on --canvas is {ratio:.2f}:1 in light "
            f"mode, below the 4.5:1 AA minimum"
        )


def test_status_tokens_contrast_still_meets_wcag_aa_in_dark_mode():
    """The light-mode fix above must not regress dark mode, which already
    passed comfortably before this change."""
    for token in ("--ok", "--warn", "--err"):
        hex_value = _status_token_hex(token, ':root[data-theme="dark"] {')
        ratio = _contrast_ratio(hex_value, _CANVAS_DARK)
        assert ratio >= 4.5, (
            f"{token} ({hex_value}) on --canvas is {ratio:.2f}:1 in dark "
            f"mode, below the 4.5:1 AA minimum"
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


_INDEX_TPL = _PROJECT_ROOT / "dashboard" / "templates" / "index.html.j2"
_SENTIMENT_TPL = _PROJECT_ROOT / "dashboard" / "templates" / "sentiment.html.j2"


def test_no_bare_plotly_newplot_outside_smplot():
    """Four independent badge writers drifted before they were consolidated
    into one pass (2026-08-10). Seven independent Plotly.newPlot call sites
    are the same shape — this test is what stops an eighth chart, or a
    forgotten call site, from silently skipping the theme."""
    for path in (_INDEX_TPL, _SENTIMENT_TPL):
        text = path.read_text()
        assert "Plotly.newPlot" not in text, (
            f"{path.name} calls Plotly.newPlot directly — use SMTheme.smPlot instead"
        )


def test_chart_dark_map_covers_every_figure_constant():
    """Every literal hex used by a figure builder must have a dark
    equivalent, or that colour silently stays light-mode-only forever."""
    m = build_chart_dark_map()
    for hexval in _WARM_PALETTE:
        assert hexval in m, f"_WARM_PALETTE entry {hexval} has no dark mapping"
    for hexval in _SCORE_SIGNAL_COLORS.values():
        assert hexval in m, f"_SCORE_SIGNAL_COLORS entry {hexval} has no dark mapping"
    base = _base_layout()
    assert base["paper_bgcolor"] in m
    assert base["plot_bgcolor"] in m
    assert base["font"]["color"] in m
    assert base["legend"]["bgcolor"] in m
    assert base["legend"]["bordercolor"] in m


def test_chart_dark_map_values_are_all_hex():
    m = build_chart_dark_map()
    for light, dark in m.items():
        assert re.match(r"^#[0-9A-Fa-f]{6}$", light), f"key {light!r} is not hex"
        assert re.match(r"^#[0-9A-Fa-f]{6}$", dark), f"value {dark!r} is not hex"
        assert light != dark, f"{light} maps to itself — not actually themed"


def _recolor(fig: dict, dark_map: dict, is_dark: bool) -> dict:
    script = f"""
      const api = require({json.dumps(str(_THEME_JS))});
      process.stdout.write(JSON.stringify(api.recolor(
        {json.dumps(fig)}, {json.dumps(dark_map)}, {json.dumps(is_dark)})));
    """
    return json.loads(_node_eval(script))


@pytestmark_node
def test_recolor_leaves_light_theme_untouched():
    fig = {"data": [{"line": {"color": "#5A6F49"}}],
           "layout": {"paper_bgcolor": "#F5F0E6"}}
    out = _recolor(fig, {"#5A6F49": "#A9C48E", "#F5F0E6": "#2A2619"}, False)
    assert out == fig


@pytestmark_node
def test_recolor_substitutes_under_colour_bearing_keys():
    fig = {"data": [{"line": {"color": "#5A6F49"}, "marker": {"color": "#A55A3C"}}],
           "layout": {"paper_bgcolor": "#F5F0E6", "plot_bgcolor": "#FAF7F0",
                      "font": {"color": "#3E392B"},
                      "xaxis": {"gridcolor": "#DFD5BE"}}}
    dark_map = {"#5A6F49": "#A9C48E", "#A55A3C": "#D98E6B", "#F5F0E6": "#2A2619",
                "#FAF7F0": "#262218", "#3E392B": "#E4DDCC", "#DFD5BE": "#34301F"}
    out = _recolor(fig, dark_map, True)
    assert out["data"][0]["line"]["color"] == "#A9C48E"
    assert out["data"][0]["marker"]["color"] == "#D98E6B"
    assert out["layout"]["paper_bgcolor"] == "#2A2619"
    assert out["layout"]["plot_bgcolor"] == "#262218"
    assert out["layout"]["font"]["color"] == "#E4DDCC"
    assert out["layout"]["xaxis"]["gridcolor"] == "#34301F"


@pytestmark_node
def test_recolor_does_not_touch_non_colour_keys():
    """A hex string appearing as DATA (a hover label, a theme name) must
    survive untouched — only keys known to carry colour are walked. This is
    the exact risk the spec's Risks section calls out."""
    fig = {"data": [{"text": ["#5A6F49 is a great colour"],
                     "name": "#5A6F49",
                     "line": {"color": "#5A6F49"}}]}
    dark_map = {"#5A6F49": "#A9C48E"}
    out = _recolor(fig, dark_map, True)
    assert out["data"][0]["text"] == ["#5A6F49 is a great colour"]
    assert out["data"][0]["name"] == "#5A6F49"
    assert out["data"][0]["line"]["color"] == "#A9C48E"


@pytestmark_node
def test_recolor_handles_a_string_valued_line_or_marker():
    """Plotly sometimes accepts `marker_color: "#hex"` directly, or a list of
    colours (marker.color can be an array for per-point colouring)."""
    fig = {"data": [{"marker": {"color": ["#5A6F49", "#A55A3C", "#5A6F49"]}}]}
    dark_map = {"#5A6F49": "#A9C48E", "#A55A3C": "#D98E6B"}
    out = _recolor(fig, dark_map, True)
    assert out["data"][0]["marker"]["color"] == ["#A9C48E", "#D98E6B", "#A9C48E"]


@pytestmark_node
def test_recolor_leaves_unmapped_colours_and_named_colorscales_alone():
    """RdBu_r (the correlation heatmap's colorscale) is a Plotly built-in
    name, not a hex value — recolor must not choke on it or try to "fix" it.
    An unmapped hex also passes through unchanged rather than erroring."""
    fig = {"data": [{"colorscale": "RdBu_r", "marker": {"color": "#999999"}}]}
    out = _recolor(fig, {"#5A6F49": "#A9C48E"}, True)
    assert out["data"][0]["colorscale"] == "RdBu_r"
    assert out["data"][0]["marker"]["color"] == "#999999"


# ---------------------------------------------------------------------------
# Whole-figure survivor test — closes the gap that let Important #1 (the
# sentiment scatter's textfont labels shipping unthemed) get through 9 rounds
# of task review. test_chart_dark_map_covers_every_figure_constant above only
# checks a colour is IN the dark map; it never checks recolor() actually
# REACHES it inside a real baked figure. This builds all 7 real figure types
# with the actual Python builders (fixture patterns lifted from
# tests/test_dashboard_js.py, tests/test_dashboard_backtest.py, and
# tests/test_correlation.py), recolors each with the real dark map, and
# walks the OUTPUT for any light-palette hex still sitting under a
# colour-bearing key — including nested inside a container key like
# `textfont` that theme.js's CONTAINER_KEYS walk might not know to open.
# ---------------------------------------------------------------------------

def _minimal_history_df() -> pd.DataFrame:
    """One scan, two sectors — mirrors tests/test_dashboard_js.py's fixture
    of the same name (kept local rather than imported so this file's figure
    fixtures don't depend on that test module's internals)."""
    rows = []
    for region, sector in [("US", "Technology"), ("EU", "Financials")]:
        rows.append({
            "scan_id": 1, "run_at": "2026-06-23T12:00:00",
            "region": region, "gics_sector": sector,
            "level_score": 0.5, "change_score": 0.3, "data_score": 0.6,
            "sentiment_score": 0.1, "composite": 0.4, "rank": 1.0,
        })
    return pd.DataFrame(rows)


def _sentiment_history_df() -> pd.DataFrame:
    """Exercises both textfont-bearing trace shapes in
    _build_sentiment_scatter_figure: a 'solid' point (has sentiment data,
    labelled by the per-region trace's textfont) and a 'faded' one (no
    sentiment data, labelled by the separate faded trace's textfont) — the
    two textfont dicts Important #1 found shipped unthemed."""
    return pd.DataFrame([
        {"scan_id": 1, "region": "US", "gics_sector": "Technology",
         "data_score": 0.6, "sentiment_score": 0.4},
        {"scan_id": 1, "region": "EU", "gics_sector": "Financials",
         "data_score": 0.5, "sentiment_score": 0.2},
        {"scan_id": 1, "region": "US", "gics_sector": "Energy",
         "data_score": -0.1, "sentiment_score": 0.0},
    ])


def _backtest_summary() -> dict:
    """Minimal single-track summary — same shape as
    tests/test_dashboard_backtest.py's `_summary()`."""
    return {
        "generated_at": "2026-06-26T00:00:00Z", "top_n": 5,
        "tracks": {
            "US": {
                "region": "US", "benchmark": "RSP", "top_n": 5,
                "start": "2020-01-31", "end": "2020-03-31",
                "equity_curve": [
                    {"date": "2020-01-31", "strategy": 1.0, "benchmark": 1.0},
                    {"date": "2020-02-29", "strategy": 1.1, "benchmark": 1.05},
                ],
            },
        },
    }


def _correlation_fixture():
    """Minimal 3x3 correlation matrix + labels/tickers/block_sizes, same
    shape tests/test_correlation.py builds via _order_labels — small and
    literal here since _build_heatmap_figure only needs a valid matrix, not
    a realistic one."""
    tickers = ["A", "B", "C"]
    corr = pd.DataFrame(
        [[1.0, 0.3, -0.2],
         [0.3, 1.0, 0.1],
         [-0.2, 0.1, 1.0]],
        index=tickers, columns=tickers,
    )
    labels = ["Alpha (US)", "Beta (US)", "Gamma (EU)"]
    block_sizes = [2, 1]
    return corr, labels, tickers, block_sizes


def _build_all_seven_figures() -> dict:
    """Build one real instance of each of the 7 baked Plotly figure types,
    as Plotly-JSON-shaped dicts (post pio.to_json + json.loads, exactly as
    build.py embeds them). Returns exactly the 7 named figures — richer
    coverage (e.g. every sector's drilldown) is out of scope; one instance
    per builder is enough to prove recolor() reaches every colour-bearing
    key that builder can produce."""
    figs: dict[str, dict] = {}

    hist = _minimal_history_df()

    figs["rrg"] = json.loads(
        _build_rrg_figure(hist.assign(rs_ratio=100.5, rs_momentum=99.8)))

    figs["sentiment_scatter"] = json.loads(
        _build_sentiment_scatter_figure(_sentiment_history_df()))

    two_scan = pd.concat([
        hist.assign(scan_id=1),
        hist.assign(scan_id=2, composite=hist["composite"] + 0.1),
    ], ignore_index=True)
    figs["movers"] = json.loads(_build_movers_figure(two_scan))

    figs["history"] = json.loads(_build_history_figure(hist))

    drilldown, sector_keys, _ = _build_drilldown_data(hist)
    figs["drilldown"] = json.loads(drilldown[sector_keys[0]])

    backtest_figs = _build_backtest_figures(_backtest_summary())
    figs["backtest"] = json.loads(backtest_figs["US"])

    corr, labels, tickers, block_sizes = _correlation_fixture()
    figs["correlation"] = json.loads(
        _build_heatmap_figure(corr, labels, tickers, block_sizes))

    assert set(figs) == {
        "rrg", "sentiment_scatter", "movers", "history",
        "drilldown", "backtest", "correlation",
    }, f"expected exactly the 7 baked figure types, got {sorted(figs)}"
    return figs


# Colour-bearing keys the survivor walk checks — COLOUR_KEYS from theme.js
# plus textfont (a CONTAINER_KEYS entry whose value is itself colour-bearing
# once you're inside it, and the specific gap Important #1 closed).
_SURVIVOR_KEYS = {
    "color", "bgcolor", "bordercolor", "gridcolor", "zerolinecolor",
    "paper_bgcolor", "plot_bgcolor", "textfont",
}


def _collect_strings(value) -> list[str]:
    """Recursively collect every string leaf under `value` (dict/list/str)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_collect_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_collect_strings(item))
        return out
    return []


def _find_light_survivors(node, light_hexes: set[str]) -> list[tuple[str, str]]:
    """Walk a recolor()'d figure and return every (key, value) pair where a
    colour-bearing key still holds one of `light_hexes` — i.e. recolor()
    failed to reach it. Walks the WHOLE tree unconditionally (unlike
    theme.js's own CONTAINER_KEYS-gated _walk) specifically so a key
    theme.js doesn't know to recurse into can't hide a survivor from this
    check the way it hid the sentiment scatter's textfont bug."""
    survivors: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _SURVIVOR_KEYS:
                for s in _collect_strings(value):
                    if s in light_hexes:
                        survivors.append((key, s))
            survivors.extend(_find_light_survivors(value, light_hexes))
    elif isinstance(node, list):
        for item in node:
            survivors.extend(_find_light_survivors(item, light_hexes))
    return survivors


@pytestmark_node
def test_recolor_leaves_no_light_hex_survivors_in_any_real_figure():
    """For each of the 7 real baked figures, apply the dark map via the real
    theme.js recolor() and assert no light-palette hex survives anywhere
    under a colour-bearing key. This is the spec's original Testing-section
    requirement ("apply the map and assert no light-palette hex survives
    anywhere in the resulting object") that Task 6's brief substituted a
    weaker version of — see test_chart_dark_map_covers_every_figure_constant,
    which only checks membership in the map, not that recolor() reaches it.

    Verified to catch the actual shipped bug: with theme.js's `textfont: true`
    CONTAINER_KEYS entry reverted, this test fails on "sentiment_scatter"
    (textfont.color survives at #3E392B and #8C8370); with the fix applied,
    it passes. See final-review-fix-report.md for the red/green transcript."""
    dark_map = build_chart_dark_map()
    light_hexes = set(dark_map.keys())
    figs = _build_all_seven_figures()

    all_survivors: dict[str, list[tuple[str, str]]] = {}
    for name, fig in figs.items():
        recolored = _recolor(fig, dark_map, True)
        survivors = _find_light_survivors(recolored, light_hexes)
        if survivors:
            all_survivors[name] = survivors

    assert not all_survivors, (
        "light-palette hex survived recolor() under a colour-bearing key "
        f"(figure -> [(key, hex), ...]): {all_survivors}"
    )


# ---------------------------------------------------------------------------
# Responsive cascade order
# ---------------------------------------------------------------------------

def test_responsive_css_is_included_last():
    """Media queries add no specificity, so a responsive override only wins if
    it comes after the unconditional rule it targets.

    This was a live bug: the mobile block sat in _foundation (first include)
    while _chrome (second) re-declared `.card` margin and `.tab-panel` padding
    unconditionally. At 375px the query matched and the desktop values still
    applied — 104px of a 375px viewport lost to margins that were supposed to
    have shrunk.
    """
    style = (Path(__file__).parent.parent
             / "dashboard/templates/_style.html.j2").read_text()
    includes = re.findall(r'include\s+"(css/[^"]+)"', style)
    assert includes, "no css includes found"
    assert includes[-1] == "css/_responsive.css.j2", (
        f"_responsive.css.j2 must be the last CSS include, got {includes[-1]!r}. "
        "Responsive rules placed earlier are silently outranked."
    )


def test_no_responsive_overrides_left_earlier_in_the_cascade():
    """A width-based media query in a partial that loads before _responsive is
    liable to be dead. (pointer/prefers-* queries are fine — they do not
    conflict with the same properties.)"""
    root = Path(__file__).parent.parent / "dashboard/templates"
    style = (root / "_style.html.j2").read_text()
    includes = re.findall(r'include\s+"(css/[^"]+)"', style)

    offenders = []
    for name in includes[:-1]:                      # everything before _responsive
        src = (root / name).read_text()
        for m in re.finditer(r"@media\s*\(\s*(max|min)-width", src):
            offenders.append(f"{name}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "width-based media queries before _responsive.css.j2 may be dead: "
        + ", ".join(offenders)
    )
