from src.backtest.rotations import load_rotations


def test_load_rotations_reads_seeded_file():
    rots = load_rotations("config/rotations.yaml")
    assert isinstance(rots, list) and len(rots) >= 1
    r = rots[0]
    assert {"name", "region", "gics_sector", "start", "end"} <= set(r)


def test_load_rotations_missing_file_returns_empty():
    assert load_rotations("config/does_not_exist.yaml") == []
