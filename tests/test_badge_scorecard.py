"""Tests for dashboard.badges — badge scorecard computation."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
from dashboard.badges import build_badge_scorecard


def _make_history(n_scans: int = 8) -> pd.DataFrame:
    """Synthetic scan history with controllable ranks.

    Sector layout (region=US):
      - TechUp: rank improves 4→1 over 8 scans (strong_up trajectory).
        composite > 0 and change > 0 → Entry badge.
      - EnergyDown: rank worsens 1→4 (strong_down).
        change < 0 → Exit badge.
      - HealthFlat: rank stays 2 (flat trajectory).
        No setup badge.
      - FinFlat: rank stays 3 (flat trajectory).
        No setup badge.
    """
    rows = []
    sectors = ["Technology", "Energy", "Health Care", "Financials"]
    for i in range(n_scans):
        sid = 100 + i
        run_at = f"2026-07-{1 + i:02d}T10:00:00"
        ranks = [4 - i * 3 / (n_scans - 1), 1 + i * 3 / (n_scans - 1), 2.0, 3.0]
        composites = [0.5, -0.2, 0.3, 0.1]
        changes = [0.3, -0.4, 0.1, -0.05]
        for j, sec in enumerate(sectors):
            rows.append({
                "scan_id": sid,
                "run_at": run_at,
                "region": "US",
                "gics_sector": sec,
                "rank": round(ranks[j]),
                "composite": composites[j],
                "change_score": changes[j],
                "level_score": 0.5,
                "data_score": 0.4,
                "sentiment_score": None,
            })
    return pd.DataFrame(rows)


def _make_prices() -> dict[str, pd.DataFrame]:
    """Mock prices: every ticker returns a flat 100 except XLK which rises
    steadily over the window."""
    dates = pd.bdate_range("2026-06-25", "2026-07-20")
    flat = pd.DataFrame({"Close": [100.0] * len(dates)}, index=dates)

    xlk_prices = [100.0 + i * 0.2 for i in range(len(dates))]
    xlk = pd.DataFrame({"Close": xlk_prices}, index=dates)

    return {"XLK": xlk, "XLF": flat, "XLE": flat, "XLV": flat}


UNIVERSE = {
    "us_sectors": {
        "Technology": "XLK",
        "Energy": "XLE",
        "Health Care": "XLV",
        "Financials": "XLF",
    },
}


@patch("dashboard.badges.fetch_prices")
def test_scorecard_basic(mock_fetch):
    """With 8 scans, produces 8 rows with correct badge labels in order."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    assert isinstance(result, list)
    assert len(result) == 8
    keys = [r["badge_key"] for r in result]
    assert keys == [
        "entry", "rising_fast", "rising", "flat",
        "falling", "falling_fast", "exit", "no_badge",
    ]
    for row in result:
        assert "count" in row
        assert "hit_rate" in row
        assert "mean_return" in row
        assert "median_return" in row


@patch("dashboard.badges.fetch_prices")
def test_scorecard_too_few_scans(mock_fetch):
    """Fewer than 6 scans → empty list."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=5)
    result = build_badge_scorecard(history, UNIVERSE)
    assert result == []


@patch("dashboard.badges.fetch_prices")
def test_scorecard_min_obs_guard(mock_fetch):
    """Buckets with < 3 observations get None stats."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    for row in result:
        if row["count"] < 3:
            assert row["hit_rate"] is None
            assert row["mean_return"] is None
            assert row["median_return"] is None


@patch("dashboard.badges.fetch_prices")
def test_scorecard_entry_has_positive_mean(mock_fetch):
    """XLK (Entry badge) has rising prices → mean_return should be > 0."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    entry_row = next(r for r in result if r["badge_key"] == "entry")
    if entry_row["count"] >= 3:
        assert entry_row["mean_return"] > 0


@patch("dashboard.badges.fetch_prices")
def test_scorecard_metadata(mock_fetch):
    """Result is a non-empty list of badge-row dicts."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    assert len(result) > 0


def test_scorecard_eu_scalar_ticker():
    """EU sectors map scalar tickers like US sectors."""
    from dashboard.badges import _sector_ticker_map
    universe = {
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {"Banks": "EXV1.DE", "Chemicals": "EXV7.DE"},
    }
    m = _sector_ticker_map(universe)
    assert m["US|Technology"] == "XLK"
    assert m["EU|Banks"] == "EXV1.DE"
    assert m["EU|Chemicals"] == "EXV7.DE"


@patch("dashboard.badges.fetch_prices")
def test_scorecard_empty_history(mock_fetch):
    """Empty history_df → empty list, no price fetch needed."""
    mock_fetch.return_value = {}
    result = build_badge_scorecard(pd.DataFrame(), UNIVERSE)
    assert result == []
