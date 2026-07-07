from src.data.trends_symbols import _symbols_by_region, _average_geo_series


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


def test_average_geo_series_means_live_geos():
    per_geo = [
        {"EXV3.DE": [100.0, 100.0]},   # DE
        {"EXV3.DE": [200.0, 200.0]},   # FR
        {"EXV3.DE": [300.0, 300.0]},   # GB
    ]
    out = _average_geo_series(per_geo, window=2)
    assert out["EXV3.DE"] == [200.0, 200.0]   # mean of 100/200/300


def test_average_geo_series_skips_dead_geos():
    per_geo = [
        {"X": [10.0, 10.0]},
        {"X": [0.0, 0.0]},             # dead in this geo -> excluded from mean
        {"X": [30.0, 30.0]},
    ]
    out = _average_geo_series(per_geo, window=2)
    assert out["X"] == [20.0, 20.0]    # mean of 10 and 30 only


def test_average_geo_series_all_dead_is_zero():
    per_geo = [{"X": [0.0, 0.0]}, {"X": [0.0, 0.0]}]
    out = _average_geo_series(per_geo, window=2)
    assert out["X"] == [0.0, 0.0]
