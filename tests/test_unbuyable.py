"""Themes with no route to purchase: scored, ranked, never held.

Shipping (BOAT) has no UCITS equivalent, so an EU account cannot buy it. It
stays in the universe because it shapes the cross-sectional z-scores — dropping
it measured worse on the default Medium preset — but nothing may prompt or
record a position in it.

One config flag drives both halves. If the board and `strategy.simulate` ever
read different sources, the dashboard goes back to advertising returns from
trades the reader cannot make, which is the defect this closes.
"""
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from dashboard.breakdown import (_build_instruments_html, is_unbuyable,
                                 unbuyable_names)
from src.backtest import strategy
from src.backtest.engine import unbuyable_keys

_PROJECT_ROOT = Path(__file__).parent.parent
_THEMES = yaml.safe_load((_PROJECT_ROOT / "config/themes.yaml").read_text())


# ---------------------------------------------------------------------------
# Config — the single source both halves read
# ---------------------------------------------------------------------------

def test_shipping_is_flagged_and_still_in_the_universe():
    """Flagged, not removed. Removing it is a different (measured, worse)
    change — see the 2026-08-10 Done entry in BACKLOG.md."""
    themes = _THEMES["themes"]
    assert "Shipping" in themes, "Shipping must stay scored; the flag is not a deletion"
    assert themes["Shipping"]["ticker"] == "BOAT"
    assert themes["Shipping"]["unbuyable"] is True


def test_shipping_has_no_ucits_entry():
    """The flag exists because there is nothing to list. If someone adds a UCITS
    row, the flag is what should be deleted — not left contradicting it."""
    assert "Shipping" not in (_THEMES.get("ucits") or {})


def test_no_other_theme_is_flagged_by_accident():
    flagged = {n for n, c in _THEMES["themes"].items()
               if isinstance(c, dict) and c.get("unbuyable")}
    assert flagged == {"Shipping"}


def test_both_halves_read_the_same_flag():
    """`unbuyable_keys` (backtest) and `is_unbuyable` (dashboard) must agree."""
    keys = unbuyable_keys(_THEMES)
    assert keys == frozenset({"THEME|Shipping"})
    for name in _THEMES["themes"]:
        assert is_unbuyable(name, _THEMES) == (f"THEME|{name}" in keys)


def test_helpers_tolerate_missing_and_shorthand_config():
    assert unbuyable_keys({}) == frozenset()
    assert is_unbuyable("Shipping", None) is False
    # Legacy shorthand `Theme: TICKER` (a str, not a dict) must not blow up.
    assert unbuyable_keys({"themes": {"Old": "SPY"}}) == frozenset()
    assert is_unbuyable("Old", {"themes": {"Old": "SPY"}}) is False


# ---------------------------------------------------------------------------
# Backtest — never booked, and the slot is not passed on
# ---------------------------------------------------------------------------

def _sim_fixture(n_periods=6):
    """Six periods where BAD is always rank 1 and A..D follow in order."""
    dates = pd.date_range("2024-01-31", periods=n_periods, freq="ME")
    keys = ["THEME|Bad", "THEME|A", "THEME|B", "THEME|C", "THEME|D"]
    instrument_of = {k: k.split("|")[1].upper() for k in keys}
    score_by_date = {
        d: pd.DataFrame({"composite": [5.0, 4.0, 3.0, 2.0, 1.0]}, index=keys)
        for d in dates
    }
    # BAD returns +50% a period; everything else +1%. If it is ever booked, the
    # strategy return is unmissable.
    fwd = pd.DataFrame(
        {t: [0.50 if t == "BAD" else 0.01] * n_periods for t in instrument_of.values()},
        index=dates,
    )
    return score_by_date, fwd, instrument_of


def test_unbuyable_name_is_never_held():
    score_by_date, fwd, instrument_of = _sim_fixture()
    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3,
                            unbuyable=frozenset({"THEME|Bad"}))
    assert sim["holdings"], "simulation produced no periods"
    for picks in sim["holdings"]:
        assert "THEME|Bad" not in picks
    # +50% never reaches the returns: every period is the +1% names.
    assert max(sim["strategy_returns"]) == pytest.approx(0.01, abs=1e-9)


def test_without_the_flag_it_is_held():
    """Guards the fixture itself — if BAD were unreachable for some other
    reason, the test above would pass while proving nothing."""
    score_by_date, fwd, instrument_of = _sim_fixture()
    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3)
    assert all("THEME|Bad" in picks for picks in sim["holdings"])
    assert max(sim["strategy_returns"]) > 0.10


def test_the_freed_slot_is_not_given_to_the_next_name():
    """Skipping beat substituting rank N+1 in all three presets on both CAGR and
    Sharpe, so the slot goes unused. top_n=3 with one unbuyable name holds 2."""
    score_by_date, fwd, instrument_of = _sim_fixture()
    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3,
                            unbuyable=frozenset({"THEME|Bad"}))
    assert all(len(picks) == 2 for picks in sim["holdings"])
    assert all("THEME|C" not in picks for picks in sim["holdings"])


def test_an_untradeable_name_costs_no_turnover():
    """It is re-selected and re-dropped every period. If it leaked into the
    turnover calculation the strategy would pay to trade something it never
    trades."""
    score_by_date, fwd, instrument_of = _sim_fixture()
    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3,
                            cost_bps=10_000, unbuyable=frozenset({"THEME|Bad"}))
    assert sim["turnover"][1:] == [0.0] * (len(sim["turnover"]) - 1)


