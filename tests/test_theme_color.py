"""The browser chrome colour (<meta name="theme-color">) follows the site's
manual Light/Dark control, not only the OS.

The two media-conditioned tags in _head_icons.html.j2 follow prefers-color-scheme,
which is right on Auto. A reader on a light OS who picks Dark by hand got a
dark page under a light address bar. theme.js now rewrites both tags on every
choice and once at load. The pure rule is checked under Node; the wiring is
checked in a real page in tests/test_badge_suffix_playwright.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
_THEME_JS = ROOT / "dashboard" / "assets" / "theme.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _color(attr, for_dark):
    script = f"""
      const T = require({str(_THEME_JS)!r});
      console.log(JSON.stringify(T.themeColorFor({json.dumps(attr)}, {json.dumps(for_dark)})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


LIGHT, DARK = "#f1ecdf", "#17150F"


def test_on_auto_each_tag_keeps_the_colour_of_its_own_scheme():
    assert _color(None, False) == LIGHT
    assert _color(None, True) == DARK


def test_an_explicit_choice_wins_for_every_tag():
    for for_dark in (False, True):
        assert _color("dark", for_dark) == DARK
        assert _color("light", for_dark) == LIGHT


def test_the_colours_are_the_canvas_tokens_in_the_stylesheet():
    """Hardcoded in theme.js; pin them to --canvas so a palette change cannot
    leave the address bar behind."""
    css = (ROOT / "dashboard" / "templates" / "css" / "_foundation.css.j2").read_text()
    assert f"--canvas:      {LIGHT}" in css.replace("#F1ECDF", LIGHT)
    assert f"--canvas:       {DARK}" in css
    partial = (ROOT / "dashboard" / "templates" / "_head_icons.html.j2").read_text()
    assert LIGHT in partial and DARK in partial


def test_choosing_a_theme_and_loading_both_resync_the_tags():
    src = _THEME_JS.read_text()
    set_body = src.split("function set(choice)")[1].split("function initControl")[0]
    assert "syncThemeColor();" in set_body
    init_body = src.split("function initControl()")[1][:200]
    assert "syncThemeColor();" in init_body
