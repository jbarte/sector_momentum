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
    _FALLBACK, _FALLBACK_ROUND_TRIP_BPS, Horizon, default_horizon, horizons,
    review_dates, round_trip_bps,
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
    assert {h.key for h in hs} >= {"medium", "long"}


def test_every_preset_names_a_real_cadence():
    """A cadence typo would raise deep inside a backtest run, hours later."""
    for h in horizons():
        assert h.rebalance in REBALANCE_FREQS, f"{h.key}: bad cadence {h.rebalance!r}"


def test_presets_are_ordered_by_holding_period():
    """Long holds longer and trades less than Medium. If either inverts, the
    labels lie about what the reader is choosing.

    Both assertions are back after the 2026-08-14 cut to two presets. The
    trade-count one had to be relaxed while `short` existed, because a wide band
    on a weekly cadence churned less than a narrow band on a monthly one — Short
    ended up trading less per year than Medium. With both presets now on the same
    cadence, differing only in band width, holding period and trade count order
    together again and there is no reason to accept a looser guarantee.
    """
    by_key = {h.key: h for h in horizons()}
    m, l = by_key["medium"], by_key["long"]
    assert m.median_holding_days < l.median_holding_days
    assert m.trades_per_year > l.trades_per_year


def test_presets_share_one_cadence():
    """The two-preset design is 'one cadence, two band widths'.

    Adding a preset on a different cadence would reintroduce what the 2026-08-14
    cut removed: a lineup where the faster-cadence option can trade less than the
    slower one, and three separate review calendars for the badges to be blind to.
    Deliberate enough to be worth failing a test over rather than discovering in
    a sweep months later.
    """
    cadences = {h.rebalance for h in horizons()}
    assert len(cadences) == 1, f"presets span multiple cadences: {sorted(cadences)}"


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


def test_fallback_matches_the_shipped_default_preset():
    """The hardcoded fallback claims to mirror `medium` — hold it to that.

    It silently stopped matching: `_FALLBACK` sat at M/5/3 (exit rank 8) through
    two preset retunes while the shipped `medium` moved to M/4/5 (exit rank 9),
    so a config-less run got a different book AND a different band from the
    default it documented itself as copying. Nothing failed, because every other
    test either reads config or only checks the fallback is non-degenerate.
    """
    shipped = default_horizon()
    assert (shipped.rebalance, shipped.top_n, shipped.buffer) == (
        _FALLBACK["rebalance"], _FALLBACK["top_n"], _FALLBACK["buffer"]
    ), "_FALLBACK has drifted from the shipped default preset in config/weights.yaml"


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


def test_long_tolerates_more_drift_than_medium():
    """Long's defining property is a WIDER band, not a smaller book.

    This test used to also require `long.top_n <= medium.top_n`, on the reasoning
    that Long "concentrates into fewer positions held for longer". The 2026-08-14
    two-preset cut dropped that half: Long is now M/5/8 (5 names, band 72%)
    against Medium's M/4/5 (4 names, band 50%), so Long holds one name MORE.

    That is a correction, not a regression. Concentration is a risk choice and
    holding period is a horizon choice; the old lineup conflated them. A patient
    sleeve that tolerates more drift before selling naturally ends up holding a
    broader set, and the cells bear it out — M/5/8 beat the best top_n<=4 patient
    candidate (M/4/7) on CAGR, Sharpe, churn and holding period across both
    windows, losing only on drawdown. Keeping the concentration rule would have
    cost real quality to preserve a property nothing depends on.

    What still must hold is the band ordering: if Long's exit rank were not
    strictly wider, the two presets would differ only in book size and Long would
    not be longer in any meaningful sense.
    """
    by_key = {h.key: h for h in horizons()}
    assert by_key["long"].exit_rank > by_key["medium"].exit_rank


# ---------------------------------------------------------------------------
# review_dates — the calendar the badge-muting UI reads
# ---------------------------------------------------------------------------

def test_review_dates_reads_the_presets_own_cadence():
    """A distinct fixture per preset, not a hardcoded 'M' — a future preset on
    a different cadence must get its own calendar shape, not medium's."""
    h = Horizon(key="x", label="X", rebalance="Q", top_n=3, buffer=2)
    dates = review_dates(h, since="2026-01-15", count=2)
    assert dates == ["2026-03-31", "2026-06-30"]


def test_review_dates_returns_iso_strings_not_timestamps():
    """This feeds straight into build.py's JSON context — a pandas Timestamp
    is not JSON-serialisable, and the failure would surface as a build crash
    far from this function."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer=2)
    dates = review_dates(h, since="2026-01-15", count=1)
    assert dates == ["2026-01-30"]
    assert all(isinstance(d, str) for d in dates)


def test_review_dates_count_defaults_to_six():
    """Six matches the design note ('next ~6 review dates') and gives the
    client enough runway that a normal build cadence never runs the calendar
    out before the next one refreshes it."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer=2)
    assert len(review_dates(h, since="2026-01-15")) == 6


def test_shipped_presets_agree_on_review_dates():
    """Both shipped presets share one cadence (test_presets_share_one_cadence)
    — their calendars must therefore be identical, not just same-shaped."""
    by_key = {h.key: h for h in horizons()}
    assert (review_dates(by_key["medium"], since="2026-01-15")
            == review_dates(by_key["long"], since="2026-01-15"))
