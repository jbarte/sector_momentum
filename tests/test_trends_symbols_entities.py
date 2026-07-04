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
