import json
from dashboard.build import _build_rotation_figures


def _summary():
    return {"rotations": [{
        "name": "Energy 2021–22", "region": "US", "sector": "Energy", "ticker": "XLE",
        "dates": ["2021-01-31", "2021-02-28"], "rank": [9.0, 4.0],
        "composite": [-0.5, 0.6], "price_indexed": [100.0, 118.0],
    }]}


def test_build_rotation_figures_dual_axis():
    figs = _build_rotation_figures(_summary())
    assert len(figs) == 1
    parsed = json.loads(figs[0]["fig_json"])
    assert len(parsed["data"]) == 2          # rank + price traces
    assert parsed["layout"]["yaxis"]["autorange"] == "reversed"  # rank inverted


def test_build_rotation_figures_empty_when_none():
    assert _build_rotation_figures({"rotations": []}) == []
    assert _build_rotation_figures(None) == []
