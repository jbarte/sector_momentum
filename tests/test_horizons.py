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

    `Horizon` no longer carries these figures (2026-08-31 churn-figure
    honesty fix, see sector_momentum-notes/specs/2026-08-31-long-churn-
    honesty-design.md) -- they come from the live backtest artifact now,
    same source and same real-committed-file pattern as
    test_backtest_artifact_was_generated_at_the_configured_cost above.
    Reading a synthetic fixture instead would only prove the mechanism
    threads numbers through, never that the REAL shipped figures still
    keep the labels' promise -- which is the entire point of this test.
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "backtests" / "summary.json"
    if not path.exists():
        pytest.skip("no backtest artifact committed")
    data = json.loads(path.read_text())
    tracks = data["tracks"]
    m = tracks["medium"]["metrics"]
    l = tracks["long"]["metrics"]
    assert m["median_holding_days"] < l["median_holding_days"], (
        f"medium ({m['median_holding_days']}d) no longer holds shorter than "
        f"long ({l['median_holding_days']}d) -- the labels would be lying"
    )
    assert m["trades_per_year"] > l["trades_per_year"], (
        f"medium ({m['trades_per_year']}/yr) no longer trades more than "
        f"long ({l['trades_per_year']}/yr) -- the labels would be lying"
    )


def test_horizon_has_no_performance_attributes():
    """cagr/trades_per_year/median_holding_days used to be config-driven
    Horizon fields -- hand-copied sweep figures that went stale the moment
    the scored universe grew (see the churn-figure-honesty spec). Removed
    entirely, not merely defaulted to None: a future accidental reference
    must fail loudly (AttributeError) rather than silently rendering an em
    dash everywhere forever."""
    h = horizons()[0]
    for attr in ("cagr", "trades_per_year", "median_holding_days"):
        assert not hasattr(h, attr), (
            f"Horizon still has a '{attr}' attribute -- it should have been "
            f"removed by the churn-figure-honesty fix"
        )


def test_presets_do_not_share_a_cadence():
    """The presets must hand the reader DIFFERENT review dates.

    Replaces test_presets_share_one_cadence (2026-08-14 - 2026-09-02), which
    asserted the opposite. Both presets were monthly, so switching preset
    changed the hold band but never changed when the reader was told to look:
    the review panel showed one date whichever preset was selected, which is
    what made the two feel interchangeable. `long` moved to 2M on 2026-09-02
    to fix exactly that.

    Pinned in the same spirit the old assertion was: collapsing back to one
    cadence should fail here rather than quietly un-differentiate the lineup
    months later.
    """
    cadences = {h.rebalance for h in horizons()}
    assert len(cadences) > 1, (
        f"every preset is on the {cadences.pop()!r} cadence again — switching "
        f"preset would show the reader the same review date either way, which "
        f"is the differentiation problem the 2026-09-02 change fixed"
    )


def test_the_faster_cadence_preset_is_not_the_lower_churn_one():
    """The specific failure the old one-cadence rule existed to prevent.

    Retiring test_presets_share_one_cadence gave up a blunt guarantee, so the
    real risk it was standing in for is now pinned directly: a lineup where
    the faster-cadence preset trades LESS than the slower one is incoherent
    (the labels would promise the reverse of what they deliver). Measured on
    both sweep windows before the change: medium 26.2 vs long 10.7 trades/yr
    (2022-) and 26.1 vs 9.4 (2019-2021).

    Reads the live artifact rather than config for the same reason
    test_presets_are_ordered_by_holding_period does -- config no longer
    carries these figures.
    """
    import json
    from pathlib import Path
    from src.backtest.replay import REBALANCE_FREQS

    path = Path(__file__).resolve().parent.parent / "backtests" / "summary.json"
    if not path.exists():
        pytest.skip("no backtest artifact committed")
    tracks = json.loads(path.read_text())["tracks"]
    by_key = {h.key: h for h in horizons()}

    def period_len(freq: str) -> int:
        base, step = REBALANCE_FREQS[freq]
        return {"W": 7, "M": 30, "Q": 91}[base] * step

    ranked = sorted(by_key.values(), key=lambda h: period_len(h.rebalance))
    for faster, slower in zip(ranked, ranked[1:]):
        f = tracks[faster.key]["metrics"]["trades_per_year"]
        s = tracks[slower.key]["metrics"]["trades_per_year"]
        assert f > s, (
            f"{faster.key} reviews more often than {slower.key} "
            f"({faster.rebalance} vs {slower.rebalance}) but trades LESS "
            f"({f} vs {s}/yr) -- the labels promise the reverse"
        )


