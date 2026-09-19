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
    """v_recent_scores column names, so the Swift test decodes them with the
    same ScoreRow type it uses for live data."""
    keys = {"scan_id", "run_at", "region", "gics_sector", "rank", "composite"}
    for b in _fx()["boards"]:
        assert b["rows"], b["name"]
        assert all(set(r) == keys for r in b["rows"]), b["name"]
        latest = max(r["scan_id"] for r in b["rows"])
        latest_keys = {f'{r["region"]}|{r["gics_sector"]}'
                       for r in b["rows"] if r["scan_id"] == latest}
        assert set(b["expected"]) == latest_keys, b["name"]


def test_boards_fit_the_apps_window():
    """The app only ever sees v_recent_scores' last 6 scans. A board case
    longer than that would test a window the app never has."""
    for b in _fx()["boards"]:
        assert len({r["scan_id"] for r in b["rows"]}) <= 6, b["name"]


def test_build_publishes_the_fixture_and_the_config():
    """Both artifacts are consumed by another repo, so a silently unwired
    build would only show up there. Pinned at the source, like the other
    build.py wiring tests."""
    src = (ROOT / "dashboard" / "build.py").read_text()
    assert 'out_dir / "band-fixture.json"' in src
    assert "build_band_fixture(horizon_list)" in src
    assert "config=build_config_block(" in src
