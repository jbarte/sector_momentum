from src.data.trends_symbols import build_symbol_map


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Financials": "XLF"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def _sector_etfs():
    return {
        "US": {
            "Technology": [{"ticker": "XLK"}, {"ticker": "VGT"}],
            "Financials": [{"ticker": "XLF"}, {"ticker": "ALL"}],  # ALL is blocklisted
        },
        "EU": {"Technology": [{"ticker": "EXV3.DE"}]},
    }


def test_build_symbol_map_combines_and_dedups():
    m = build_symbol_map(_universe(), _sector_etfs(), blocklist={"ALL"})
    assert m["US|Technology"] == ["XLK", "VGT"]        # primary + alternate, deduped
    assert m["US|Financials"] == ["XLF"]               # ALL dropped by blocklist
    assert m["EU|Technology"] == ["EXV3.DE"]
    # benchmark tickers never appear
    assert all("RSP" not in v and "EXSA.DE" not in v and "SPY" not in v for v in m.values())
