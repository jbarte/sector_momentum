import json

import pytest

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


def test_live_horizon_stats_reads_raw_numbers_not_formatted_strings(tmp_path):
    """The horizon-selector chip needs RAW numbers for its own client-side
    Math.round() (index.html.j2's renderHorizonStats) -- unlike
    _build_backtest_context's pre-formatted display strings for the
    Backtest tab table ("21" or "—"). A second, independent read of the
    same file, not a reuse of that function's output."""
    from dashboard.figures import _live_horizon_stats
    summary = _summary()
    summary["tracks"]["US"]["metrics"]["trades_per_year"] = 21.4
    summary["tracks"]["US"]["metrics"]["median_holding_days"] = 92.0
    (tmp_path / "summary.json").write_text(json.dumps(summary))

    stats = _live_horizon_stats(str(tmp_path))

    assert stats["US"]["trades_per_year"] == 21.4
    assert stats["US"]["median_holding_days"] == 92.0


def test_live_horizon_stats_skips_a_none_track(tmp_path):
    """summary["tracks"]["EU"] is None in the fixture (a track that failed
    or was never run) -- must be skipped, not raise on None.get(...)."""
    from dashboard.figures import _live_horizon_stats
    (tmp_path / "summary.json").write_text(json.dumps(_summary()))

    stats = _live_horizon_stats(str(tmp_path))

    assert "EU" not in stats


def test_live_horizon_stats_empty_when_file_missing(tmp_path):
    from dashboard.figures import _live_horizon_stats

    stats = _live_horizon_stats(str(tmp_path))  # no summary.json written

    assert stats == {}


def _curve_summary(points, end):
    """A one-track summary whose equity_curve is `points`."""
    return {
        "generated_at": "2026-09-02T00:00:00Z", "top_n": 5,
        "tracks": {
            "medium": {
                "region": "THEME", "benchmark": "ACWI", "top_n": 5,
                "start": points[0]["date"], "end": end,
                "metrics": {"trades_per_year": 21.0, "median_holding_days": 92.0},
                "equity_curve": points,
                "holdings": [],
            },
        },
    }


def test_window_excess_anchors_by_date_not_by_index(tmp_path):
    """engine.py appends ONE calendar date past the final rebalance, so the
    last curve point is a stub a day after the last real rebalance. Indexing
    back one point measures a single day and would render labelled "1M".
    The 1M window must reach back ~30 days, past the stub."""
    from datetime import date
    from dashboard.figures import _live_horizon_stats
    import json

    points = [
        {"date": "2026-06-30", "strategy": 1.00, "benchmark": 1.00},
        {"date": "2026-07-31", "strategy": 1.00, "benchmark": 1.00},
        {"date": "2026-08-31", "strategy": 1.50, "benchmark": 1.20},
        # the stub: one day past the last rebalance
        {"date": "2026-09-01", "strategy": 1.03, "benchmark": 1.02},
    ]
    (tmp_path / "summary.json").write_text(json.dumps(_curve_summary(points, "2026-09-01")))

    stats = _live_horizon_stats(str(tmp_path), today=date(2026, 9, 5))

    # 30 days before 2026-09-01 is 2026-08-02 -> last point at/before it is 2026-07-31
    assert stats["medium"]["m1_from"] == "2026-07-31"
    # 1.03/1.00 - 1.02/1.00 = 0.01 -> 1.0pp   (NOT the stub's one-day move)
    assert stats["medium"]["m1"] == pytest.approx(1.0)


def test_window_excess_is_strategy_minus_benchmark_in_points(tmp_path):
    from datetime import date
    from dashboard.figures import _live_horizon_stats
    import json

    points = [
        {"date": "2025-08-29", "strategy": 1.00, "benchmark": 1.00},
        {"date": "2026-09-01", "strategy": 1.50, "benchmark": 1.20},
    ]
    (tmp_path / "summary.json").write_text(json.dumps(_curve_summary(points, "2026-09-01")))

    stats = _live_horizon_stats(str(tmp_path), today=date(2026, 9, 5))

    assert stats["medium"]["m12_from"] == "2025-08-29"
    assert stats["medium"]["m12"] == pytest.approx(30.0)   # 1.50 - 1.20 = 0.30


def test_window_excess_none_when_curve_too_short(tmp_path):
    """A track with under a year of history must yield no m12 rather than a
    wrong number measured from its first point."""
    from datetime import date
    from dashboard.figures import _live_horizon_stats
    import json

    points = [
        {"date": "2026-07-31", "strategy": 1.00, "benchmark": 1.00},
        {"date": "2026-09-01", "strategy": 1.10, "benchmark": 1.05},
    ]
    (tmp_path / "summary.json").write_text(json.dumps(_curve_summary(points, "2026-09-01")))

    stats = _live_horizon_stats(str(tmp_path), today=date(2026, 9, 5))

    assert stats["medium"]["m12"] is None
    assert stats["medium"]["m12_from"] is None
    assert stats["medium"]["m1"] is not None


def test_track_record_flags_stale_past_the_threshold(tmp_path):
    """summary.json refreshes only on a manual `python3 backtest.py`. A
    window LABELLED 1M computed from a months-old artifact is wrong in a way
    "since 2008" never is, so staleness is surfaced, not hidden."""
    from datetime import date
    from dashboard.figures import _live_horizon_stats
    import json

    points = [
        {"date": "2026-04-30", "strategy": 1.00, "benchmark": 1.00},
        {"date": "2026-06-01", "strategy": 1.10, "benchmark": 1.05},
    ]
    (tmp_path / "summary.json").write_text(json.dumps(_curve_summary(points, "2026-06-01")))

    fresh = _live_horizon_stats(str(tmp_path), today=date(2026, 6, 20))   # 19 days
    stale = _live_horizon_stats(str(tmp_path), today=date(2026, 9, 5))    # 96 days

    assert fresh["medium"]["stale"] is False
    assert stale["medium"]["stale"] is True
    assert stale["medium"]["as_of"] == "2026-06-01"


def test_live_horizon_stats_keeps_existing_fields(tmp_path):
    """The chip's existing trades/hold numbers must survive the addition.

    _summary()'s US metrics carry neither key, so both read None -- the
    point is that the keys are still PRESENT (renderHorizonStats' em-dash
    fallback keys off None, and a missing key would raise instead)."""
    from dashboard.figures import _live_horizon_stats
    import json
    (tmp_path / "summary.json").write_text(json.dumps(_summary()))

    stats = _live_horizon_stats(str(tmp_path))

    assert stats["US"]["trades_per_year"] is None
    assert stats["US"]["median_holding_days"] is None
