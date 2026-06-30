from pathlib import Path

import yaml

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


def test_blocklist_config_entries_are_all_strings():
    """YAML 1.1 parses bare ON/OFF/YES/NO as booleans — entries must stay strings
    or build_symbol_map crashes on `b.upper()`. Catches the unquoted-`ON` regression."""
    raw = yaml.safe_load(Path("config/trends_blocklist.yaml").read_text())
    assert all(isinstance(b, str) for b in raw), f"non-string blocklist entries: {raw}"
    assert "ON" in raw  # the ticker that YAML would coerce to True if unquoted


def test_build_symbol_map_tolerates_non_string_blocklist_entry():
    """Defensive: a stray non-string (e.g. a YAML-coerced bool) must not crash."""
    m = build_symbol_map(_universe(), _sector_etfs(), blocklist={True, "ALL"})
    assert m["US|Financials"] == ["XLF"]  # "ALL" still blocked; True ignored, no crash


def test_build_symbol_map_handles_list_valued_eu_sector():
    universe = {
        "us_sectors": {}, "eu_sectors": {"Financials": ["EXV1.DE", "EXH2.DE", "EXH5.DE"]},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }
    sector_etfs = {"EU": {"Financials": []}}
    m = build_symbol_map(universe, sector_etfs, blocklist=set())
    assert m["EU|Financials"] == ["EXV1.DE", "EXH2.DE", "EXH5.DE"]
