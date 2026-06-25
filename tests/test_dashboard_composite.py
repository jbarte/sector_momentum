import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.build import _build_composite_history


def _df():
    # 1 scan, 2 sectors × 2 regions. Composite means: Tech=(0.8+0.2)/2=0.5, Energy=(0.6+0.6)/2=0.6
    rows = [
        dict(scan_id=1, run_at="2026-06-01", region="US", gics_sector="Technology",
             composite=0.8, data_score=0.8, level_score=0.7, change_score=0.9, sentiment_score=0.0, rank=1.0),
        dict(scan_id=1, run_at="2026-06-01", region="EU", gics_sector="Technology",
             composite=0.2, data_score=0.2, level_score=0.1, change_score=0.3, sentiment_score=0.0, rank=2.0),
        dict(scan_id=1, run_at="2026-06-01", region="US", gics_sector="Energy",
             composite=0.6, data_score=0.6, level_score=0.5, change_score=0.7, sentiment_score=0.0, rank=2.0),
        dict(scan_id=1, run_at="2026-06-01", region="EU", gics_sector="Energy",
             composite=0.6, data_score=0.6, level_score=0.6, change_score=0.6, sentiment_score=0.0, rank=1.0),
    ]
    return pd.DataFrame(rows)


def test_composite_history_means_and_rank():
    out = _build_composite_history(_df())
    assert len(out) == 2                       # 2 sectors, 1 scan
    assert set(out["region"]) == {"ALL"}
    tech = out[out["gics_sector"] == "Technology"].iloc[0]
    energy = out[out["gics_sector"] == "Energy"].iloc[0]
    assert tech["composite"] == pytest.approx(0.5)
    assert tech["data_score"] == pytest.approx(0.5)
    assert energy["composite"] == pytest.approx(0.6)
    # Energy (0.6) outranks Technology (0.5): Energy rank 1, Tech rank 2
    assert energy["rank"] == pytest.approx(1.0)
    assert tech["rank"] == pytest.approx(2.0)


def test_composite_history_empty():
    out = _build_composite_history(pd.DataFrame())
    assert out.empty
