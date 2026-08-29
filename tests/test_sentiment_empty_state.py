"""The Sentiment page's empty state must explain itself.

The page is baked at the LAGGED scan and has no client-side path to refresh
that data. It *does* load auth.js — `sentiment.html.j2` includes
`_footer.html.j2`, which pulls in supabase-client, auth, positions and
alert-prefs under `{% if auth %}` — so sign-in works here and the page is NOT
byte-identical for every reader. (An earlier version of this docstring claimed
it never loaded auth.js at all; corrected in review.) What makes signing in
inert for the sentiment data specifically is that `upgradeLeaderboard()`
returns early without a `#leaderboard-table tbody`, and nothing else re-fetches
`sentiment_signals`. The empty state itself is a build-time Jinja branch, so
that element genuinely cannot change per reader.

Net effect for the reader is what the copy claims: signing in moves the
leaderboard to today's scan and leaves this page a week behind — the state hit
on 2026-08-22, a live-looking board beside an empty sentiment tab.

Worse, on desktop the page shows no scan date anywhere: `.mobile-scan-meta` is
display:none above the mobile breakpoint, and the lag banner is index-only. So
the copy could say "the snapshot shown here" while the page never said which
snapshot that was.
"""
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from dashboard.build import register_asset_url

ROOT = Path(__file__).parent.parent
TPL_DIR = ROOT / "dashboard" / "templates"
SENTIMENT_TPL = (TPL_DIR / "sentiment.html.j2").read_text()


def _render(**overrides) -> str:
    env = Environment(loader=FileSystemLoader(str(TPL_DIR)))
    register_asset_url(env)
    env.filters["js_json"] = (
        lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    )
    ctx = dict(
        sentiment_available=False,
        auth="supabase-config",
        # Matches _build_rows_common's real format (dashboard/rows.py), not a
        # plausible-looking ISO string — both slice to the same 10 chars today,
        # which is exactly how a fixture drifts from production unnoticed.
        scan_date="2026-08-15 06:00 UTC",
        active_scan_id=162,
        lag_days=7,
        active_segment="sentiment",
        plotly_bundle="p.js",
        chart_dark_json="{}",
        horizons_json="[]",
        horizon_default_json="{}",
        round_trip_bps=10,
        sentiment_ranking_enabled=False,
        sentiment_signal_rows=[],
    )
    ctx.update(overrides)
    return env.get_template("sentiment.html.j2").render(**ctx)


def _empty_state(html: str) -> str:
    """The empty-state block only. Bounded by its own title key so the scan
    can't drift into the chart branch or the page chrome."""
    assert 'data-i18n="sentiment_empty_title"' in html, "empty state did not render"
    return html.split('data-i18n="sentiment_empty_title"', 1)[1].split("</div>", 1)[0]


def test_gated_empty_state_names_the_snapshot_it_is_showing():
    """"The snapshot shown here" is unfalsifiable if the page never says which
    snapshot. On desktop this page shows no date anywhere else, so the empty
    state is the only place it can come from."""
    block = _empty_state(_render())
    assert "162" in block, "the empty state does not name the scan it is showing"
    assert "2026-08-15" in block, "the empty state does not give the snapshot's date"


def test_gated_empty_state_says_signing_in_will_not_help():
    """The single most misleading thing about the old copy: it implied a
    fresher view existed ("or when this page is showing an earlier scan than
    the most recent one") without saying that signing in cannot reach it here,
    while the leaderboard beside it visibly does jump to today."""
    block = _empty_state(_render()).lower()
    assert "sign" in block, (
        "the empty state never mentions signing in, so a signed-in reader "
        "looking at a live leaderboard has no way to know this page is pinned"
    )


