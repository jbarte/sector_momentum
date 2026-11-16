"""Chart polish (2026-11-16 UI audit): a quiet toolbar, no one-entry legend, a
Drill-down whose scores are readable, and a heatmap in the page's palette.

Figure checks build real figures; the toolbar and copy checks read the sources
(the toolbar is configured in theme.js and styled in _charts.css.j2).
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dashboard.figures import _build_drilldown_data, _build_rrg_figure  # noqa: E402


def _history(themes: dict[str, list[int]]) -> pd.DataFrame:
    """themes: name -> rank per scan, oldest first."""
    rows = []
    for name, ranks in themes.items():
        for i, rank in enumerate(ranks, start=1):
            rows.append({
                "scan_id": i, "run_at": f"2026-11-0{i}T12:00:00+00:00",
                "region": "THEME", "gics_sector": name, "rank": rank,
                "composite": 1.0 - rank * 0.1, "level_score": 0.4,
                "change_score": 0.3, "data_score": 0.6,
                "rs_ratio": 100.0 + rank * 0.3, "rs_momentum": 100.0 - rank * 0.2,
            })
    return pd.DataFrame(rows)


def test_a_single_cohort_rrg_has_no_legend_and_no_region_in_its_hover():
    """A legend with one entry ("THEME") explained nothing."""
    fig = json.loads(_build_rrg_figure(_history({"Space": [3, 2, 1], "Biotech": [1, 2, 3]})))
    assert fig["layout"]["showlegend"] is False
    hover = next(t["hovertemplate"] for t in fig["data"] if t.get("mode") == "markers+text")
    assert "(THEME)" not in hover and hover.startswith("<b>%{text}</b>")


def test_drilldown_puts_rank_on_its_own_reversed_axis():
    """Rank is 1 to 18 and the scores about -1 to 1: on one axis the scores were
    flat lines along the bottom."""
    drill, _, _ = _build_drilldown_data(_history({"Space": [3, 2, 1]}))
    fig = json.loads(drill["THEME|Space"])
    by_name = {t["name"]: t for t in fig["data"]}
    rank = by_name["Rank (right axis)"]
    assert rank["yaxis"] == "y2" and rank["line"]["dash"] == "dot"
    for name in ("Composite", "Level Score", "Change Score", "Data Score"):
        assert by_name[name].get("yaxis", "y") == "y", f"{name} stays on the score axis"
    layout = fig["layout"]
    assert layout["yaxis2"]["overlaying"] == "y" and layout["yaxis2"]["side"] == "right"
    assert layout["yaxis2"]["autorange"] == "reversed", "rank 1 at the top"
    assert layout["yaxis"]["title"]["text"] == "Score"
    assert layout["legend"]["orientation"] == "h", "above the plot: at the right it overlapped the rank axis title"


def test_the_plotly_toolbar_has_no_logo_and_no_selection_tools():
    theme = (_ROOT / "dashboard" / "assets" / "theme.js").read_text()
    assert "displaylogo: false" in theme
    for tool in ("select2d", "lasso2d", "autoScale2d"):
        assert f'"{tool}"' in theme, tool
    assert "Plotly.newPlot(el, themed.data, themed.layout, {" in theme


def test_the_toolbar_appears_on_hover_and_stays_reachable_on_touch():
    css = (_ROOT / "dashboard" / "templates" / "css" / "_charts.css.j2").read_text()
    assert re.search(r"\.js-plotly-plot \.modebar\s*\{\s*opacity:\s*0;", css)
    assert ".js-plotly-plot:hover .modebar" in css and ".js-plotly-plot:focus-within .modebar" in css
    assert re.search(r"@media \(hover: none\), \(pointer: coarse\)\s*\{\s*\.js-plotly-plot \.modebar\s*\{\s*opacity:\s*1;", css), (
        "without reliable hover (a phone, a touch laptop) the toolbar must stay visible or it cannot be reached")
    assert re.search(r"\.modebar-btn path\s*\{\s*fill:\s*var\(--fg4\)\s*!important", css)


def test_the_heatmap_is_in_the_palette_and_the_note_says_what_the_colours_mean():
    from dashboard.correlation import _build_heatmap_figure

    tickers = ["A", "B"]
    corr = pd.DataFrame([[1.0, 0.2], [0.2, 1.0]], index=tickers, columns=tickers)
    fig = json.loads(_build_heatmap_figure(corr, ["Alpha", "Beta"], tickers, [2]))
    assert fig["data"][0]["colorscale"] == [[0, "#5A6F49"], [0.5, "#F4EFE4"], [1, "#A55A3C"]]
    page = (_ROOT / "dashboard" / "templates" / "index.html.j2").read_text()
    assert "Terracotta = moving together" in page and "green = moving independently or opposite" in page
    assert "Red = moving together" not in page and "blue = moving independently" not in page
