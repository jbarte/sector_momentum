import json

from dashboard.build import _build_backtest_figures


def _summary():
    return {
        "generated_at": "2026-06-26T00:00:00Z", "top_n": 5,
        "tracks": {
            "US": {
                "region": "US", "benchmark": "RSP", "top_n": 5,
                "start": "2020-01-31", "end": "2020-03-31",
                "metrics": {"total_return": 0.1, "cagr": 0.4, "ann_vol": 0.1,
                            "sharpe": 1.2, "max_drawdown": -0.05, "hit_rate": 0.6,
                            "avg_turnover": 0.3, "benchmark_total_return": 0.05,
                            "benchmark_cagr": 0.2},
                "equity_curve": [{"date": "2020-01-31", "strategy": 1.0, "benchmark": 1.0},
                                 {"date": "2020-02-29", "strategy": 1.1, "benchmark": 1.05}],
                "holdings": [{"date": "2020-01-31", "sectors": ["US|Technology"]}],
            },
            "EU": None,
        },
    }


def test_build_backtest_figures_returns_valid_plotly_json():
    figs = _build_backtest_figures(_summary())
    assert "US" in figs
    parsed = json.loads(figs["US"])
    assert "data" in parsed and "layout" in parsed
    # strategy + benchmark traces
    assert len(parsed["data"]) == 2


def test_build_backtest_figures_empty_when_none():
    figs = _build_backtest_figures(None)
    assert figs == {}


def test_track_label_shows_buffer_as_a_percentage_not_zero():
    """Regression: the chart title used to read track["buffer"], an absolute
    rank count. That key was renamed to buffer_frac; _track_label silently
    fell back to 0 and every chart title said "buffer 0" once summary.json
    was regenerated under the new schema — caught in the fractional-band
    whole-branch review's fix-wave re-review, not by any existing test."""
    summary = _summary()
    summary["tracks"]["US"]["buffer_frac"] = 0.25
    figs = _build_backtest_figures(summary)
    parsed = json.loads(figs["US"])
    title = parsed["layout"]["title"]["text"]
    assert "buffer 25%" in title, title
    assert "buffer 0" not in title, title


def test_build_backtest_context_json_is_not_double_encoded(tmp_path):
    from dashboard.build import _build_backtest_context
    # write a minimal summary.json
    (tmp_path / "summary.json").write_text(json.dumps(_summary()))
    ctx = _build_backtest_context(str(tmp_path))
    assert ctx["has_backtest"] is True
    parsed = json.loads(ctx["backtest_json"])
    # values must be objects with Plotly keys, NOT strings
    assert isinstance(parsed["US"], dict)
    assert "data" in parsed["US"] and "layout" in parsed["US"]