def test_default_is_one_of_the_presets():
    assert default_horizon().key in {h.key for h in horizons()}


def test_unknown_default_falls_back_rather_than_raising(tmp_path):
    """A typo in `default:` must not take the dashboard down."""
    cfg = tmp_path / "w.yaml"
    cfg.write_text(
        "horizons:\n"
        "  default: nonexistent\n"
        "  presets:\n"
        "    medium: {label: Medium, rebalance: M, top_n: 5, buffer_frac: 0.15}\n"
    )
    assert default_horizon(cfg).key == "medium"


def test_missing_config_yields_a_sane_fallback(tmp_path):
    """No config should degrade to a working strategy, not to top_n=0 (hold
    nothing) or buffer_frac=0 (maximum churn)."""
    h = default_horizon(tmp_path / "does-not-exist.yaml")
    assert h.top_n > 0 and h.buffer_frac > 0
    assert h.rebalance in REBALANCE_FREQS


def test_fallback_matches_the_shipped_default_preset():
    """The hardcoded fallback claims to mirror `medium` — hold it to that."""
    shipped = default_horizon()
    assert (shipped.rebalance, shipped.top_n) == (
        _FALLBACK["rebalance"], _FALLBACK["top_n"]
    ), "_FALLBACK has drifted from the shipped default preset in config/weights.yaml"
    assert shipped.buffer_frac == pytest.approx(_FALLBACK["buffer_frac"]), (
        "_FALLBACK's buffer_frac has drifted from the shipped default preset"
    )


def test_round_half_up_rounds_ties_away_from_zero_not_to_even():
    """Python's builtin round() is banker's rounding (round(4.5) == 4), which
    would silently diverge from JS's Math.round() (always rounds .5 up for
    non-negative inputs) the first time a buffer_frac * universe_size product
    lands on an exact .5 boundary."""
    from src.horizons import _round_half_up
    assert _round_half_up(4.5) == 5
    assert _round_half_up(2.5) == 3
    assert _round_half_up(0.4999999999) == 0
    assert _round_half_up(0.0) == 0


def test_exit_rank_resolves_from_universe_size():
    h = Horizon(key="k", label="K", rebalance="M", top_n=5, buffer_frac=0.15)
    assert h.exit_rank(20) == 8   # top_n 5 + round(0.15*20=3.0) = 8
    assert h.exit_rank(10) == 7   # top_n 5 + round(0.15*10=1.5) = 7 (half-up rounds away from zero)


def test_exit_rank_scales_with_universe_size_not_fixed():
    """The whole point: the SAME preset yields a DIFFERENT exit_rank as the
    universe grows -- this is what 'stop being silently re-tuned by universe
    growth' actually means."""
    h = Horizon(key="k", label="K", rebalance="M", top_n=4, buffer_frac=5 / 18)
    assert h.exit_rank(10) != h.exit_rank(18)
    assert h.exit_rank(18) == 9   # migration value: reproduces today's shipped exit_rank


def test_migration_values_reproduce_todays_shipped_exit_rank():
    """medium and long's buffer_frac must round-trip EXACTLY to today's
    absolute exit_rank (9, 13) at today's real universe size (18) -- this is
    what makes the migration a no-op on rollout day."""
    by_key = {h.key: h for h in horizons()}
    assert by_key["medium"].exit_rank(18) == 9
    assert by_key["long"].exit_rank(18) == 13


# ---------------------------------------------------------------------------
# the band rule
# ---------------------------------------------------------------------------

_TEST_UNIVERSE = 20  # see plan's Global Constraints: N/20 round-trips exactly for every N used here


@pytest.fixture
def h():
    return Horizon(key="k", label="K", rebalance="M", top_n=5, buffer_frac=3 / _TEST_UNIVERSE)


def _setup(rank, horizon, universe_size=_TEST_UNIVERSE):
    row = {"rank": rank}
    _compute_setup(row, horizon, universe_size=universe_size)
    return row["setup"]


def test_band_boundaries(h):
    assert _setup(1, h) == "entry"
    assert _setup(h.top_n, h) == "entry", "top_n itself is inside the buy band"
    assert _setup(h.top_n + 1, h) is None, "the hold zone is silent"
    assert _setup(h.exit_rank(_TEST_UNIVERSE), h) is None, "exit_rank itself is still held"
    assert _setup(h.exit_rank(_TEST_UNIVERSE) + 1, h) == "exit"


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
    _compute_setup(row, h, universe_size=_TEST_UNIVERSE)
    assert row["setup"] == "entry"


