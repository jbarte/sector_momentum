"""Parity test: rescore.js (run under Node) must match a Python reference
using scipy.rankdata and the same OLS slope as _compute_rank_trajectories."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import rankdata

_PROJECT_ROOT = Path(__file__).parent.parent
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _py_reference(data, W):
    sectors = data["sectors"]
    n = len(data["scans"])
    comp_by_scan, rank_by_scan = [], []
    for s in range(n):
        vals = np.array([(1 - W) * data["data"][k][s] + W * data["sentiment"][k][s]
                         for k in sectors])
        ranks = rankdata(-vals, method="average")
        comp_by_scan.append(dict(zip(sectors, vals)))
        rank_by_scan.append(dict(zip(sectors, ranks)))
    last = n - 1
    prev = last - 1 if n >= 2 else None
    out = {}
    for k in sectors:
        rank_now = rank_by_scan[last][k]
        comp_now = comp_by_scan[last][k]
        d_rank = (rank_by_scan[prev][k] - rank_now) if prev is not None else 0.0
        d_comp = (comp_now - comp_by_scan[prev][k]) if prev is not None else 0.0
        start = max(0, n - 5)
        series = [rank_by_scan[s][k] for s in range(start, n)]
        slope = _ols(series)
        out[k] = {
            "rank": rank_now, "composite": comp_now,
            "delta_rank": d_rank, "delta_composite": d_comp,
            "emerging": bool(d_rank > 0 and d_comp > 0),
            "trajectory_label": _traj(slope)[0],
            "trajectory_state": _traj(slope)[1],
        }
    return out


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


def _run_js(data, W):
    script = f"""
        const R = require({json.dumps(str(_RESCORE_JS))});
        const data = {json.dumps(data)};
        process.stdout.write(JSON.stringify(R.rescore(data, {W})));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def _make_data(n_scans, sectors, seed):
    rng = np.random.default_rng(seed)
    return {
        "scans": [{"scan_id": i + 1, "run_at": f"2026-06-{i+1:02d}T00:00:00"} for i in range(n_scans)],
        "sectors": sectors,
        "data": {k: rng.normal(size=n_scans).round(4).tolist() for k in sectors},
        "sentiment": {k: rng.normal(size=n_scans).round(4).tolist() for k in sectors},
    }


@pytest.mark.parametrize("W", [0.0, 0.30, 1.0])
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_rescore_parity_random(W, seed):
    sectors = [f"US|S{i}" for i in range(6)] + [f"EU|S{i}" for i in range(6)]
    data = _make_data(8, sectors, seed)
    js = _run_js(data, W)
    py = _py_reference(data, W)
    for k in sectors:
        assert js[k]["rank"] == pytest.approx(py[k]["rank"], abs=1e-6)
        assert js[k]["composite"] == pytest.approx(py[k]["composite"], abs=1e-6)
        assert js[k]["delta_rank"] == pytest.approx(py[k]["delta_rank"], abs=1e-6)
        assert js[k]["delta_composite"] == pytest.approx(py[k]["delta_composite"], abs=1e-6)
        assert js[k]["emerging"] == py[k]["emerging"]
        assert js[k]["trajectory_label"] == py[k]["trajectory_label"]
        assert js[k]["trajectory_state"] == py[k]["trajectory_state"]


def test_rescore_parity_ties():
    # All-equal data -> all ranks tie to the average (n+1)/2
    sectors = ["US|A", "US|B", "US|C", "US|D"]
    data = {
        "scans": [{"scan_id": 1, "run_at": "2026-06-01T00:00:00"}],
        "sectors": sectors,
        "data": {k: [1.0] for k in sectors},
        "sentiment": {k: [0.0] for k in sectors},
    }
    js = _run_js(data, 0.30)
    for k in sectors:
        assert js[k]["rank"] == pytest.approx(2.5, abs=1e-6)  # (1+2+3+4)/4


def test_rescore_w0_equals_data_only_order():
    # At W=0 the ranking equals ranking by data_score alone.
    sectors = ["US|A", "US|B", "US|C"]
    data = {
        "scans": [{"scan_id": 1, "run_at": "2026-06-01T00:00:00"}],
        "sectors": sectors,
        "data": {"US|A": [2.0], "US|B": [1.0], "US|C": [3.0]},
        "sentiment": {"US|A": [9.0], "US|B": [9.0], "US|C": [-9.0]},  # ignored at W=0
    }
    js = _run_js(data, 0.0)
    assert js["US|C"]["rank"] == 1.0  # highest data
    assert js["US|A"]["rank"] == 2.0
    assert js["US|B"]["rank"] == 3.0


def _make_split_data(n_scans=4):
    """Two regions × 3 sectors, deterministic values."""
    sectors = ["US|Technology", "EU|Technology",
               "US|Energy", "EU|Energy",
               "US|Health Care", "EU|Health Care"]
    scans = [{"scan_id": i + 1, "run_at": f"2026-06-0{i+1}"} for i in range(n_scans)]
    data, sentiment = {}, {}
    for j, s in enumerate(sectors):
        data[s] = [round(0.5 * j + 0.1 * i, 3) for i in range(n_scans)]
        sentiment[s] = [round(0.2 * j - 0.05 * i, 3) for i in range(n_scans)]
    return {"scans": scans, "sectors": sectors, "data": data, "sentiment": sentiment}


def _py_merge_composite(data):
    sectors = data["sectors"]
    bare = sorted({k.split("|", 1)[1] for k in sectors})
    n = len(data["scans"])
    out = {"scans": data["scans"], "sectors": [f"ALL|{b}" for b in bare],
           "data": {}, "sentiment": {}}
    for b in bare:
        us, eu = f"US|{b}", f"EU|{b}"
        out["data"][f"ALL|{b}"] = [(data["data"][us][i] + data["data"][eu][i]) / 2 for i in range(n)]
        out["sentiment"][f"ALL|{b}"] = [(data["sentiment"][us][i] + data["sentiment"][eu][i]) / 2 for i in range(n)]
    return out


@pytest.mark.parametrize("W", [0.0, 0.3, 1.0])
def test_merge_composite_parity(tmp_path, W):
    data = _make_split_data()
    # JS: mergeComposite then rescore
    script = f"""
      const R = require({json.dumps(str(_RESCORE_JS))});
      const data = {json.dumps(data)};
      const merged = R.mergeComposite(data);
      console.log(JSON.stringify(R.rescore(merged, {W})));
    """
    js_out = json.loads(subprocess.run(["node", "-e", script],
                                       capture_output=True, text=True, check=True).stdout)
    py_merged = _py_merge_composite(data)
    py_out = _py_reference(py_merged, W)
    assert set(js_out.keys()) == set(py_out.keys())
    for k in py_out:
        assert js_out[k]["rank"] == pytest.approx(py_out[k]["rank"])
        assert js_out[k]["composite"] == pytest.approx(py_out[k]["composite"], abs=1e-9)
        assert js_out[k]["trajectory_label"] == py_out[k]["trajectory_label"]
