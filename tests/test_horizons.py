"""Horizon presets and the rank-band setup rule.

These run against the *shipped* `config/weights.yaml`, not a fixture: the whole
point of `src/horizons.py` is that the backtest, the server-rendered badges and
the client-side re-derivation read one source, so a config typo must fail here
rather than silently produce a dashboard describing a strategy nobody ran.
"""
from __future__ import annotations

import pytest

from dashboard.rows import _compute_setup
from src.backtest.replay import REBALANCE_FREQS
from src.horizons import (
    _FALLBACK_ROUND_TRIP_BPS, Horizon, default_horizon, horizons, round_trip_bps,
)


# ---------------------------------------------------------------------------
# trading costs
# ---------------------------------------------------------------------------

def test_shipped_cost_is_not_zero():
    """The presets were originally selected by a sweep that defaulted to 0 bps,
    which systematically favours whichever cadence trades most — measured on
    this universe the preset CAGR ranking inverts at roughly 50 bps. A zero
    here would silently restore that bias, so it is pinned."""
    assert round_trip_bps() > 0


def test_missing_costs_block_falls_back_non_zero(tmp_path):
    cfg = tmp_path / "w.yaml"
    cfg.write_text("horizons:\n  default: medium\n")
    assert round_trip_bps(cfg) == _FALLBACK_ROUND_TRIP_BPS


@pytest.mark.parametrize("bad", ["", "costs: {}\n", "costs:\n  round_trip_bps: nope\n",
                                 "costs:\n  round_trip_bps: -10\n"])
def test_bad_cost_config_falls_back_rather_than_raising(tmp_path, bad):
    """A typo must not take the dashboard down, and a negative cost must not
    produce a strategy that is paid to trade."""
    cfg = tmp_path / "w.yaml"
    cfg.write_text(bad)
    assert round_trip_bps(cfg) == _FALLBACK_ROUND_TRIP_BPS


