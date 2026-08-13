"""Sentiment must not move the board while it is alpha.

Two separate promises, tested separately because they can break independently:

1. The STORED composite never includes sentiment. `scan.py` passes
   `blend_sentiment=False`, so `scores.composite` is pure price data. This has
   always been true and is the one that matters — it is what the backtest, the
   ranking, the badges and every alert are derived from.
2. The client-side blend is unreachable. The "Ranking" cogwheel is the only
   control that mixes sentiment into the composite, and while
   `SENTIMENT_RANKING_ENABLED` is False it is not rendered at all.

(2) is deliberately about ABSENCE rather than CSS. `index.html.j2`'s sentiment
wiring early-returns when the control is missing, which is what prevents a
reader who once enabled the blend from having it re-applied from localStorage on
their next visit. A `display:none` would hide the control and leave that path
live, which is why the test asserts the input is gone rather than invisible.
"""
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_TPL_DIR = _ROOT / "dashboard" / "templates"


def test_scan_never_blends_sentiment_into_the_stored_composite():
    """The invariant the whole feature rests on, asserted at its one call site."""
    text = (_ROOT / "scan.py").read_text()
    # Split rather than match a closing paren: the call's indentation and
    # trailing `))` have moved before, and a whitespace-tuned regex silently
    # matches nothing, which makes the test vacuous instead of failing.
    parts = text.split("score_all(")[1:]
    assert parts, "score_all call site not found in scan.py — did it move?"
    for call in parts:
        window = call[:400]
        assert "blend_sentiment=False" in window, (
            "scan.py must pass blend_sentiment=False; sentiment is alpha and may "
            f"not enter the stored composite. Saw:\n{window[:200]}"
        )


def test_the_flag_is_off():
    """Guards the flip being made by accident rather than on purpose. Delete this
    test in the same change that enables the feature."""
    from dashboard import build
    assert build.SENTIMENT_RANKING_ENABLED is False


def _render_index(**overrides):
    from jinja2 import Environment, FileSystemLoader

    from tests.test_badge_gating import _render_index as _render
    return _render(**overrides)


@pytest.mark.parametrize("enabled,expect_control", [(False, False), (True, True)])
def test_the_blend_control_renders_only_when_enabled(enabled, expect_control):
    """Both directions: off means gone, on means present.

    The `True` case matters as much as the `False` one — a gate that never lets
    the feature through is indistinguishable from deleting it, and this is meant
    to be reversible by one flag.
    """
    html = _render_index(sentiment_ranking_enabled=enabled)
    has_input = 'id="sentiment-toggle"' in html
    has_details = 'class="rank-settings"' in html
    assert has_input is expect_control
    assert has_details is expect_control


def test_the_control_is_absent_rather_than_hidden():
    """A hidden control would leave the localStorage re-apply path live."""
    html = _render_index(sentiment_ranking_enabled=False)
    # No element to hide, so no display rule can be the mechanism.
    assert 'id="sentiment-control"' not in html
    assert 'id="sentiment-weight"' not in html


def test_the_wiring_bails_out_when_the_control_is_missing():
    """The template's own guard is what makes absence sufficient. If this
    early-return is ever removed, the gate stops being safe."""
    text = (_TPL_DIR / "index.html.j2").read_text()
    assert re.search(
        r"if \(!toggle \|\| !weightInput \|\| !table[^\)]*\) \{ return; \}", text
    ), "the sentiment wiring no longer early-returns on a missing control"
