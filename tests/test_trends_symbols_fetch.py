import pandas as pd
from src.data.trends_symbols import fetch_symbol_trends


class FakeClient:
    """Returns a deterministic interest frame for whatever terms were last built."""
    def __init__(self):
        self._terms = []

    def build_payload(self, kw_list, timeframe=None, geo=None):
        self._terms = list(kw_list)

    def interest_over_time(self):
        # anchor flat at 10; each ticker ramps so normalized series is rising
        data = {}
        for i, t in enumerate(self._terms):
            data[t] = [10.0] * 13 if t == "SPY" else [float(i + 1)] * 13
        return pd.DataFrame(data)


def test_fetch_aggregates_per_sector_key_via_fake_client():
    smap = {"US|Technology": ["XLK", "VGT"], "EU|Technology": ["EXV3.DE"]}
    out = fetch_symbol_trends(smap, anchor="SPY", client=FakeClient(), sleep_s=0.0)
    assert set(out) == set(smap)
    assert len(out["US|Technology"]) == 13
    # all series are non-negative numbers
    assert (out["US|Technology"] >= 0).all()
