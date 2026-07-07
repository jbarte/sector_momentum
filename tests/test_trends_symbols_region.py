from src.data.trends_symbols import _symbols_by_region


def test_symbols_by_region_groups_and_dedupes():
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE", "XLK"],       # XLK repeats within US
        "EU|Technology": ["EXV3.DE"],
    }
    out = _symbols_by_region(smap)
    assert out["US"] == ["XLK", "VGT", "XLE"]   # first-seen order, deduped
    assert out["EU"] == ["EXV3.DE"]
    assert set(out) == {"US", "EU"}
