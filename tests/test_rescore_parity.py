"""Parity tests: rescore.js (run under Node) must match the Python rules it
mirrors -- the latest-row trajectory/delta/band read, the exit rank, and the buy
band (same OLS slope as _compute_rank_trajectories)."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.horizons import default_horizon
from dashboard.rows import TRAJECTORY_WORDS

# The parity check must drive BOTH sides from the same horizon, or it would be
# comparing two different strategies and calling the difference a JS bug.
_H = default_horizon()

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _ols(values):
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return 0.0 if den == 0 else num / den


def _traj(slope):
    if slope <= -1.5:
        return "↑↑", "strong_up"
    if slope <= -0.3:
        return "↑", "up"
    if slope < 0.3:
        return "→", "flat"
    if slope < 1.5:
        return "↓", "down"
    return "↓↓", "strong_down"


def _run_js_meta(recent_rows):
    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const rows = {json.dumps(recent_rows)};
        process.stdout.write(JSON.stringify(R.latestRowMeta(rows, {{top_n: {_H.top_n}, buffer_frac: {_H.buffer_frac}}})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def _py_latest_meta(recent_rows):
    """Mirror dashboard/rows.py: delta format, trajectory, _compute_setup."""
    groups = {}
    for r in recent_rows:
        key = f"{r['region']}|{r['gics_sector']}"
        groups.setdefault(key, []).append(r)
    universe_size = len(groups)  # distinct theme keys, not scans-per-theme
    out = {}
    for key, rows in groups.items():
        rows = sorted(rows, key=lambda x: x["scan_id"])
        n = len(rows)
        latest = rows[-1]
        delta = (rows[-2]["rank"] - latest["rank"]) if n >= 2 else 0.0
        delta_str = f"{delta:+.1f}" if delta != 0 else "—"
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "")
        arrow_class = "up" if delta > 0 else ("down" if delta < 0 else "")
        series = [x["rank"] for x in rows[max(0, n - 5):]]
        label, state = _traj(_ols(series))
        # Position band, mirroring dashboard/rows.py:_compute_setup.
        rank = latest["rank"]
        if rank is None:
            setup = None
        elif rank <= _H.top_n:
            setup = "entry"
        elif rank > _H.exit_rank(universe_size):
            setup = "exit"
        else:
            setup = None
        out[key] = {
            "delta_rank": delta_str, "arrow": arrow, "arrow_class": arrow_class,
            "trajectory_label": label, "trajectory_state": state,
            "trajectory_word": TRAJECTORY_WORDS[state], "setup": setup,
        }
    return out


def _make_recent_rows(n_scans, sectors, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_scans):
        for k in sectors:
            region, gics = k.split("|", 1)
            rows.append({
                "scan_id": i + 1,
                "region": region, "gics_sector": gics,
                "rank": float(rng.integers(1, len(sectors) + 1)),
                "composite": round(float(rng.normal()), 4),
                "change_score": round(float(rng.normal()), 4),
            })
    return rows


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_latest_row_meta_parity_random(seed):
    sectors = [f"US|S{i}" for i in range(6)] + [f"EU|S{i}" for i in range(6)]
    rows = _make_recent_rows(6, sectors, seed)
    js = _run_js_meta(rows)
    py = _py_latest_meta(rows)
    assert set(js.keys()) == set(py.keys())
    for k in py:
        assert js[k] == py[k], k


def test_latest_row_meta_single_scan():
    """A single scan has no trajectory, but the band still applies: setup now
    depends on rank alone, not on history."""
    rows = [{"scan_id": 5, "region": "US", "gics_sector": "Energy",
             "rank": 1.0, "composite": 0.9, "change_score": 0.5}]
    js = _run_js_meta(rows)
    assert js["US|Energy"] == {
        "delta_rank": "—", "arrow": "", "arrow_class": "",
        "trajectory_label": "→", "trajectory_state": "flat",
        "trajectory_word": "flat", "setup": "entry",
    }


def test_latest_row_meta_spans_the_three_bands():
    """Entry inside top_n, Exit past top_n + buffer, silence in the hold zone.
    Ranks are chosen from the configured horizon so this follows config rather
    than pinning magic numbers."""
    def series(name, final_rank):
        return [{"scan_id": i + 1, "region": "US", "gics_sector": name,
                 "rank": float(final_rank), "composite": 0.1, "change_score": 0.1}
                for i in range(5)]

    inside = _H.top_n                 # in the buy band
    holding = _H.exit_rank(3)         # still inside the hold band -- 3 distinct
    gone = _H.exit_rank(3) + 1        # themes are constructed below (In/Hold/Out)

    js = _run_js_meta(series("In", inside) + series("Hold", holding) + series("Out", gone))
    assert js["US|In"]["setup"] == "entry"
    assert js["US|Hold"]["setup"] is None, "the hold zone must be silent"
    assert js["US|Out"]["setup"] == "exit"


def test_setup_band_is_independent_of_momentum():
    """The old rule keyed off trajectory + change score. A falling name that is
    still top-ranked must now read Entry, not Exit — that is the point."""
    falling_but_top = [{"scan_id": i + 1, "region": "US", "gics_sector": "Down",
                        "rank": 1.0, "composite": -0.9, "change_score": -0.9}
                       for i in range(5)]
    js = _run_js_meta(falling_but_top)
    assert js["US|Down"]["setup"] == "entry"


def test_exit_rank_parity_across_universe_sizes():
    """Horizon.exit_rank (Python) and Rescore.exitRank (JS) must agree at
    every universe size — including one that lands exactly on a .5 rounding
    tie, which is precisely where a naive Python round() (banker's rounding)
    would silently diverge from JS's Math.round() (always rounds .5 up)."""
    from src.horizons import Horizon
    h = Horizon(key="k", label="K", rebalance="M", top_n=4, buffer_frac=0.15)
    sizes = [1, 2, 3, 6, 10, 18, 20, 100]
    py = [h.exit_rank(n) for n in sizes]

    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const h = {{top_n: 4, buffer_frac: 0.15}};
        const sizes = {json.dumps(sizes)};
        process.stdout.write(JSON.stringify(sizes.map(n => R.exitRank(h, n))));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    js = json.loads(res.stdout)

    assert py == js, f"Python {py} vs JS {js} at sizes {sizes}"
    # 0.15 * 10 = 1.5 exactly -- the tie this test exists to catch.
    assert h.exit_rank(10) == 4 + 2, "0.15*10=1.5 must round to 2 (half away from zero), not 1"


# ---------------------------------------------------------------------------
# inBuyBand — the highlighted rank badge
# ---------------------------------------------------------------------------
# Was a literal `rank <= 3` copied into the server bake, the sentiment rescore,
# the signed-in rebuild and the scan-history rebuild, none of which knew the
# horizon. With `medium` at top_n 4 the highlight disagreed with the buy-band
# cut line by a row, and with `long` at 5 by two. One rule now, tested on both
# sides of the language boundary like every other shared rule in this file.

def _run_js_in_buy_band(cases):
    """cases: list of [rank, top_n]. Returns list of booleans from rescore.js."""
    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const cases = {json.dumps(cases)};
        process.stdout.write(JSON.stringify(
            cases.map(c => R.inBuyBand(c[0], {{top_n: c[1], buffer_frac: 99}}))));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def test_in_buy_band_matches_python_across_the_boundary():
    cases = [[r, n] for n in (3, 4, 5) for r in (1, 2, 3, 4, 5, 6, 20)]
    expected = [r <= n for r, n in cases]
    assert _run_js_in_buy_band(cases) == expected


def test_in_buy_band_handles_the_shipped_horizon():
    """The literal it replaced was 3; the shipped default is not."""
    n = _H.top_n
    cases = [[n, n], [n + 1, n]]
    assert _run_js_in_buy_band(cases) == [True, False]


def test_in_buy_band_accepts_average_ranks():
    """Tied themes get average (.5) ranks, and the badge must not flicker on
    them — 4.5 is outside a top_n of 4, inside a top_n of 5."""
    assert _run_js_in_buy_band([[4.5, 4], [4.5, 5]]) == [False, True]


def test_in_buy_band_is_false_for_missing_ranks():
    """A row with no rank (the signed-in rebuild renders "—") must not light up.
    Guarded because `null <= 4` is TRUE in JavaScript."""
    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const h = {{top_n: 4, buffer_frac: 5}};
        process.stdout.write(JSON.stringify(
            [R.inBuyBand(null, h), R.inBuyBand(undefined, h), R.inBuyBand(NaN, h),
             R.inBuyBand(2, null)]));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(res.stdout) == [False, False, False, False]
