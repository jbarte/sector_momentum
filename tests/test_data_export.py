"""Unit tests for the docs/data.json payload builder."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_export import build_data_export


def _sector_rows():
    return [
        {"region": "US", "sector": "Energy", "rank": 1, "delta_rank": 2.0,
         "trajectory_state": "up", "setup": "entry", "_raw_composite": 0.98},
        {"region": "EU", "sector": "Banks", "rank": 3, "delta_rank": -1.0,
         "trajectory_state": "down", "setup": "exit", "_raw_composite": 0.10},
    ]


def _sector_scores():
    return pd.DataFrame([
        {"region": "US", "gics_sector": "Energy", "composite": 0.978,
         "level_score": 1.445, "change_score": 0.512, "data_score": 0.978,
         "sentiment_score": float("nan")},
        {"region": "EU", "gics_sector": "Banks", "composite": 0.10,
         "level_score": 0.2, "change_score": -0.1, "data_score": 0.10,
         "sentiment_score": 0.3},
    ])


def _theme_rows():
    return [
        {"theme": "Semiconductors", "rank": 1, "delta_rank": 0.0,
         "trajectory_state": "flat", "setup": None, "_raw_composite": 0.61},
    ]


def _theme_scores():
    return pd.DataFrame([
        {"gics_sector": "Semiconductors", "composite": 0.61, "level_score": 0.6,
         "change_score": 0.5, "data_score": 0.55, "sentiment_score": float("nan")},
    ])


def test_top_level_shape_and_metadata():
    d = build_data_export(_sector_rows(), _theme_rows(), _sector_scores(),
                          _theme_scores(), scan_id=412, scan_date="2026-07-23 06:00 UTC",
                          lagged=False, generated_at="2026-07-23T06:00:00Z")
    assert d["schema_version"] == 1
    assert d["generated_at"] == "2026-07-23T06:00:00Z"
    assert d["scan_id"] == 412
    assert d["scan_date"] == "2026-07-23 06:00 UTC"
    assert d["lagged"] is False
    assert len(d["sectors"]) == 2
    assert len(d["themes"]) == 1


def test_raw_numeric_types_and_nan_to_null():
    d = build_data_export(_sector_rows(), _theme_rows(), _sector_scores(),
                          _theme_scores(), scan_id=1, scan_date="x",
                          lagged=True, generated_at="t")
    energy = next(s for s in d["sectors"] if s["sector"] == "Energy")
    assert energy["rank"] == 1 and isinstance(energy["rank"], int)
    assert energy["composite"] == 0.978 and isinstance(energy["composite"], float)
    assert energy["level"] == 1.445
    assert energy["delta_rank"] == 2.0 and isinstance(energy["delta_rank"], float)
    assert energy["sentiment"] is None          # NaN -> None
    assert energy["trajectory"] == "up"
    assert energy["setup"] == "entry"
    assert d["lagged"] is True


def test_themes_render_even_with_empty_scores_df():
    d = build_data_export(_sector_rows(), _theme_rows(), _sector_scores(),
                          pd.DataFrame(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    t = d["themes"][0]
    assert t["theme"] == "Semiconductors"
    assert t["rank"] == 1
    assert t["level"] is None                    # empty df -> null raw scores
    assert t["setup"] is None


def test_output_is_json_serializable():
    d = build_data_export(_sector_rows(), _theme_rows(), _sector_scores(),
                          _theme_scores(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    text = json.dumps(d)                         # must not raise
    assert '"schema_version": 1' in text
    assert "NaN" not in text


def test_scan_id_none_is_null():
    d = build_data_export([], [], pd.DataFrame(), pd.DataFrame(),
                          scan_id=None, scan_date="x", lagged=False, generated_at="t")
    assert d["scan_id"] is None
    assert d["sectors"] == [] and d["themes"] == []
