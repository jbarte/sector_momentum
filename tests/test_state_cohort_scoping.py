"""Cohort scoping for readers over the shared scores/signals tables.

Theme rows land in the same tables as sectors from the cohort-unification
migration on. These tests pin the guarantee that sector readers stay
sector-scoped by default, so a theme row can never leak into the sector
leaderboard, rank deltas, or alerts.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import state


class _FakeCursor:
    """Enough of a cursor for load_last_scan's MAX(scan_id) lookup."""

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch) -> dict:
    """Stub _read_sql and record the SQL + params it was handed."""
    seen: dict = {}

    def _fake(conn, q, p=None):
        seen["q"] = " ".join(q.lower().split())
        seen["p"] = p
        return pd.DataFrame()

    monkeypatch.setattr(state, "_read_sql", _fake)
    return seen


# (reader name, extra kwargs) for every reader that must be sector-scoped.
_SECTOR_READERS = [
    ("get_scan_history", {}),
    ("get_rrg_history", {}),
    ("get_signals_for_latest_scan", {}),
    ("get_signals_for_scan", {"scan_id": 1}),
    ("get_sentiment_signals_for_latest_scan", {}),
    ("load_last_scan", {}),
]


@pytest.mark.parametrize("name,kwargs", _SECTOR_READERS)
def test_reader_defaults_to_sector_regions(name, kwargs, monkeypatch):
    seen = _capture(monkeypatch)
    getattr(state, name)(_FakeConn(), **kwargs)
    assert "region = any" in seen["q"], f"{name} is not region-scoped"
    assert list(state.SECTOR_REGIONS) in list(seen["p"]), f"{name} lost its region params"


@pytest.mark.parametrize("name,kwargs", _SECTOR_READERS)
def test_reader_regions_none_selects_all_cohorts(name, kwargs, monkeypatch):
    seen = _capture(monkeypatch)
    getattr(state, name)(_FakeConn(), regions=None, **kwargs)
    assert "region = any" not in seen["q"], f"{name} filtered despite regions=None"


def test_reader_accepts_explicit_theme_region(monkeypatch):
    seen = _capture(monkeypatch)
    state.get_scan_history(_FakeConn(), regions=(state.THEME_REGION,))
    assert ["THEME"] in list(seen["p"])


def test_theme_readers_are_not_region_filtered(monkeypatch):
    """theme_* tables have no region column — filtering them would be a SQL error."""
    seen = _capture(monkeypatch)
    state.get_theme_scan_history(_FakeConn())
    assert "region = any" not in seen["q"]
