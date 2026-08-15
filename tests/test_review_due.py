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
import shutil
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


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

def test_between_reviews_is_muted_not_a_failure():
    """The common case: today sits before every date in the calendar (true on
    most days of the month). This is the normal 'between reviews' state, not
    an error — badges must mute, not fail open."""
    dates = ["2026-08-31", "2026-09-30", "2026-10-30"]
    result = _review_status(dates, "2026-08-15", None)
    assert result == {"due": False, "dueDate": None, "nextDate": "2026-08-31"}


def test_review_date_today_is_due_and_unacknowledged():
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-08-31", None)
    assert result["due"] is True
    assert result["dueDate"] == "2026-08-31"
    assert result["nextDate"] == "2026-09-30"


def test_review_stays_due_after_the_date_until_acknowledged():
    """Not a fixed grace period — a reader who checks a week late still sees
    the review as due."""
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-06", None)
    assert result["due"] is True
    assert result["dueDate"] == "2026-08-31"


def test_acknowledging_mutes_until_the_next_review_date():
    dates = ["2026-08-31", "2026-09-30"]
    # Acknowledged the day it came due.
    result = _review_status(dates, "2026-09-06", "2026-08-31")
    assert result["due"] is False
    assert result["dueDate"] == "2026-08-31"


def test_next_review_date_arriving_overrides_a_stale_acknowledgment():
    """'...or the next review date arrives' — an ack from the PREVIOUS period
    must not suppress the new one."""
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-30", "2026-08-31")
    assert result["due"] is True
    assert result["dueDate"] == "2026-09-30"


def test_acknowledging_the_current_period_again_stays_muted():
    dates = ["2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-30", "2026-09-30")
    assert result["due"] is False


def test_missing_calendar_fails_open_to_due():
    """Missing/malformed input (an old cached page, a bug) must fall back to
    the PRE-EXISTING always-on behaviour, not a new silently-muted one — muting
    everything forever is a worse failure than never muting."""
    assert _review_status([], "2026-08-15", None)["due"] is True
    assert _review_status(None, "2026-08-15", None)["due"] is True


def test_missing_today_fails_open_to_due():
    dates = ["2026-08-31"]
    assert _review_status(dates, None, None)["due"] is True


def test_unordered_input_is_sorted_before_comparison():
    """The server always emits ascending dates, but the function must not
    silently trust that — a reversed or unsorted list must not flip due/next."""
    dates = ["2026-10-30", "2026-08-31", "2026-09-30"]
    result = _review_status(dates, "2026-09-06", None)
    assert result == {"due": True, "dueDate": "2026-08-31", "nextDate": "2026-09-30"}


# ---------------------------------------------------------------------------
# localISODate — local-calendar-day formatting, no timezone/UTC ambiguity
# ---------------------------------------------------------------------------

def test_local_iso_date_pads_single_digit_month_and_day():
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      console.log(api.localISODate(new Date(2026, 0, 5)));   // Jan 5 -> 01-05
      console.log(api.localISODate(new Date(2026, 10, 30))); // Nov 30 -> 11-30
    """
    out = _node_eval(script).strip().splitlines()
    assert out == ["2026-01-05", "2026-11-30"]
