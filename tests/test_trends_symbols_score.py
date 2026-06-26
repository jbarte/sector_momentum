import pandas as pd
from src.data.trends_symbols import score_symbol_sentiment


def test_rising_key_scores_above_falling():
    trends = {
        "US|Technology": pd.Series([1.0, 2.0, 3.0, 4.0]),   # rising
        "US|Energy": pd.Series([4.0, 3.0, 2.0, 1.0]),       # falling
        "US|Utilities": pd.Series([2.0, 2.0, 2.0, 2.0]),    # flat
    }
    s = score_symbol_sentiment(trends)
    assert set(s.index) == set(trends)
    assert s["US|Technology"] > s["US|Utilities"] > s["US|Energy"]
    # cross-sectional z is centered near zero
    assert abs(s.mean()) < 1e-9
