# tests/test_sector_map.py
import pytest

from src.sector_map import load_parent_map, parent_sector


def test_load_parent_map_reads_repo_config():
    pmap = load_parent_map()
    assert pmap["Banks"] == "Financials"
    assert pmap["Financial Services"] == "Financials"
    assert pmap["Insurance"] == "Financials"
    assert pmap["Basic Resources"] == "Materials"
    assert pmap["Chemicals"] == "Materials"


def test_parent_sector_identity_fallback():
    pmap = {"Banks": "Financials"}
    assert parent_sector("Banks", pmap) == "Financials"
    assert parent_sector("Technology", pmap) == "Technology"
    assert parent_sector("Utilities", {}) == "Utilities"


def test_load_parent_map_missing_key_raises(tmp_path):
    bad = tmp_path / "sector_map.yaml"
    bad.write_text("gics_sectors: [Technology]\n")
    with pytest.raises(KeyError):
        load_parent_map(str(bad))


def test_load_parent_map_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_parent_map(str(tmp_path / "nope.yaml"))
