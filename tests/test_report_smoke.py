"""Pytest tests for the report generator module."""
import os
import tempfile

import pandas as pd
import pytest

from src.report import build_ranked_table, build_movers, build_swedish_overlay, write_report


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_scores():
    return pd.DataFrame(
        {
            "region": ["US", "US", "EU", "EU"],
            "gics_sector": ["Technology", "Energy", "Financials", "Industrials"],
            "composite": [0.8, 0.5, 0.3, -0.2],
            "level_score": [0.7, 0.4, 0.2, -0.3],
            "change_score": [0.9, 0.6, 0.4, -0.1],
            "data_score": [0.8, 0.5, 0.3, -0.2],
            "rank": [1.0, 2.0, 3.0, 4.0],
            "delta_composite": [0.1, -0.05, 0.2, -0.1],
            "delta_rank": [1, 0, 2, -1],
            "emerging_flag": [False, False, True, False],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_ranked_table_has_header(sample_scores):
    """build_ranked_table should produce a markdown table with a Rank header."""
    table = build_ranked_table(sample_scores)
    assert "| Rank |" in table


def test_build_ranked_table_contains_sector(sample_scores):
    """build_ranked_table should contain the top sector name."""
    table = build_ranked_table(sample_scores)
    assert "Technology" in table


def test_build_ranked_table_emerging_flag(sample_scores):
    """An emerging sector should show the seedling emoji in the table."""
    table = build_ranked_table(sample_scores)
    assert "\U0001f331" in table  # 🌱


def test_build_movers_contains_climbers(sample_scores):
    """build_movers output should include a 'Climbers' section."""
    movers_str = build_movers(sample_scores)
    assert "Climbers" in movers_str


def test_build_swedish_overlay_returns_string(sample_scores):
    """build_swedish_overlay should return a non-empty string."""
    result = build_swedish_overlay(sample_scores, top_n=3)
    assert isinstance(result, str)
    assert len(result) > 0


def test_write_report_creates_file(sample_scores):
    """write_report should create a file that contains required content."""
    table = build_ranked_table(sample_scores)
    movers_str = build_movers(sample_scores)
    swedish_str = build_swedish_overlay(sample_scores, top_n=3)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = write_report("2024-01-15", table, movers_str, swedish_str, output_dir=tmpdir)
        assert os.path.exists(path)
        content = open(path).read()
        assert "Sector Momentum Report" in content
        assert "not investment advice" in content
