"""The badge-muting UI: whether a preset's Enter/Exit badges read as
"actionable now" or "informational since the last review" — the fix for "the
daily-signal mismatch" (see BACKLOG.md, and
sector_momentum-notes/specs/2026-08-07-rebalance-horizon-hysteresis-design.md
for the original problem statement).

`Rescore.reviewStatus` is a pure function (dates in, due/mute decision out) so
it is testable under Node with no DOM — the same pattern test_color_theme.py
uses for theme.js's resolveTheme/pressedStateFor.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"
_TPL = _PROJECT_ROOT / "dashboard" / "templates"
_INDEX = _TPL / "index.html.j2"
_CORE_JS = _TPL / "i18n" / "_core.js.j2"

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node_eval(script: str) -> str:
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return out.stdout


def _review_status(review_dates, today, ack):
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      console.log(JSON.stringify(api.reviewStatus(
        {json.dumps(review_dates)}, {json.dumps(today)}, {json.dumps(ack)})));
    """
    return json.loads(_node_eval(script))


# ---------------------------------------------------------------------------
# reviewStatus
# ---------------------------------------------------------------------------

@pytestmark_node
def test_between_reviews_is_muted_not_a_failure():
    """The common case: today sits before every date in the calendar (true on
    most days of the month). This is the normal 'between reviews' state, not
    an error — badges must mute, not fail open."""
    dates = ["2026-08-31", "2026-09-30", "2026-10-30"]
    result = _review_status(dates, "2026-08-15", None)
    assert result == {"due": False, "dueDate": None, "nextDate": "2026-08-31"}


@pytestmark_node
def test_review_date_today_is_due_and_unacknowledged():
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-08-31", None)
    assert result["due"] is True
    assert result["dueDate"] == "2026-08-31"
    assert result["nextDate"] == "2026-09-30"


@pytestmark_node
def test_review_stays_due_after_the_date_until_acknowledged():
    """Not a fixed grace period — a reader who checks a week late still sees
    the review as due."""
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-06", None)
    assert result["due"] is True
    assert result["dueDate"] == "2026-08-31"


@pytestmark_node
def test_acknowledging_mutes_until_the_next_review_date():
    dates = ["2026-08-31", "2026-09-30"]
    # Acknowledged the day it came due.
    result = _review_status(dates, "2026-09-06", "2026-08-31")
    assert result["due"] is False
    assert result["dueDate"] == "2026-08-31"


@pytestmark_node
def test_next_review_date_arriving_overrides_a_stale_acknowledgment():
    """'...or the next review date arrives' — an ack from the PREVIOUS period
    must not suppress the new one."""
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-30", "2026-08-31")
    assert result["due"] is True
    assert result["dueDate"] == "2026-09-30"


@pytestmark_node
def test_acknowledging_the_current_period_again_stays_muted():
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-30", "2026-09-30")
    assert result["due"] is False


@pytestmark_node
def test_missing_calendar_fails_open_to_due():
    """Missing/malformed input (an old cached page, a bug) must fall back to
    the PRE-EXISTING always-on behaviour, not a new silently-muted one — muting
    everything forever is a worse failure than never muting."""
    assert _review_status([], "2026-08-15", None)["due"] is True
    assert _review_status(None, "2026-08-15", None)["due"] is True


@pytestmark_node
def test_missing_today_fails_open_to_due():
    dates = ["2026-08-31"]
    assert _review_status(dates, None, None)["due"] is True


@pytestmark_node
def test_unordered_input_is_sorted_before_comparison():
    """The server always emits ascending dates, but the function must not
    silently trust that — a reversed or unsorted list must not flip due/next."""
    dates = ["2026-10-30", "2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-06", None)
    assert result == {"due": True, "dueDate": "2026-08-31", "nextDate": "2026-09-30"}


# ---------------------------------------------------------------------------
# localISODate — local-calendar-day formatting, no timezone/UTC ambiguity
# ---------------------------------------------------------------------------

@pytestmark_node
def test_local_iso_date_pads_single_digit_month_and_day():
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      console.log(api.localISODate(new Date(2026, 0, 5)));   // Jan 5 -> 01-05
      console.log(api.localISODate(new Date(2026, 10, 30))); // Nov 30 -> 11-30
    """
    out = _node_eval(script).strip().splitlines()
    assert out == ["2026-01-05", "2026-11-30"]


# ---------------------------------------------------------------------------
# Markup + i18n — the two ways this class of bug already shipped once
# (horizon_label/horizon_note carried data-i18n with no Swedish entry, and
# silently fell back to English; see _core.js.j2's comment on that fix)
# ---------------------------------------------------------------------------

def test_review_panel_markup_has_all_regions_and_starts_hidden():
    """The inline "Next review:" chip (id="review-status", with separate
    #review-due/#review-next spans) was replaced by the review panel — see
    dashboard/templates/_review_panel.html.j2 and test_review_panel.py. The
    panel is a single container renderReviewPanel() fills in client-side, not
    two states baked separately, so it starts hidden as one unit."""
    html = (_TPL / "_review_panel.html.j2").read_text()
    for el_id in ("review-panel", "rp-headline", "rp-actions", "rp-count",
                  "review-done-btn"):
        assert f'id="{el_id}"' in html, f"missing #{el_id}"
    panel_tag = re.search(r'<section[^>]*id="review-panel"[^>]*>', html).group(0)
    assert "hidden" in panel_tag, "#review-panel must start hidden"
    btn_tag = re.search(r'<button[^>]*id="review-done-btn"[^>]*>', html).group(0)
    assert "hidden" in btn_tag, "#review-done-btn must start hidden"


@pytest.mark.parametrize("key", ["review_due", "review_done", "review_next_label"])
def test_swedish_has_the_new_review_strings(key):
    sv = _CORE_JS.read_text()
    assert f"{key}:" in sv, f"{key} carries data-i18n but has no Swedish entry"


def test_current_review_status_guards_against_missing_rescore():
    """Every other window.Rescore call site in this file guards with
    `window.Rescore && window.Rescore.X` before calling — applyHorizonBadges,
    auth.js. currentReviewStatus() must too: renderReviewStatus() calls it
    BEFORE applyHorizonBadges() in both initHorizonSelect() and
    switchHorizon(), so an unguarded throw here (a stale cache, a blocked
    script, a network hiccup) aborts badge rendering, band boundaries and the
    Done-button binding — not just the review chip."""
    html = _INDEX.read_text()
    start = html.index("function currentReviewStatus()")
    body = html[start:html.index("\n}", start)]
    # Must guard BEFORE the actual call, not merely mention window.Rescore
    # somewhere in the body — the buggy version also contained that
    # substring, in the unguarded call itself.
    call_at = body.index("window.Rescore.reviewStatus(")
    guard_at = body.find("if (!window.Rescore")
    assert guard_at != -1 and guard_at < call_at, (
        "currentReviewStatus() must check window.Rescore BEFORE calling it — "
        "a missing Rescore would otherwise throw and abort the whole "
        "init/switch flow, not just the review chip"
    )
