import math

from src.data.trends_symbols import _build_chained_batches, _rescale_chain


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
