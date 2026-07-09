import math

import pandas as pd

from src.data.trends_symbols import (
    _build_chained_batches,
    _rescale_chain,
    fetch_comparative_interest,
)


def test_chained_batches_11_terms():
    terms = [f"S{i}" for i in range(11)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [
        ["S0", "S1", "S2", "S3", "S4"],
        ["S4", "S5", "S6", "S7", "S8"],
        ["S8", "S9", "S10"],
    ]


def test_chained_batches_5_terms_single_batch():
    terms = [f"S{i}" for i in range(5)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [["S0", "S1", "S2", "S3", "S4"]]


def test_chained_batches_3_terms():
    terms = ["A", "B", "C"]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [["A", "B", "C"]]


def test_chained_batches_6_terms():
    terms = [f"S{i}" for i in range(6)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [
        ["S0", "S1", "S2", "S3", "S4"],
        ["S4", "S5"],
    ]


def test_chained_batches_1_term():
    batches = _build_chained_batches(["only"], batch_size=5)
    assert batches == [["only"]]


def test_chained_batches_empty():
    batches = _build_chained_batches([], batch_size=5)
    assert batches == []


def test_chained_batches_9_terms_no_redundant_trailing():
    terms = [f"S{i}" for i in range(9)]
    batches = _build_chained_batches(terms, batch_size=5)
    assert batches == [
        ["S0", "S1", "S2", "S3", "S4"],
        ["S4", "S5", "S6", "S7", "S8"],
    ]


def test_rescale_chain_two_batches():
    batches = [["A", "B", "C"], ["C", "D", "E"]]
    batch_results = [
        {"A": 50.0, "B": 30.0, "C": 100.0},
        {"C": 50.0, "D": 25.0, "E": 75.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert merged["B"] == 30.0
    assert merged["C"] == 100.0
    # batch1 scale factor = 100/50 = 2.0
    assert merged["D"] == 50.0
    assert merged["E"] == 150.0


def test_rescale_chain_three_batches():
    batches = [["A", "B", "C"], ["C", "D", "E"], ["E", "F"]]
    batch_results = [
        {"A": 10.0, "B": 20.0, "C": 40.0},
        {"C": 20.0, "D": 10.0, "E": 30.0},
        {"E": 15.0, "F": 45.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    # batch0: as-is. batch1 factor = 40/20 = 2.0
    assert merged["A"] == 10.0
    assert merged["D"] == 20.0   # 10 * 2
    assert merged["E"] == 60.0   # 30 * 2
    # batch2 factor = 60/15 = 4.0
    assert merged["F"] == 180.0  # 45 * 4


def test_rescale_chain_single_batch():
    batches = [["X", "Y"]]
    batch_results = [{"X": 80.0, "Y": 20.0}]
    merged = _rescale_chain(batch_results, batches)
    assert merged == {"X": 80.0, "Y": 20.0}


def test_rescale_chain_zero_bridge():
    batches = [["A", "B"], ["B", "C"]]
    batch_results = [
        {"A": 50.0, "B": 0.0},
        {"B": 0.0, "C": 30.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert merged["B"] == 0.0
    assert math.isnan(merged["C"])


def test_rescale_chain_zero_bridge_cascades():
    batches = [["A", "B"], ["B", "C"], ["C", "D"]]
    batch_results = [
        {"A": 50.0, "B": 0.0},
        {"B": 0.0, "C": 30.0},
        {"C": 10.0, "D": 20.0},
    ]
    merged = _rescale_chain(batch_results, batches)
    assert merged["A"] == 50.0
    assert math.isnan(merged["C"])
    assert math.isnan(merged["D"])


class FakeComparativeClient:
    """Returns deterministic interest for comparative batches (no anchor term)."""
    def __init__(self):
        self._terms = []
        self._geo = ""

    def build_payload(self, kw_list, timeframe=None, geo=None):
        self._terms = list(kw_list)
        self._geo = geo

    def interest_over_time(self):
        data = {}
        for i, t in enumerate(self._terms):
            data[t] = [float((i + 1) * 10)] * 13
        return pd.DataFrame(data)


def test_fetch_comparative_interest_basic():
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE"],
        "US|Financials": ["XLF"],
    }
    result = fetch_comparative_interest(
        smap,
        client=FakeComparativeClient(),
        sleep_s=0.0,
        region_geos={"US": ["US"]},
    )
    assert "US|Technology" in result
    assert "US|Energy" in result
    assert "US|Financials" in result
    assert all(isinstance(v, float) for v in result.values())


def test_fetch_comparative_interest_uses_first_symbol():
    """Representative term is symbols[0] for each sector."""
    smap = {
        "US|Technology": ["XLK", "VGT"],
        "US|Energy": ["XLE", "IYE"],
    }

    class CapturingClient:
        def __init__(self):
            self.payloads = []
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self.payloads.append(list(kw_list))
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    client = CapturingClient()
    fetch_comparative_interest(
        smap, client=client, sleep_s=0.0, region_geos={"US": ["US"]},
    )
    all_terms = [t for p in client.payloads for t in p]
    assert "XLK" in all_terms
    assert "XLE" in all_terms
    assert "VGT" not in all_terms
    assert "IYE" not in all_terms


def test_fetch_comparative_interest_entity_resolution():
    """If an entity mid exists for symbols[0], use the mid instead."""
    smap = {"US|Technology": ["XLK"]}
    entities = {"XLK": "/m/tech_entity"}

    class CapturingClient:
        def __init__(self):
            self.payloads = []
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self.payloads.append(list(kw_list))
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    client = CapturingClient()
    fetch_comparative_interest(
        smap, client=client, sleep_s=0.0, entities=entities,
        region_geos={"US": ["US"]},
    )
    all_terms = [t for p in client.payloads for t in p]
    assert "/m/tech_entity" in all_terms
    assert "XLK" not in all_terms


def test_fetch_comparative_interest_empty_map():
    result = fetch_comparative_interest({}, sleep_s=0.0)
    assert result == {}


def test_fetch_comparative_interest_no_cache_on_failure():
    """Failed batches must not be cached — a retriggered scan should retry."""
    class FailingClient:
        def build_payload(self, kw_list, timeframe=None, geo=None):
            raise Exception("rate limited")

    smap = {"US|Technology": ["XLK"], "US|Energy": ["XLE"]}
    cache = {}
    fetch_comparative_interest(
        smap, client=FailingClient(), sleep_s=0.0, max_retries=1,
        region_geos={"US": ["US"]}, cache=cache,
    )
    assert cache.get("cmp_US", {}) == {}
