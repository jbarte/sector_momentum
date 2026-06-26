import backtest


def test_build_ticker_list_dedups_and_includes_benchmarks():
    universe = {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }
    tickers = backtest.build_ticker_list(universe)
    assert tickers == ["XLK", "XLE", "EXV3.DE", "RSP", "EXSA.DE"]
