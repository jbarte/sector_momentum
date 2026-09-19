"""Golden parity vectors for clients that re-derive the board themselves.

The iOS app (jbarte/etf-momentum-ios) computes rank delta, trend and the
Enter/Exit band natively from v_recent_scores rows, which makes it another
implementation of rules that live here in Python. This module runs the REAL
Python functions over synthetic inputs and publishes their answers as
docs/band-fixture.json; the app's tests assert it agrees, so the two cannot
drift silently. Spec: sector_momentum-notes/specs/2026-09-17-ios-board-app-design.md.

Three sections:

- ``band``   -- ``_compute_setup`` and ``Horizon.exit_rank`` for every
  configured horizon at several universe sizes, including half-ranks either
  side of both band edges (a tie produces an x.5 rank, and edges are where an
  off-by-one hides).
- ``series`` -- ``_compute_rank_trajectories``' slope and state for rank series
  built to land on and either side of every threshold, plus one that only
  classifies correctly if the slope is rounded to 3 dp first, as Python does.
- ``boards`` -- short scan histories through ``_build_leaderboard_rows`` and
  ``_compute_rank_trajectories``, including the weekend replay: Sat/Sun/Mon
  scans repeat Friday's close, and the delta must be taken against the
  previous DISTINCT scan or it reads "—".

Deterministic and synthetic: no timestamps, no database rows. It is rebuilt
daily with the dashboard but only changes when a rule or a preset does -- which
is exactly when the app needs to know. Nothing in it is a real score, so it
leaks nothing the content gate protects.
"""
from __future__ import annotations

import pandas as pd

from dashboard.rows import (_build_leaderboard_rows, _compute_rank_trajectories,
                            _compute_setup)

FIXTURE_VERSION = 1

#: Sizes the universe has actually had (10, 13, 18, 20 themes) plus both ends.
UNIVERSE_SIZES = (5, 10, 13, 18, 20, 25)

#: On and either side of every trajectory threshold (-1.5, -0.3, 0.3, 1.5).
_SERIES_SLOPES = (-1.6, -1.5, -1.4, -0.35, -0.3, -0.25, 0.0,
                  0.25, 0.3, 0.35, 1.4, 1.5, 1.6)

_REGION = "THEME"


def _band_cases(horizon_list) -> list[dict]:
    cases = []
    for h in horizon_list:
        for n in UNIVERSE_SIZES:
            exit_rank = h.exit_rank(n)
            whole = {float(r) for r in range(1, max(n, exit_rank + 1) + 1)}
            edges = {h.top_n - 0.5, h.top_n + 0.5, exit_rank - 0.5, exit_rank + 0.5}
            expected = []
            for r in sorted(whole | {e for e in edges if e >= 1}):
                row = {"rank": r}
                _compute_setup(row, h, universe_size=n)
                expected.append({"rank": r, "setup": row["setup"],
                                 "in_buy_band": row["in_buy_band"]})
            cases.append({
                "horizon": h.key, "top_n": h.top_n, "buffer_frac": h.buffer_frac,
                "universe_size": n, "exit_rank": exit_rank, "ranks": expected,
            })
    return cases


def _run_at(scan_id: int) -> str:
    return f"2026-01-{scan_id:02d}T11:00:00+00:00"


def _history(scans) -> pd.DataFrame:
    """scans: [(scan_id, [(theme, rank, composite), ...]), ...] -> history_df."""
    return pd.DataFrame([
        {"scan_id": sid, "run_at": _run_at(sid), "region": _REGION,
         "gics_sector": theme, "rank": rank, "composite": composite,
         "level_score": composite, "change_score": composite,
         "data_score": composite, "sentiment_score": None}
        for sid, rows in scans for theme, rank, composite in rows
    ])


def _as_rows(scans) -> list[dict]:
    """The same history in the v_recent_scores row shape the app receives."""
    return [{"scan_id": sid, "run_at": _run_at(sid), "region": _REGION,
             "gics_sector": theme, "rank": rank, "composite": composite}
            for sid, rows in scans for theme, rank, composite in rows]


def _scan(scan_id: int, ranks: dict[str, float], bump: float) -> tuple:
    """One scan. `bump` shifts every composite, so two scans with identical
    ranks are still distinct observations unless given the same bump."""
    return (scan_id, [(t, float(r), round(1.0 - r / 10 + bump, 6)) for t, r in ranks.items()])


