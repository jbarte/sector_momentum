"""Unit tests for the docs/data.json payload builder."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_export import build_data_export


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
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=412, scan_date="2026-07-23 06:00 UTC",
                          lagged=False, generated_at="2026-07-23T06:00:00Z")
    assert d["schema_version"] == 1
    assert d["generated_at"] == "2026-07-23T06:00:00Z"
    assert d["scan_id"] == 412
    assert d["scan_date"] == "2026-07-23 06:00 UTC"
    assert d["lagged"] is False
    assert len(d["themes"]) == 1


def test_raw_numeric_types_and_nan_to_null():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=True, generated_at="t")
    semis = next(t for t in d["themes"] if t["theme"] == "Semiconductors")
    assert semis["rank"] == 1 and isinstance(semis["rank"], int)
    assert semis["composite"] == 0.61 and isinstance(semis["composite"], float)
    assert semis["level"] == 0.6
    assert semis["delta_rank"] == 0.0 and isinstance(semis["delta_rank"], float)
    assert semis["sentiment"] is None            # NaN -> None
    assert semis["trajectory"] == "flat"
    assert semis["setup"] is None
    assert d["lagged"] is True


def test_themes_render_even_with_empty_scores_df():
    d = build_data_export(_theme_rows(), pd.DataFrame(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    t = d["themes"][0]
    assert t["theme"] == "Semiconductors"
    assert t["rank"] == 1
    assert t["level"] is None                    # empty df -> null raw scores
    assert t["setup"] is None


def test_output_is_json_serializable():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    text = json.dumps(d)                         # must not raise
    assert '"schema_version": 1' in text
    assert "NaN" not in text


def test_scan_id_none_is_null():
    d = build_data_export([], pd.DataFrame(),
                          scan_id=None, scan_date="x", lagged=False, generated_at="t")
    assert d["scan_id"] is None
    assert d["themes"] == []
