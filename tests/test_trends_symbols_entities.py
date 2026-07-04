from src.data.trends_symbols import _resolve_query_terms, _rekey_by_ticker


def test_resolve_query_terms_substitutes_mid_else_ticker():
    terms, rev = _resolve_query_terms(["XLK", "VOX"], {"XLK": "/m/abc"})
    assert terms == ["/m/abc", "VOX"]
    assert rev == {"/m/abc": "XLK", "VOX": "VOX"}


def test_resolve_query_terms_empty_entities_is_identity():
    # additivity guard: no entities -> terms are exactly the tickers
    terms, rev = _resolve_query_terms(["XLK", "XLF"], {})
    assert terms == ["XLK", "XLF"]
    assert rev == {"XLK": "XLK", "XLF": "XLF"}


def test_rekey_by_ticker_maps_terms_and_keeps_anchor():
    raw = {"SPY": [1.0, 2.0], "/m/abc": [3.0, 4.0]}
    out = _rekey_by_ticker(raw, "SPY", {"/m/abc": "XLK"})
    assert out == {"SPY": [1.0, 2.0], "XLK": [3.0, 4.0]}


def test_rekey_by_ticker_passes_through_unmapped_term():
    raw = {"SPY": [1.0], "VOX": [2.0]}
    out = _rekey_by_ticker(raw, "SPY", {"VOX": "VOX"})
    assert out == {"SPY": [1.0], "VOX": [2.0]}


import pandas as pd
from src.data.trends_symbols import fetch_symbol_trends


class _FakeClient:
    """Records the terms passed to build_payload; returns a fixed frame."""
    def __init__(self, frame):
        self._frame = frame
        self.calls: list[list[str]] = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append(list(kw_list))

    def interest_over_time(self):
        return self._frame


def test_fetch_substitutes_mid_and_rekeys_to_ticker():
    # One sector, one live ticker (XLK) resolved to a mid. Frame columns are
    # keyed by the *query terms* the client received (anchor + mid).
    frame = pd.DataFrame({
        "SPY": [10.0, 10.0, 10.0],
        "/m/abc": [5.0, 10.0, 20.0],
    })
    fake = _FakeClient(frame)
    smap = {"US|Technology": ["XLK"]}
    out = fetch_symbol_trends(
        smap, client=fake, window=3, batch_size=4, sleep_s=0.0,
        entities={"XLK": "/m/abc"},
    )
    # the client was queried with the mid, not "XLK"
    assert fake.calls == [["SPY", "/m/abc"]]
    # XLK normalized by SPY: [5/10,10/10,20/10]*100 = [50,100,200]; sector = mean
    assert list(out["US|Technology"]) == [50.0, 100.0, 200.0]


def test_fetch_without_entities_uses_raw_ticker_terms():
    # additivity guard: no entities -> query terms are the raw tickers
    frame = pd.DataFrame({
        "SPY": [10.0, 10.0],
        "XLF": [10.0, 20.0],
    })
    fake = _FakeClient(frame)
    smap = {"US|Financials": ["XLF"]}
    out = fetch_symbol_trends(
        smap, client=fake, window=2, batch_size=4, sleep_s=0.0,
    )
    assert fake.calls == [["SPY", "XLF"]]
    assert list(out["US|Financials"]) == [100.0, 200.0]
