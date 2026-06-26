import json

from src.backtest import results


def _track():
    return {
        "region": "US", "benchmark": "RSP", "top_n": 5,
        "start": "2020-01-31", "end": "2020-03-31",
        "metrics": {"total_return": 0.1, "cagr": 0.4, "ann_vol": 0.1, "sharpe": 1.2,
                    "max_drawdown": -0.05, "hit_rate": 0.6, "avg_turnover": 0.3,
                    "benchmark_total_return": 0.05, "benchmark_cagr": 0.2},
        "equity_curve": [{"date": "2020-01-31", "strategy": 1.0, "benchmark": 1.0},
                         {"date": "2020-02-29", "strategy": 1.1, "benchmark": 1.05}],
        "holdings": [{"date": "2020-01-31", "sectors": ["US|Technology"]}],
    }


def test_write_and_load_roundtrip(tmp_path):
    out = str(tmp_path / "backtests")
    path = results.write_results({"US": _track(), "EU": None},
                                 out_dir=out, generated_at="2026-06-26T00:00:00Z", top_n=5)
    assert path.endswith("summary.json")
    loaded = results.load_summary(out)
    assert loaded["top_n"] == 5
    assert loaded["tracks"]["US"]["region"] == "US"
    assert loaded["tracks"]["EU"] is None
    # CSV exports exist for the non-null track
    assert (tmp_path / "backtests" / "equity_US.csv").exists()
    assert (tmp_path / "backtests" / "holdings_US.csv").exists()


def test_load_summary_absent_returns_none(tmp_path):
    assert results.load_summary(str(tmp_path / "nope")) is None