def test_setup_changes_when_only_universe_size_changes(h):
    """The regression this task exists to prevent: the SAME rank against the
    SAME horizon must tag differently as the scored universe size changes —
    h has top_n=5, buffer_frac=3/20, so exit_rank(20)=8 and exit_rank(10)=7
    (round(0.15*10)=2 -> 5+2=7)."""
    assert _setup(8, h, universe_size=20) is None   # still held at universe 20
    assert _setup(8, h, universe_size=10) == "exit"  # past the (narrower) band at universe 10


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
    bands = {(h.top_n, h.exit_rank(18)) for h in horizons()}
    assert len(bands) == len(horizons()), (
        f"presets share a band, so they tag rows identically: "
        f"{[(h.key, h.top_n, h.exit_rank(18)) for h in horizons()]}"
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
    assert by_key["long"].exit_rank(18) > by_key["medium"].exit_rank(18)


# ---------------------------------------------------------------------------
# review_dates — the calendar the badge-muting UI reads
# ---------------------------------------------------------------------------

def test_review_dates_reads_the_presets_own_cadence():
    """A distinct fixture per preset, not a hardcoded 'M' — a future preset on
    a different cadence must get its own calendar shape, not medium's."""
    h = Horizon(key="x", label="X", rebalance="Q", top_n=3, buffer_frac=0.1)
    dates = review_dates(h, since="2026-01-15", count=2)
    assert dates == ["2026-03-31", "2026-06-30"]


def test_review_dates_returns_iso_strings_not_timestamps():
    """This feeds straight into build.py's JSON context — a pandas Timestamp
    is not JSON-serialisable, and the failure would surface as a build crash
    far from this function."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer_frac=0.1)
    dates = review_dates(h, since="2026-01-15", count=1)
    assert dates == ["2026-01-30"]
    assert all(isinstance(d, str) for d in dates)


def test_review_dates_count_defaults_to_six():
    """Six matches the design note ('next ~6 review dates') and gives the
    client enough runway that a normal build cadence never runs the calendar
    out before the next one refreshes it."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer_frac=0.1)
    assert len(review_dates(h, since="2026-01-15")) == 6


def test_shipped_presets_have_distinct_review_calendars():
    """The presets must not hand the reader the same dates.

    Replaces test_shipped_presets_agree_on_review_dates (which asserted the
    calendars were IDENTICAL, correct while both presets were monthly). Since
    2026-09-02 `medium` is M and `long` is 2M, so the calendars must actually
    diverge — that divergence is the whole point of the change, and an
    accidental collapse back to one cadence would otherwise only show up as a
    reader noticing the two presets feel the same again.
    """
    by_key = {h.key: h for h in horizons()}
    med = review_dates(by_key["medium"], since="2026-01-15")
    lng = review_dates(by_key["long"], since="2026-01-15")
    assert med != lng, (
        f"medium and long produce identical review calendars ({med}) — "
        f"switching preset would not change when the reader is told to look"
    )
    # 2M is every other M date, so the cadences NEST: within a shared span,
    # every long review day is also a medium review day. Compared over
    # medium's span rather than the raw lists -- review_dates returns a fixed
    # COUNT, so long's six bi-monthly dates reach further out than medium's
    # six monthly ones and a naive subset check fails on span, not nesting.
    overlap = [d for d in lng if d <= med[-1]]
    assert overlap, "long has no review date inside medium's span to compare"
    assert set(overlap) <= set(med), (
        f"long's calendar does not nest inside medium's — a long review can "
        f"land on a day medium skips: medium={med} long_in_span={overlap}"
    )


def test_review_dates_absorbs_a_days_worth_of_client_clock_skew():
    """`since` is the SERVER's date (UTC, at build time) — but a reader
    outside UTC has a local calendar date that trails the server's for part
    of each day (anyone west of UTC), or leads it (anyone east). If a review
    date falls out of the array the moment the server's date passes it, a
    reader whose local date is still ON that review date never sees it as
    due — and since every later build's window only moves forward, it can
    never reappear. `since` one day past a boundary must still return that
    boundary as the first date, not skip to the next one."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer_frac=0.1)
    assert review_dates(h, since="2026-09-01", count=1) == ["2026-08-31"]


def test_review_dates_skew_margin_does_not_shift_the_ordinary_case():
    """The margin must be invisible away from a boundary — otherwise every
    calendar would be running a few days 'behind' for no reason."""
    h = Horizon(key="x", label="X", rebalance="M", top_n=3, buffer_frac=0.1)
    assert review_dates(h, since="2026-01-15", count=1) == ["2026-01-30"]