def test_turnover_is_a_fraction_of_the_book_actually_held():
    """Returns are the equal-weighted mean of the names held, so with one of
    three slots empty each holding is 1/2 of the portfolio, not 1/3."""
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    keys = ["THEME|Bad", "THEME|A", "THEME|B", "THEME|C"]
    instrument_of = {k: k.split("|")[1].upper() for k in keys}
    # Period 2 demotes A below C, forcing exactly one swap in a 2-name book.
    scores = [[5.0, 4.0, 3.0, 2.0], [5.0, 1.0, 3.0, 2.0], [5.0, 1.0, 3.0, 2.0]]
    score_by_date = {d: pd.DataFrame({"composite": s}, index=keys)
                     for d, s in zip(dates, scores)}
    fwd = pd.DataFrame({t: [0.01] * 3 for t in instrument_of.values()}, index=dates)
    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3,
                            unbuyable=frozenset({"THEME|Bad"}))
    assert sim["holdings"][0] == ["THEME|A", "THEME|B"]
    assert sim["holdings"][1] == ["THEME|B", "THEME|C"]
    # One name in, one out, on a 2-name book: 2/(2*2) = 0.5, not 2/(2*3) = 0.33.
    assert sim["turnover"][1] == pytest.approx(0.5)


def test_default_is_no_exclusion():
    """Every existing backtest number was produced without this argument."""
    score_by_date, fwd, instrument_of = _sim_fixture()
    a = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3)
    b = strategy.simulate(score_by_date, fwd, instrument_of, top_n=3,
                          unbuyable=frozenset())
    assert a["holdings"] == b["holdings"]
    assert a["strategy_returns"] == b["strategy_returns"]


def test_published_backtest_excludes_shipping():
    """The shipped artifact, not a fixture: Shipping must appear in no book.

    `holdings` is a list of {"date", "sectors"} dicts. An earlier version of
    this test iterated the period as if it were a plain list of sector keys,
    which silently compared against the dict's KEYS ("date"/"sectors") and
    passed on every input — including artifacts that did hold Shipping. The
    structure assertions below exist so that failure mode cannot come back.
    """
    summary = json.loads((_PROJECT_ROOT / "backtests/summary.json").read_text())
    assert summary["tracks"], "no tracks in the published artifact"
    checked = 0
    for name, track in summary["tracks"].items():
        holdings = track.get("holdings")
        assert holdings, f"{name} has no holdings to check"
        for period in holdings:
            assert isinstance(period, dict) and "sectors" in period, (
                f"{name}: holdings entry shape changed — this test is no longer "
                f"checking what it claims: {period!r}"
            )
            assert "THEME|Shipping" not in period["sectors"], (
                f"{name} books an unbuyable theme on {period['date']}"
            )
            checked += 1
    assert checked > 100, f"only {checked} periods checked — artifact looks truncated"


# ---------------------------------------------------------------------------
# Dashboard — the reader is told why, instead of shown a blank panel
# ---------------------------------------------------------------------------

def test_drilldown_explains_the_absence_instead_of_rendering_nothing():
    """An empty panel reads as missing data. The reader expanding a theme that
    may be sitting at rank 1 needs to know there is nothing to buy."""
    html = _build_instruments_html("THEME|Shipping", {}, _THEMES)
    assert html, "unbuyable theme rendered an empty instruments panel"
    assert 'data-i18n="unbuyable_note"' in html
    assert "cannot be bought" in html


def test_a_buyable_theme_still_lists_its_ucits_row():
    html = _build_instruments_html("THEME|Semiconductors", {}, _THEMES)
    assert "unbuyable_note" not in html
    assert "etf-table" in html


def test_unbuyable_list_comes_from_config_not_the_rendered_rows():
    """The signed-in leaderboard is rebuilt from the DB and learns buyability
    only from this list. The baked rows are LAGGED, so on a build where gating
    caps the bake before a theme entered the universe (13 rows vs 18 live, as on
    2026-08-10) a rows-derived list is empty — and the marker disappears on the
    one path that actually shows the theme.
    """
    assert unbuyable_names(_THEMES) == ["Shipping"]
    # The trap, reproduced: no row mentions Shipping, config still must.
    lagged_rows = [{"sector": "Semiconductors"}, {"sector": "Biotech"}]
    assert not [r for r in lagged_rows if r["sector"] in unbuyable_names(_THEMES)]
    assert unbuyable_names(_THEMES), "config-sourced list must not depend on rows"


def test_built_page_carries_the_unbuyable_list():
    """End-to-end: whatever the lag did to the row set, the page ships the flag."""
    built = _PROJECT_ROOT / "docs/index.html"
    if not built.exists():
        pytest.skip("docs/ not built in this environment")
    html = built.read_text()
    assert 'var UNBUYABLE = ["Shipping"];' in html


def test_swedish_copy_exists_for_every_new_string():
    sv = (_PROJECT_ROOT / "dashboard/templates/i18n/_badges.js.j2").read_text()
    for key in ("badge_unbuyable", "unbuyable_tip", "unbuyable_note", "ucits_title"):
        assert f"{key}:" in sv, f"{key} has no Swedish translation"