def _board_case(name: str, scans) -> dict:
    df = _history(scans)
    rows, _ = _build_leaderboard_rows(df)
    traj = _compute_rank_trajectories(df)
    expected = {}
    for r in rows:
        key = f'{r["region"]}|{r["sector"]}'
        expected[key] = {"delta_rank": r["delta_rank"],
                         "trajectory": traj[key]["state"],
                         "slope": traj[key]["slope"]}
    return {"name": name, "rows": _as_rows(scans), "expected": expected}


def _board_cases() -> list[dict]:
    thu = {"Alpha": 2, "Bravo": 1, "Charlie": 3}
    fri = {"Alpha": 1, "Bravo": 2, "Charlie": 3}
    return [
        _board_case("steady climb", [
            _scan(1, {"Alpha": 3, "Bravo": 1, "Charlie": 2}, 0.01),
            _scan(2, {"Alpha": 3, "Bravo": 1, "Charlie": 2}, 0.02),
            _scan(3, {"Alpha": 2, "Bravo": 1, "Charlie": 3}, 0.03),
            _scan(4, {"Alpha": 2, "Bravo": 1, "Charlie": 3}, 0.04),
            _scan(5, {"Alpha": 1, "Bravo": 2, "Charlie": 3}, 0.05),
        ]),
        # Thu, Fri, then Sat/Sun/Mon replaying Friday exactly (same bump).
        _board_case("weekend replay", [
            _scan(1, thu, 0.01), _scan(2, fri, 0.02),
            _scan(3, fri, 0.02), _scan(4, fri, 0.02), _scan(5, fri, 0.02),
        ]),
        _board_case("nothing moved", [
            _scan(1, fri, 0.02), _scan(2, fri, 0.02), _scan(3, fri, 0.02),
        ]),
        _board_case("theme joins", [
            _scan(1, {"Alpha": 1, "Bravo": 2}, 0.01),
            _scan(2, {"Alpha": 2, "Bravo": 1, "Delta": 3}, 0.02),
        ]),
        # Same ranks, composites moved: a distinct scan, but a zero delta.
        _board_case("composite only", [_scan(1, fri, 0.01), _scan(2, fri, 0.02)]),
        _board_case("tied ranks", [
            _scan(1, {"Alpha": 1, "Bravo": 2, "Charlie": 3}, 0.01),
            _scan(2, {"Alpha": 1.5, "Bravo": 1.5, "Charlie": 3}, 0.02),
        ]),
        _board_case("big moves", [
            _scan(i + 1, {"Alpha": a, "Bravo": b, "Charlie": 5}, 0.01 * (i + 1))
            for i, (a, b) in enumerate([(9, 1), (7, 3), (5, 5), (3, 7), (1, 9)])
        ]),
        # Six raw scans, three distinct: the window the app really has on a Monday.
        _board_case("six raw scans, three distinct", [
            _scan(1, {"Alpha": 3, "Bravo": 1, "Charlie": 2}, 0.01),
            _scan(2, thu, 0.02), _scan(3, fri, 0.03),
            _scan(4, fri, 0.03), _scan(5, fri, 0.03), _scan(6, fri, 0.03),
        ]),
    ]


def _series_case(ranks: list[float]) -> dict:
    # Composites differ per scan so no scan collapses into its neighbour.
    scans = [(i + 1, [("Alpha", r, round(1.0 + i * 0.01, 6))]) for i, r in enumerate(ranks)]
    traj = _compute_rank_trajectories(_history(scans))[f"{_REGION}|Alpha"]
    return {"ranks": ranks, "slope": traj["slope"], "trajectory": traj["state"]}


def _series_cases() -> list[dict]:
    cases = [_series_case([10 + s * (i - 2) for i in range(5)]) for s in _SERIES_SLOPES]
    cases.append(_series_case([3.0, 2.0, 2.0, 2.0, 1.502]))  # -0.2996 -> -0.3 -> "up"
    cases.append(_series_case([4.0, 2.0]))                   # two points
    cases.append(_series_case([6.0]))                        # one point: flat, slope 0
    return cases


def build_band_fixture(horizon_list) -> dict:
    """The whole fixture. Pure: presets in, dict out."""
    return {
        "fixture_version": FIXTURE_VERSION,
        "band": _band_cases(horizon_list),
        "series": _series_cases(),
        "boards": _board_cases(),
    }
