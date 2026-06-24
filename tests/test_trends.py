"""Tests for the Google Trends search momentum loader."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.trends import fetch_trends


@pytest.fixture
def keywords():
    return {
        "Technology": ["semiconductor", "AI", "cloud"],
        "Energy": ["oil", "gas", "crude"],
    }


def _mock_pytrends(interest_values: list[float], keyword: str) -> MagicMock:
    pt = MagicMock()
    idx = pd.date_range("2026-01-01", periods=len(interest_values), freq="W")
    pt.interest_over_time.return_value = pd.DataFrame(
        {keyword: interest_values}, index=idx
    )
    return pt


def test_returns_series_per_sector(keywords, tmp_path):
    values = list(range(13))
    mock_pt = _mock_pytrends(values, "semiconductor")

    with patch("src.data.trends.TrendReq", return_value=mock_pt):
        result = fetch_trends(keywords, cache_dir=str(tmp_path))

    assert result is not None
    assert "Technology" in result
    assert isinstance(result["Technology"], pd.Series)
    assert len(result["Technology"]) == 13


def test_returns_none_on_error(keywords, tmp_path):
    with patch("src.data.trends.TrendReq", side_effect=Exception("429")):
        result = fetch_trends(keywords, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache_on_second_call(keywords, tmp_path):
    cached = {"Technology": list(range(13)), "Energy": list(range(13, 26))}
    cache_file = tmp_path / f"trends_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.trends.TrendReq") as mock_cls:
        result = fetch_trends(keywords, cache_dir=str(tmp_path))
        mock_cls.assert_not_called()

    assert result is not None
    assert len(result["Technology"]) == 13


# ---------------------------------------------------------------------------
# Resilience: retries, partial success, total failure
# ---------------------------------------------------------------------------

def _df_with_columns(kw_cols: list[str], n: int = 13) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="W")
    return pd.DataFrame({kw: list(range(n)) for kw in kw_cols}, index=idx)


def _six_sector_keywords() -> dict[str, list[str]]:
    # 6 sectors → two batches (5 + 1) at _BATCH=5
    names = ["A", "B", "C", "D", "E", "F"]
    return {n: [f"kw_{n}"] for n in names}


def test_retries_then_succeeds(keywords, tmp_path):
    """A batch that 429s once then succeeds is retried, not abandoned."""
    df = _df_with_columns(["semiconductor", "oil"])
    pt = MagicMock()
    # First interest_over_time call raises, second returns data.
    pt.interest_over_time.side_effect = [Exception("429"), df]

    with patch("src.data.trends.TrendReq", return_value=pt), \
         patch("src.data.trends.time.sleep"):  # no real backoff wait
        result = fetch_trends(keywords, cache_dir=str(tmp_path))

    assert result is not None
    assert len(result["Technology"]) == 13
    assert pt.interest_over_time.call_count == 2  # retried once
    # Full success → cached
    assert (tmp_path / f"trends_{datetime.now().strftime('%Y-%m-%d')}.json").exists()


def test_partial_success_fills_failed_batch_and_skips_cache(tmp_path):
    """If one batch keeps failing, its sectors go neutral but the rest survive,
    and the partial result is NOT cached (so it can be retried later)."""
    kws = _six_sector_keywords()
    batch1_cols = [f"kw_{n}" for n in ["A", "B", "C", "D", "E"]]
    df1 = _df_with_columns(batch1_cols)
    pt = MagicMock()
    # batch1: one successful call. batch2: 3 failed attempts.
    pt.interest_over_time.side_effect = [df1, Exception("429"), Exception("429"), Exception("429")]

    with patch("src.data.trends.TrendReq", return_value=pt), \
         patch("src.data.trends.time.sleep"):
        result = fetch_trends(kws, cache_dir=str(tmp_path))

    assert result is not None
    # batch1 sectors got real (non-zero) data
    assert result["A"].tolist()[-1] == 12.0
    # batch2 sector (F) is neutral
    assert result["F"].tolist() == [0.0] * 13
    # partial → not cached
    assert not (tmp_path / f"trends_{datetime.now().strftime('%Y-%m-%d')}.json").exists()


def test_all_batches_fail_returns_none(keywords, tmp_path):
    """If every batch exhausts retries, return None and cache nothing."""
    pt = MagicMock()
    pt.interest_over_time.side_effect = Exception("429")

    with patch("src.data.trends.TrendReq", return_value=pt), \
         patch("src.data.trends.time.sleep"):
        result = fetch_trends(keywords, cache_dir=str(tmp_path))

    assert result is None
    assert not (tmp_path / f"trends_{datetime.now().strftime('%Y-%m-%d')}.json").exists()
