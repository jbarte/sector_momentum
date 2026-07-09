import math
import pandas as pd
from dashboard.build import _build_sentiment_signal_rows


def test_attention_level_included_in_rows():
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Technology", "signal_name": "momentum", "value": 0.5},
        {"region": "US", "gics_sector": "Technology", "signal_name": "acceleration", "value": 0.1},
        {"region": "US", "gics_sector": "Technology", "signal_name": "range_position", "value": 0.7},
        {"region": "US", "gics_sector": "Technology", "signal_name": "spike", "value": 1.2},
        {"region": "US", "gics_sector": "Technology", "signal_name": "volatility", "value": 0.3},
        {"region": "US", "gics_sector": "Technology", "signal_name": "attention_level", "value": 85.3},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["attention"] == "85.3"


def test_attention_level_missing_shows_dash():
    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Energy", "signal_name": "momentum", "value": -0.2},
        {"region": "US", "gics_sector": "Energy", "signal_name": "acceleration", "value": 0.0},
        {"region": "US", "gics_sector": "Energy", "signal_name": "range_position", "value": 0.5},
        {"region": "US", "gics_sector": "Energy", "signal_name": "spike", "value": 0.0},
        {"region": "US", "gics_sector": "Energy", "signal_name": "volatility", "value": 0.1},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["attention"] == "—"
