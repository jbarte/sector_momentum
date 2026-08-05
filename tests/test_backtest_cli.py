import backtest


def test_build_theme_ticker_list_includes_benchmark_and_spy_fallback():
    themes_cfg = {
        "benchmark": "ACWI",
        "themes": {"Semiconductors": {"ticker": "SOXX"}, "Space": {"ticker": "UFO"}},
    }
    tickers = backtest.build_theme_ticker_list(themes_cfg)
    assert tickers == ["SOXX", "UFO", "ACWI", "SPY"]
