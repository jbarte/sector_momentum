from src.backtest import results


def test_rotations_round_trip(tmp_path):
    out = str(tmp_path / "bt")
    rot = [{"name": "Tech run", "region": "US", "sector": "Technology",
            "ticker": "XLK", "dates": ["2019-01-31", "2019-02-28"],
            "rank": [3.0, 1.0], "composite": [0.1, 0.9], "price_indexed": [100.0, 110.0]}]
    results.write_results({"US": None, "EU": None}, out_dir=out,
                          generated_at="2026-06-27T00:00:00Z", top_n=5, rotations=rot)
    loaded = results.load_summary(out)
    assert loaded["rotations"][0]["sector"] == "Technology"
    assert loaded["rotations"][0]["price_indexed"][0] == 100.0


def test_write_results_without_rotations_defaults_empty(tmp_path):
    out = str(tmp_path / "bt")
    results.write_results({"US": None, "EU": None}, out_dir=out)
    assert results.load_summary(out)["rotations"] == []
