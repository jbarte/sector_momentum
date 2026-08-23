"""`_fig_to_json` — strips Plotly's auto-applied default `template`.

Found in the 2026-08-23 sweep: `pio.to_json(fig)` bakes in Plotly's FULL
default-styling template on every call — every trace type Plotly ships
(choropleth, scatter3d, mesh3d, parcoords, and 20 others this project never
draws), not just the three actually used (scatter/bar/heatmap). 26 serialized
figures each carried their own copy — 63% of `COHORT_CHARTS`' bytes. Nothing
in this project reads from `layout.template` (every color/font/layout value
is set explicitly), so stripping it changes nothing visually; Plotly.js
falls back to its own built-in default, which is exactly what the blob was
restating.
"""
import json
import sys
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.figures import _fig_to_json, _build_rrg_figure


def test_fig_to_json_strips_the_template():
    """Direct unit test on the helper: a figure with real trace data must
    still serialize its data/layout, but `template` must come back empty."""
    fig = go.Figure(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))
    fig.update_layout(title="test")
    parsed = json.loads(_fig_to_json(fig))

    assert parsed["data"][0]["x"] == [1, 2, 3], "real trace data must survive"
    assert parsed["layout"]["title"]["text"] == "test", "real layout must survive"
    assert parsed["layout"]["template"] == {}, (
        "layout.template was not stripped -- expected an empty object, got "
        f"{parsed['layout']['template']!r}"
    )


def test_fig_to_json_output_is_much_smaller_than_the_unstripped_default():
    """Not just present/absent -- the actual byte saving this item exists
    for. Plotly's default template is tens of KB; stripping it must remove
    the overwhelming majority of that on a trivial figure, where the
    template dominates the payload."""
    import plotly.io as pio

    fig = go.Figure(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))
    unstripped = len(pio.to_json(fig))
    stripped = len(_fig_to_json(fig))

    assert stripped < unstripped * 0.1, (
        f"stripped ({stripped}B) is not under 10% of unstripped ({unstripped}B) "
        f"-- the template strip is not actually removing the bulk of the blob"
    )


def test_a_real_builder_output_has_no_template_blob():
    """End-to-end, not just the helper in isolation: a real figure builder
    (_build_rrg_figure, empty-data path -- no DB needed) must produce JSON
    whose layout.template is the stripped empty object, not the full
    default-styling blob. Confirms the builders actually route through
    _fig_to_json rather than calling pio.to_json directly."""
    raw = _build_rrg_figure(None)  # None/empty rrg_df -> the "no data" figure
    parsed = json.loads(raw)
    assert parsed["layout"]["template"] == {}, (
        "_build_rrg_figure's output still carries the full default template "
        "-- it is not routing through _fig_to_json"
    )
    assert "choropleth" not in raw and "scatter3d" not in raw, (
        "the serialized figure still contains unused trace-type defaults"
    )


def test_correlation_heatmap_output_has_no_template_blob():
    """Same end-to-end proof for dashboard/correlation.py, which has its own
    pio.to_json call site — a SEPARATE module from dashboard/figures.py, so
    fixing the chokepoint in one does not automatically fix the other."""
    import pandas as pd

    from dashboard.correlation import _build_heatmap_figure

    corr = pd.DataFrame([[1.0, 0.5], [0.5, 1.0]], index=["A", "B"], columns=["A", "B"])
    raw = _build_heatmap_figure(corr, ["A", "B"], ["A", "B"], [1, 1])
    parsed = json.loads(raw)
    assert parsed["layout"]["template"] == {}, (
        "_build_heatmap_figure's output still carries the full default "
        "template -- correlation.py is not routing through _fig_to_json"
    )


def test_correlation_py_imports_fig_to_json_not_raw_pio():
    """Pins the fix at the source level too: correlation.py must import the
    shared helper from figures.py rather than calling pio.to_json directly
    -- confirmed by sabotage, reverting the call site to raw pio.to_json
    passes every assertion above except this one only if pio is re-imported,
    so this also guards against a dangling unused import regressing back."""
    src = (Path(__file__).parent.parent / "dashboard" / "correlation.py").read_text()
    assert "_fig_to_json" in src, "correlation.py no longer uses the shared helper"
    assert "pio.to_json(fig)" not in src, (
        "correlation.py calls pio.to_json(fig) directly again, bypassing the "
        "template strip"
    )