def test_gated_empty_state_states_the_lag_from_the_real_constant():
    """The lag must trace to gating.LAG_DAYS. Hardcoding the number in the copy
    is how it silently becomes wrong the day LAG_DAYS changes."""
    from dashboard.gating import LAG_DAYS

    block = _empty_state(_render(lag_days=LAG_DAYS))
    assert str(LAG_DAYS) in block, "the empty state does not state the lag"

    # And it must be interpolated, not written into the template as a literal.
    body = SENTIMENT_TPL.split('data-i18n="sentiment_empty_title"', 1)[1]
    assert "lag_days" in body, (
        "the lag is not interpolated from context — a literal here drifts "
        "from dashboard/gating.py the moment LAG_DAYS changes"
    )


def test_interpolated_values_are_separated_from_the_words_around_them():
    """Found live, not by the tests above: `<strong>{{ lag_days }}</strong>`
    sat flush against the span that follows it and rendered "runs 7days
    behind". Every assertion here passed anyway, because each one checked for
    its own substring in isolation and none of them looked at the seam.

    The separator has to be a text node BETWEEN the two elements, never leading
    whitespace inside the translatable span — applyLang() replaces that span's
    whole textContent from the SV table, and the Swedish string carries no
    leading space, so the gap would survive in English and vanish in Swedish.
    """
    import re as _re

    block = _empty_state(_render())
    text = _re.sub(r"<[^>]+>", "", block)          # strip tags, keep text nodes
    assert not _re.search(r"\d(?=[A-Za-zÅÄÖåäö])", text), (
        f"a number runs straight into the following word: {text.strip()!r}"
    )


def test_lag_copy_describes_the_gate_rule_not_a_fixed_distance():
    """`LAG_DAYS` is an age threshold, not a distance from the newest scan.
    `_pick_lagged_scan` takes the newest scan at least N days old *relative to
    now*; it never measures against the newest scan. So "N days behind the
    newest scan" is false whenever scans stop — the lagged pick becomes the
    newest scan, and the data can be weeks stale while the page still claims
    exactly N days. That is precisely the outage this copy exists to explain,
    so getting it backwards there is the worst possible case.
    """
    block = _empty_state(_render()).lower()
    assert "behind the newest" not in block, (
        "the copy states a fixed distance from the newest scan; LAG_DAYS is an "
        "age threshold, and the two diverge exactly when scans stop"
    )
    assert "at least" in block, (
        "the copy should describe the gate rule (newest scan at least N days "
        "old), which stays true however stale the data gets"
    )


def test_ungated_build_makes_no_lag_or_sign_in_claim():
    """With auth unconfigured, `lag_active` is False (build.py) and the page
    renders the LATEST scan. Telling that reader the view is pinned a week back
    and that signing in won't help would be two lies in one sentence."""
    block = _empty_state(_render(auth=None)).lower()
    assert "sign" not in block, (
        "an ungated build has no sign-in and no lag, but the copy still "
        "talks about signing in"
    )
    assert "lag" not in block and "behind" not in block, (
        "an ungated build shows the latest scan; claiming a lag is false there"
    )


def test_empty_state_points_at_the_non_lagging_indicator():
    """This page reflects an outage about a week after it happens, because of
    the lag. The health panel flags one the same day (a FinBERT failure now
    records 0/N and scores a red badge). A reader asking "is sentiment broken
    right now?" must be sent there, not left reading a week-old snapshot."""
    block = _empty_state(_render()).lower()
    assert "health" in block, (
        "the empty state does not point at the health panel — the only "
        "surface that shows a sentiment outage on the day it happens"
    )


@pytest.mark.parametrize("key", [
    "sentiment_empty_title",
    "sentiment_empty_showing",
    "sentiment_empty_lag_pre",
    "sentiment_empty_lag",
    "sentiment_empty_health",
    # Still rendered by the ungated branch, so it must keep its translation.
    "sentiment_empty_body",
])
def test_new_empty_state_strings_are_translated(key):
    """Every data-i18n key must have a Swedish entry or it silently falls back
    to English for SV readers."""
    sv = (TPL_DIR / "i18n" / "_core.js.j2").read_text()
    assert re.search(rf'{key}:\s*"[^"]+"', sv), f"{key} missing from the SV table"
