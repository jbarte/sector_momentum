"""Sentiment must not move the board. It was discontinued as a ranking input.

Two separate promises, tested separately because they can break independently:

1. The STORED composite never includes sentiment. `scan.py` passes
   `blend_sentiment=False`, so `scores.composite` is pure price data. This has
   always been true and is the one that matters -- it is what the backtest, the
   ranking, the badges and every alert are derived from.
2. There is no client-side blend. The "Ranking" cogwheel, the only control that
   ever mixed sentiment into the composite in the reader's browser, was
   discontinued on 2026-10-12 and removed outright: no flag, no markup, no
   styling, no wiring, and no baked per-scan history for a client-side re-score
   (`rescore.js`'s `rescore()` went with it). Sentiment is still measured and
   shown on its own page, as a signal in its own right, and nowhere else.

(2) is about ABSENCE: a flag that can be flipped back is exactly how a
discontinued feature returns by accident, so nothing is left to flip.
"""
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DASH = _ROOT / "dashboard"


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
            "scan.py must pass blend_sentiment=False; sentiment may not enter "
            f"the stored composite. Saw:\n{window[:200]}"
        )


def test_the_blend_control_and_its_flag_no_longer_exist():
    from dashboard import build
    assert not hasattr(build, "SENTIMENT_RANKING_ENABLED"), (
        "the sentiment blend was discontinued; do not reintroduce the flag")

    sources = [p for p in _DASH.rglob("*") if p.suffix in {".py", ".j2", ".js"}]
    assert sources, "no dashboard sources found -- did the layout move?"
    forbidden = ("sentiment-toggle", "sentiment-weight", "sentiment-control",
                 "rank-settings", "sentiment_ranking_enabled", "RESCORE_DATA",
                 "rescore_data_json", "_build_rescore_data", "Sentiment toggle")
    for path in sources:
        text = path.read_text()
        for needle in forbidden:
            assert needle not in text, f"{path.relative_to(_ROOT)} still mentions {needle!r}"


def test_the_client_side_rescore_engine_is_gone():
    js = (_DASH / "assets" / "rescore.js").read_text()
    assert "function rescore(" not in js and "rescore: rescore" not in js
    assert "rankAverage" not in js


def test_the_rendered_leaderboard_has_no_ranking_control():
    from tests.test_badge_gating import _render_index
    html = _render_index()
    for needle in ('id="sentiment-toggle"', 'id="sentiment-weight"', "rank-settings",
                   "Include sentiment in ranking"):
        assert needle not in html, needle
