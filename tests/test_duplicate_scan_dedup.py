"""Duplicate scans (weekends, market holidays) must not be read as observations.

The cron runs seven days a week against a five-day market, so Saturday and
Sunday scans replay Friday's close unchanged. Verified against the live DB on
2026-08-09: scans 154→155 and 155→156 had **zero** rank changes across 20
themes, and only 3 of the last 5 scan ids were distinct.

Two consumers were reading raw scan ids and therefore counting replays:
the rank delta (compared against `scan_ids[-2]`) and the Trend slope
(averaged over the last 5 scan ids).
"""
from __future__ import annotations

import pandas as pd

from dashboard.rows import (
    MAX_DUPLICATE_RUN,
    _build_rows_common,
    _compute_rank_trajectories,
    distinct_scan_ids,
)

KEYS = ["region", "gics_sector"]


def _history(per_scan: list[dict[str, tuple[float, float]]]) -> pd.DataFrame:
    """per_scan[i] maps theme -> (rank, composite) for scan_id i+1."""
    rows = []
    for i, scan in enumerate(per_scan):
        for name, (rank, comp) in scan.items():
            rows.append({
                "scan_id": i + 1,
                "run_at": f"2026-08-{1 + i:02d}T06:00:00+00:00",
                "region": "THEME",
                "gics_sector": name,
                "rank": rank,
                "composite": comp,
                "level_score": 0.0, "change_score": 0.0,
                "data_score": 0.0, "sentiment_score": 0.0,
            })
    return pd.DataFrame(rows)


def _weekday(rank, comp):
    return {"Alpha": (rank, comp), "Beta": (3.0 - rank + 1.0, -comp)}


# ---------------------------------------------------------------------------
# distinct_scan_ids
# ---------------------------------------------------------------------------

def test_consecutive_duplicates_collapse():
    df = _history([_weekday(1, 0.9), _weekday(2, 0.5),
                   _weekday(2, 0.5), _weekday(2, 0.5)])   # Fri, Sat, Sun
    assert distinct_scan_ids(df, KEYS) == [1, 4]


def test_representative_is_the_newest_of_a_run():
    """The scan actually rendered is the newest one, so a duplicate run must be
    represented by its last id — not its first."""
    df = _history([_weekday(1, 0.9), _weekday(2, 0.5), _weekday(2, 0.5)])
    assert distinct_scan_ids(df, KEYS)[-1] == 3


def test_non_adjacent_repeat_is_not_collapsed():
    """Only *consecutive* duplicates are replays. A value that recurs after a
    genuine change is a real observation."""
    df = _history([_weekday(1, 0.9), _weekday(2, 0.5), _weekday(1, 0.9)])
    assert distinct_scan_ids(df, KEYS) == [1, 2, 3]


def test_rank_change_alone_counts_as_distinct():
    """Fingerprint covers rank as well as composite: calling two scans identical
    when they are not would skip a real observation."""
    a = {"Alpha": (1.0, 0.5), "Beta": (2.0, 0.4)}
    b = {"Alpha": (2.0, 0.5), "Beta": (1.0, 0.4)}
    assert distinct_scan_ids(_history([a, b]), KEYS) == [1, 2]


def test_all_nan_composites_still_collapse():
    """NaN != NaN would make a scan differ from itself, breaking the check
    exactly when scores are missing."""
    nan = float("nan")
    scan = {"Alpha": (1.0, nan), "Beta": (2.0, nan)}
    assert distinct_scan_ids(_history([scan, scan, scan]), KEYS) == [3]


def test_universe_change_is_distinct():
    df = _history([
        {"Alpha": (1.0, 0.5), "Beta": (2.0, 0.4)},
        {"Alpha": (1.0, 0.5)},                       # Beta dropped
    ])
    assert distinct_scan_ids(df, KEYS) == [1, 2]


def test_empty_history_is_safe():
    assert distinct_scan_ids(pd.DataFrame(), KEYS) == []


# ---------------------------------------------------------------------------
# rank delta
# ---------------------------------------------------------------------------

def _rows(df):
    def _iter(latest):
        for _, r in latest.iterrows():
            yield {"sector": r["gics_sector"], "rank": r["rank"],
                   "delta_rank": r["delta_rank"]}
    rows, _ = _build_rows_common(df, merge_key_cols=KEYS, row_iter_fn=_iter)
    return {r["sector"]: r["delta_rank"] for r in rows}


def test_delta_skips_the_weekend_replay():
    """Monday's predecessor is Sunday, which still carries Friday's close. The
    delta must compare against Thursday's distinct scan instead — this is the
    bug: every row read "—" on Saturday, Sunday and Monday."""
    df = _history([
        _weekday(3, 0.1),      # Thu — Alpha rank 3
        _weekday(1, 0.9),      # Fri — Alpha rank 1
        _weekday(1, 0.9),      # Sat (replay)
        _weekday(1, 0.9),      # Sun (replay)
    ])
    assert _rows(df)["Alpha"] == "+2.0"


def test_genuinely_unchanged_ranks_still_show_no_change():
    """Skipping replays must not manufacture movement: if the last two *distinct*
    scans really do rank the same, the honest answer is still '—'."""
    df = _history([
        {"Alpha": (1.0, 0.90), "Beta": (2.0, 0.10)},
        {"Alpha": (1.0, 0.95), "Beta": (2.0, 0.05)},   # composites moved, ranks did not
    ])
    assert _rows(df)["Alpha"] == "—"


def test_single_scan_has_no_delta():
    assert _rows(_history([_weekday(1, 0.9)]))["Alpha"] == "—"


def test_long_duplicate_run_falls_back_to_no_delta():
    """A stuck pipeline is not a quiet market. Past MAX_DUPLICATE_RUN replays,
    showing a delta would present stale data as a fresh move."""
    scans = [_weekday(3, 0.1)] + [_weekday(1, 0.9)] * (MAX_DUPLICATE_RUN + 2)
    assert _rows(_history(scans))["Alpha"] == "—"


# ---------------------------------------------------------------------------
# trend slope
# ---------------------------------------------------------------------------

def test_trend_slope_is_not_diluted_by_replays():
    """Live data on 2026-08-09 had only 3 distinct scans in the last 5, which
    dragged the slope toward flat — in the column the guide tells the reader to
    trust for exits. The slope must match the one over distinct scans alone."""
    moving = _history([_weekday(r, 0.5) for r in (5.0, 4.0, 3.0, 2.0, 1.0)])
    padded = _history([_weekday(r, 0.5) for r in
                       (5.0, 4.0, 3.0, 2.0, 1.0, 1.0, 1.0)])   # + weekend replays
    assert (_compute_rank_trajectories(padded)["THEME|Alpha"]["slope"]
            == _compute_rank_trajectories(moving)["THEME|Alpha"]["slope"])


def test_trend_still_flat_when_genuinely_flat():
    df = _history([_weekday(3.0, 0.5 + i / 100) for i in range(5)])
    assert _compute_rank_trajectories(df)["THEME|Alpha"]["state"] == "flat"