def test_backtest_artifact_was_generated_at_the_configured_cost():
    """The figures shipped in `backtests/summary.json` must have been produced
    at the cost the config declares.

    An earlier version of this test blocklisted the known gross CAGRs
    (0.174 / 0.161 / 0.142). That was fragile and produced a false positive
    within a day: the 2026-08-09 acceleration fix legitimately moved Medium's
    net CAGR to 0.142, colliding with a number on the list. Checking the cost
    the artifact records is the real invariant — it catches a regeneration at
    `--cost-bps 0` without caring what the resulting returns happen to be.
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "backtests" / "summary.json"
    if not path.exists():
        pytest.skip("no backtest artifact committed")
    data = json.loads(path.read_text())
    tracks = data.get("tracks", data)
    recorded = {k: v.get("cost_bps") for k, v in tracks.items()
                if isinstance(v, dict) and "metrics" in v}
    assert recorded, "artifact carries no per-track cost_bps"
    expected = round_trip_bps()
    for key, got in recorded.items():
        assert got == pytest.approx(expected), (
            f"{key}: figures generated at {got} bps but config says {expected}. "
            f"Re-run `python3 backtest.py` after changing costs.round_trip_bps."
        )


def test_shipped_config_defines_presets():
    hs = horizons()
    assert len(hs) >= 2, "a selector needs something to select between"
    assert {h.key for h in hs} >= {"short", "medium", "long"}


def test_every_preset_names_a_real_cadence():
    """A cadence typo would raise deep inside a backtest run, hours later."""
    for h in horizons():
        assert h.rebalance in REBALANCE_FREQS, f"{h.key}: bad cadence {h.rebalance!r}"


def test_presets_are_ordered_by_holding_period():
    """Short holds briefest, Long the longest. If this inverts, the labels lie
    about what the reader is choosing.

    HOLDING PERIOD is the ordering that defines the presets. trades/yr is only a
    consequence of band width, and it is NOT required to order with them: since
    `short` widened to W/3/6 on 2026-08-13 it trades 15.7/yr against `medium`'s
    16.8. That is not an inversion of the labels — a wide band on a weekly
    cadence genuinely churns less than a narrow band on a monthly one, while
    still turning positions over in 63 days rather than 94. Long remains the
    quietest by both measures, which is the comparison that would actually
    mislead if it broke.
    """
    by_key = {h.key: h for h in horizons()}
    s, m, l = by_key["short"], by_key["medium"], by_key["long"]
    assert s.median_holding_days < m.median_holding_days < l.median_holding_days
    assert s.trades_per_year > l.trades_per_year
    assert m.trades_per_year > l.trades_per_year


def test_default_is_one_of_the_presets():
    assert default_horizon().key in {h.key for h in horizons()}


def test_unknown_default_falls_back_rather_than_raising(tmp_path):
    """A typo in `default:` must not take the dashboard down."""
    cfg = tmp_path / "w.yaml"
    cfg.write_text(
        "horizons:\n"
        "  default: nonexistent\n"
        "  presets:\n"
        "    medium: {label: Medium, rebalance: M, top_n: 5, buffer: 3}\n"
    )
    assert default_horizon(cfg).key == "medium"


def test_missing_config_yields_a_sane_fallback(tmp_path):
    """No config should degrade to a working strategy, not to top_n=0 (hold
    nothing) or buffer=0 (maximum churn)."""
    h = default_horizon(tmp_path / "does-not-exist.yaml")
    assert h.top_n > 0 and h.buffer > 0
    assert h.rebalance in REBALANCE_FREQS


def test_exit_rank_is_the_hold_band_edge():
    h = Horizon(key="k", label="K", rebalance="M", top_n=5, buffer=3)
    assert h.exit_rank == 8


# ---------------------------------------------------------------------------
# the band rule
# ---------------------------------------------------------------------------

@pytest.fixture
def h():
    return Horizon(key="k", label="K", rebalance="M", top_n=5, buffer=3)


def _setup(rank, horizon):
    row = {"rank": rank}
    _compute_setup(row, horizon)
    return row["setup"]


def test_band_boundaries(h):
    assert _setup(1, h) == "entry"
    assert _setup(h.top_n, h) == "entry", "top_n itself is inside the buy band"
    assert _setup(h.top_n + 1, h) is None, "the hold zone is silent"
    assert _setup(h.exit_rank, h) is None, "exit_rank itself is still held"
    assert _setup(h.exit_rank + 1, h) == "exit"


def test_missing_rank_is_silent(h):
    """Rows without a rank (a theme with no score this scan) must not be
    reported as an Exit — that would read as 'sell this'."""
    assert _setup(None, h) is None


def test_setup_ignores_momentum(h):
    """The old rule keyed off trajectory and change score. A collapsing name
    that is still top-ranked reads Entry now, which is the intended change:
    the badge answers 'should I hold this', not 'is this accelerating'."""
    row = {"rank": 1.0, "_raw_composite": -9.0, "_raw_change": -9.0,
           "trajectory_state": "strong_down"}
    _compute_setup(row, h)
    assert row["setup"] == "entry"


def test_every_preset_has_a_distinct_band():
    """Each preset must tag rows differently, or the selector offers choices
    that look identical on the leaderboard.

    Medium and Long shared a band (both top_n=5, buffer=3) when presets first
    shipped, so switching between them changed the backtest curve but not a
    single Entry/Exit badge. Retuning Long to 2M/4/7 on 2026-08-08 separated
    them. This pins the property so it is not lost by accident — if a future
    retune makes two presets coincide again that is a legitimate trade-off, but
    it should be a decision, not a surprise.
    """
    bands = {(h.top_n, h.exit_rank) for h in horizons()}
    assert len(bands) == len(horizons()), (
        f"presets share a band, so they tag rows identically: "
        f"{[(h.key, h.top_n, h.exit_rank) for h in horizons()]}"
    )


def test_long_holds_fewer_names_than_medium():
    """Long concentrates into fewer positions held for longer — that is what
    makes it 'long', not just a slower rebalance."""
    by_key = {h.key: h for h in horizons()}
    assert by_key["long"].top_n <= by_key["medium"].top_n
    assert by_key["long"].exit_rank > by_key["medium"].exit_rank
