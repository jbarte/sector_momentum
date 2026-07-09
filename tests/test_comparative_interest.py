from src.data.trends_symbols import _build_chained_batches


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
