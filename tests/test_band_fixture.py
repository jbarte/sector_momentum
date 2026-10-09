"""docs/band-fixture.json: golden vectors the iOS app's Swift port is tested against.

The app re-derives rank delta, trend and the Enter/Exit band natively, which
makes it a fourth implementation of rules src/horizons.py already says three
places must agree on. These tests pin that the fixture covers the cases most
likely to drift -- band edges, trend thresholds, and the weekend replay the
signed-in JS path got wrong until #313 -- and that the build actually publishes it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.band_fixture import UNIVERSE_SIZES, build_band_fixture
from dashboard.rows import HISTORY_SCANS, MAX_DUPLICATE_RUN
from src.backtest.strategy import book_actions
from src.horizons import horizons

ROOT = Path(__file__).parent.parent


def _fx():
    return build_band_fixture(horizons())


def test_is_byte_deterministic():
    """The iOS CI diffs the published file byte-for-byte, and the dashboard
    rebuilds it daily -- so anything run-dependent (a timestamp, set order)
    would turn that check red every day for no reason."""
    a = json.dumps(_fx(), indent=2, sort_keys=True)
    b = json.dumps(_fx(), indent=2, sort_keys=True)
    assert a == b
    assert "generated" not in a


def test_band_covers_every_horizon_at_every_universe_size():
    got = {(c["horizon"], c["universe_size"]) for c in _fx()["band"]}
    assert got == {(h.key, n) for h in horizons() for n in UNIVERSE_SIZES}


def test_band_cases_carry_their_own_exit_rank():
    by_key = {h.key: h for h in horizons()}
    for c in _fx()["band"]:
        assert c["exit_rank"] == by_key[c["horizon"]].exit_rank(c["universe_size"])


def test_band_probes_both_edges_with_half_ranks():
    """A tie produces an x.5 rank, and the band edges are where an
    off-by-one or a < / <= swap would hide."""
    for c in _fx()["band"]:
        ranks = {r["rank"] for r in c["ranks"]}
        edges = {c["top_n"] - 0.5, c["top_n"] + 0.5,
                 c["exit_rank"] - 0.5, c["exit_rank"] + 0.5}
        assert edges <= ranks, (c["horizon"], c["universe_size"])


def test_band_exercises_every_outcome():
    """Guards against a vacuous fixture: a Swift port returning one constant
    would pass if every case expected it."""
    outcomes = {r["setup"] for c in _fx()["band"] for r in c["ranks"]}
    assert outcomes == {"entry", None, "exit"}


def test_series_cover_every_trajectory_state():
    states = {s["trajectory"] for s in _fx()["series"]}
    assert states == {"strong_up", "up", "flat", "down", "strong_down"}


def test_series_include_a_case_only_rounding_classifies():
    """-0.2996 is flat unrounded and 'up' once rounded to 3 dp -- which is
    what Python does before thresholding. A port that skips the rounding
    fails exactly this case."""
    case = next(s for s in _fx()["series"] if s["ranks"] == [3.0, 2.0, 2.0, 2.0, 1.502])
    assert case["slope"] == -0.3
    assert case["trajectory"] == "up"


def test_weekend_replay_keeps_the_delta():
    """Sat/Sun/Mon scans replay Friday's close. Python compares against the
    previous DISTINCT scan, so the delta survives; comparing against the
    previous raw scan (as rescore.js:latestRowMeta did before #313) reads '—'."""
    board = next(b for b in _fx()["boards"] if b["name"] == "weekend replay")
    assert board["expected"]["THEME|Alpha"]["delta_rank"] == "+1.0"
    assert board["expected"]["THEME|Bravo"]["delta_rank"] == "-1.0"


def test_boards_are_in_the_row_shape_the_app_receives():
    """The ten v_recent_scores columns (scan_id, run_at, region, gics_sector,
    level_score, change_score, data_score, sentiment_score, composite, rank --
    scripts/content_gating_migration.sql), so the Swift test decodes them with
    the same ScoreRow type it uses for live data. sentiment_score is null in
    some rows and a number in others, so both decode paths are exercised."""
    keys = {"scan_id", "run_at", "region", "gics_sector", "level_score",
            "change_score", "data_score", "sentiment_score", "composite", "rank"}
    for b in _fx()["boards"]:
        assert b["rows"], b["name"]
        assert all(set(r) == keys for r in b["rows"]), b["name"]
        latest = max(r["scan_id"] for r in b["rows"])
        latest_keys = {f'{r["region"]}|{r["gics_sector"]}'
                       for r in b["rows"] if r["scan_id"] == latest}
        assert set(b["expected"]) == latest_keys, b["name"]
    sentiments = [r["sentiment_score"] for b in _fx()["boards"] for r in b["rows"]]
    assert any(s is None for s in sentiments)
    assert any(isinstance(s, float) for s in sentiments)


def test_a_board_pins_the_five_scan_trend_window():
    """dashboard/rows.py fits the trend over the LAST 5 DISTINCT scans, while
    a client's window holds more than five. This board has six distinct scans with Alpha's
    ranks [9, 1, 2, 1, 2, 1]: the last five are flat (slope 0.0), but a port
    that fits all six reads 'up' (slope -1.086)."""
    board = next(b for b in _fx()["boards"] if b["name"] == "six distinct scans")
    assert len({r["scan_id"] for r in board["rows"]}) == 6
    alpha = board["expected"]["THEME|Alpha"]
    assert alpha["trajectory"] == "flat"
    assert alpha["slope"] == 0.0


def test_boards_fit_the_apps_window():
    """Clients see v_recent_scores' last HISTORY_SCANS scans. A board case
    longer than that would test a window no client has."""
    for b in _fx()["boards"]:
        assert len({r["scan_id"] for r in b["rows"]}) <= HISTORY_SCANS, b["name"]


def _trailing_replays(board) -> int:
    ids = sorted({r["scan_id"] for r in board["rows"]})
    return len(ids) - 2  # scan 1 is the only distinct predecessor; minus the latest


def test_a_stuck_pipeline_blanks_the_delta():
    """One more trailing replay than MAX_DUPLICATE_RUN: the pipeline looks
    stuck, so Python shows no change rather than week-old data as a move.
    Out of every client's reach while v_recent_scores returned 6 scans."""
    board = next(b for b in _fx()["boards"] if b["name"] == "stuck pipeline")
    assert _trailing_replays(board) == MAX_DUPLICATE_RUN + 1
    assert board["expected"]["THEME|Alpha"]["delta_rank"] == "—"
    assert board["expected"]["THEME|Bravo"]["delta_rank"] == "—"


def test_the_longest_healthy_replay_run_keeps_the_delta():
    """Exactly MAX_DUPLICATE_RUN trailing replays is still idle, not stuck.
    With the case above, this pins the boundary from both sides, so a port
    using > where Python uses >= (or the reverse) fails one of the two."""
    board = next(b for b in _fx()["boards"] if b["name"] == "longest healthy replay run")
    assert _trailing_replays(board) == MAX_DUPLICATE_RUN
    assert board["expected"]["THEME|Alpha"]["delta_rank"] == "+1.0"
    assert board["expected"]["THEME|Bravo"]["delta_rank"] == "-1.0"


def test_build_publishes_the_fixture_and_the_config():
    """Source pins, not a behavioural test: both artifacts are consumed by
    another repo, so a silently unwired build would only show up there. The
    config block is computed in its own fail-open step BEFORE data.json's try,
    so a config failure cannot take data.json down with it."""
    src = (ROOT / "dashboard" / "build.py").read_text()
    assert 'out_dir / "band-fixture.json"' in src
    assert "build_band_fixture(horizon_list)" in src
    assert "build_config_block(_themes_cfg, cohort_list, horizon_list," in src
    assert "config=config_block" in src
    assert src.index("build_config_block(") < src.index("# 6b")


# --- the `book` section ---------------------------------------------------

_BOOK_SCENARIOS = [
    "empty book fills the top slots",
    "healthy book changes nothing",
    "hold range is not displaced by new leaders",
    "past the sell line",
    "on the sell line",
    "unbuyable wins a freed slot",
    "unbuyable already held",
    "over-held by one",
    "over-held by two",
    "held theme missing from the scan",
    "tied ranks keep the board's order",
]


def _book():
    return _fx()["book"]


def test_fixture_version_marks_the_book_section():
    """The shape changed (a `book` key was added), so the version moved. The
    iOS app asserts it, which is how a shape change gets noticed."""
    assert _fx()["fixture_version"] == 2


def test_book_covers_every_scenario_for_every_preset():
    assert {c["name"] for c in _book()} == {
        f"{h.key}: {s}" for h in horizons() for s in _BOOK_SCENARIOS}
    assert len(_book()) == len(_BOOK_SCENARIOS) * len(horizons())


def test_book_cases_use_the_configured_presets():
    by_key = {h.key: h for h in horizons()}
    for c in _book():
        h = by_key[c["horizon"]]
        assert (c["top_n"], c["buffer_frac"]) == (h.top_n, h.buffer_frac), c["name"]


def test_book_expected_is_the_python_references_output():
    for c in _book():
        assert c["expected"] == book_actions(
            c["ranked"], c["held"], c["top_n"], c["buffer_frac"], c["unbuyable"]), c["name"]


def test_book_cases_are_well_formed():
    keys = {"picks", "buys", "sells", "blocked", "surplus", "free_slots", "over_held"}
    for c in _book():
        assert len(c["ranks"]) == len(c["ranked"]), c["name"]
        assert c["ranks"] == sorted(c["ranks"]), c["name"]       # best first
        assert c["held"] == sorted(c["held"]), c["name"]
        assert c["unbuyable"] == sorted(c["unbuyable"]), c["name"]
        assert set(c["expected"]) == keys, c["name"]


def test_book_pins_that_the_hold_range_is_not_displaced():
    """The "can't hold 8" case, from the fixture's side: a full book inside
    the hold range buys nothing and sells nothing."""
    cases = [c for c in _book() if "hold range is not displaced" in c["name"]]
    assert len(cases) == len(horizons())
    for c in cases:
        assert c["expected"]["buys"] == [] and c["expected"]["sells"] == [], c["name"]
        assert c["expected"]["free_slots"] == 0, c["name"]


def test_book_pins_the_sell_line_from_both_sides():
    """One rank apart: on the exit rank a holding is kept, one past it is sold.
    A port using < where Python uses <= (or the reverse) fails one of the two."""
    for h in horizons():
        on = next(c for c in _book() if c["name"] == f"{h.key}: on the sell line")
        past = next(c for c in _book() if c["name"] == f"{h.key}: past the sell line")
        assert on["expected"]["sells"] == []
        assert len(past["expected"]["sells"]) == 1


def test_book_tie_case_has_tied_ranks_and_an_order_dependent_surplus():
    """The over-held surplus is the LAST kept holding in board order. With the
    pair straddling top_n tied, a port that reorders ties names the wrong one."""
    ties = [c for c in _book() if "tied ranks keep" in c["name"]]
    assert len(ties) == len(horizons())
    for c in ties:
        assert len(set(c["ranks"])) < len(c["ranks"]), c["name"]
        assert c["expected"]["over_held"] == 1, c["name"]
        assert c["expected"]["surplus"] == [c["ranked"][c["top_n"]]], c["name"]


def test_book_builder_fails_loudly_when_a_preset_leaves_no_hold_range():
    """A wide buffer would otherwise surface as an IndexError in the daily
    build; the builder says which preset and why instead."""
    import dataclasses
    import pytest
    from dashboard.band_fixture import _book_cases
    wide = dataclasses.replace(horizons()[0], buffer_frac=5.0)
    with pytest.raises(ValueError, match="no hold range wide enough"):
        _book_cases([wide])
