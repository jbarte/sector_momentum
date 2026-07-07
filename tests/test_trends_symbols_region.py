import pandas as pd

from src.data.trends_symbols import _symbols_by_region, _average_geo_series, _fetch_geo


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


class _RecordingClient:
    """Records (kw_list, geo) per build_payload; returns a fixed frame."""
    def __init__(self, frame):
        self._frame = frame
        self.calls = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append((list(kw_list), geo))

    def interest_over_time(self):
        return self._frame


def test_fetch_geo_normalizes_and_passes_geo():
    frame = pd.DataFrame({"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]})
    client = _RecordingClient(frame)
    out = _fetch_geo(client, ["XLK"], anchor="SPY", geo="US", timeframe="today 3-m",
                     window=3, batch_size=4, sleep_s=0.0, max_retries=3, entities={})
    assert client.calls == [(["SPY", "XLK"], "US")]   # geo forwarded
    assert out["XLK"] == [50.0, 100.0, 200.0]         # normalized by SPY


from src.data.trends_symbols import fetch_symbol_trends, DEFAULT_ANCHOR


class _GeoClient:
    """Returns a per-geo frame so multi-geo averaging is observable.

    Anchor 'YouTube' flat at 10. Each non-anchor term is flat at a geo-specific
    level: US=10, DE=10, FR=20, GB=30  -> normalized (÷anchor×100): US=100,
    DE=100, FR=200, GB=300.
    """
    LEVEL = {"US": 10.0, "DE": 10.0, "FR": 20.0, "GB": 30.0, "": 10.0}

    def __init__(self):
        self.calls = []
        self._geo = ""
        self._terms = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append((list(kw_list), geo))
        self._geo = geo
        self._terms = list(kw_list)

    def interest_over_time(self):
        lvl = self.LEVEL[self._geo]
        data = {t: ([10.0] * 3 if t == DEFAULT_ANCHOR else [lvl] * 3) for t in self._terms}
        return pd.DataFrame(data)


def test_fetch_is_region_aware_us_geo_and_eu_average():
    smap = {"US|Technology": ["XLK"], "EU|Technology": ["EXV3.DE"]}
    client = _GeoClient()
    out = fetch_symbol_trends(smap, client=client, window=3, batch_size=4, sleep_s=0.0)

    geos_used = {geo for _, geo in client.calls}
    assert geos_used == {"US", "DE", "FR", "GB"}          # US in US; EU in DE/FR/GB
    assert all(terms[0] == DEFAULT_ANCHOR for terms, _ in client.calls)  # YouTube anchor

    assert list(out["US|Technology"]) == [100.0, 100.0, 100.0]   # US level 10 / anchor 10
    # EU: DE=100, FR=200, GB=300 -> average 200
    assert list(out["EU|Technology"]) == [200.0, 200.0, 200.0]


def test_fetch_region_geos_override():
    smap = {"US|Technology": ["XLK"]}
    client = _GeoClient()
    fetch_symbol_trends(smap, client=client, window=3, batch_size=4, sleep_s=0.0,
                        region_geos={"US": ["DE"]})
    assert {geo for _, geo in client.calls} == {"DE"}    # override respected
