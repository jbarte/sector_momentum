# FinBERT News Sentiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add signed news sentiment per GICS sector via ProsusAI/finbert over GDELT headlines, replacing the directionless Google Trends slope as `sentiment_score` and surfacing four FinBERT-specific signals on the sentiment dashboard.

**Architecture:** A single new module `src/data/news_sentiment.py` fetches headlines from GDELT's DOC 2.0 API (one query per GICS sector, 11 total), scores them with the FinBERT pipeline, aggregates per-sector polarity, and z-scores cross-sectionally. `scan.py` gets a new non-fatal step 8d that overwrites the Trends-based `sentiment_score` with the FinBERT z-score and appends four info-only signal rows to `sentiment_signals_df`. The sentiment dashboard gains four new columns.

**Tech Stack:** Python 3, `transformers` (HuggingFace), `torch` (CPU-only), `requests` (GDELT HTTP), existing `sentiment_signals` DB table.

## Global Constraints

- No DDL changes — use existing `sentiment_signals` table columns (`signal_name` TEXT, `value` REAL).
- GICS sector names must match `config/universe.yaml` exactly: `Energy`, `Materials`, `Industrials`, `Consumer Discretionary`, `Consumer Staples`, `Health Care`, `Financials`, `Technology`, `Communication Services`, `Utilities`, `Real Estate`.
- `sentiment_score` override is a pure overwrite — if FinBERT fails, Trends z-score remains (fallback).
- All tests mock GDELT HTTP and FinBERT model — no network or model download in test runs.
- Step 8d uses the same non-fatal `try/except` pattern as steps 8b/8c in `scan.py`.
- `--no-finbert` CLI flag follows the same pattern as `--no-cache` and `--no-alerts`.
- EN+SV i18n for all new dashboard text.
- Sectors only — themes are unaffected.

---

### Task 1: Core news sentiment module + tests

**Files:**
- Create: `src/data/news_sentiment.py`
- Create: `tests/test_news_sentiment.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing from other tasks
- Produces:
  - `GDELT_SECTOR_THEMES: dict[str, list[str]]` — GICS sector → GDELT theme codes
  - `MIN_ARTICLES: int` (= 5)
  - `GDELT_ENDPOINT: str`
  - `fetch_news_headlines(sectors: list[str] | None = None, timespan: str = "24h", sleep_s: float = 5.0, max_retries: int = 3) -> dict[str, list[str]]` — returns `{sector: [headline, ...]}`. If `sectors` is None, uses all keys from `GDELT_SECTOR_THEMES`.
  - `score_headlines(headlines_by_sector: dict[str, list[str]], batch_size: int = 32) -> dict[str, dict]` — returns `{sector: {"mean_polarity": float, "count": int, "positive_pct": float, "negative_pct": float}}`. Uses `ProsusAI/finbert`. Sectors with < `MIN_ARTICLES` headlines get `NaN` values.
  - `zscore_polarity(scores: dict[str, dict]) -> dict[str, float]` — cross-sectional z-score of `mean_polarity` values. Returns `{sector: z_float}`. NaN inputs excluded from mean/std.

- [ ] **Step 1: Add `transformers` and `torch` to `requirements.txt`**

Append to `requirements.txt`:

```
transformers>=4.30
torch>=2.0
```

Note: CI will need `--index-url https://download.pytorch.org/whl/cpu` for torch to avoid pulling CUDA. This is handled in `.github/workflows/scan.yml` pip install line (documented in the commit message for the operator to add).

- [ ] **Step 2: Write the GDELT fetch tests**

Create `tests/test_news_sentiment.py`:

```python
"""Tests for src/data/news_sentiment.py — GDELT fetch + FinBERT scoring."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


class TestGdeltSectorThemes:
    """GDELT_SECTOR_THEMES constant must cover all 11 GICS sectors."""

    def test_all_sectors_mapped(self):
        from src.data.news_sentiment import GDELT_SECTOR_THEMES

        expected = {
            "Energy", "Materials", "Industrials", "Consumer Discretionary",
            "Consumer Staples", "Health Care", "Financials", "Technology",
            "Communication Services", "Utilities", "Real Estate",
        }
        assert set(GDELT_SECTOR_THEMES.keys()) == expected

    def test_each_sector_has_themes(self):
        from src.data.news_sentiment import GDELT_SECTOR_THEMES

        for sector, themes in GDELT_SECTOR_THEMES.items():
            assert len(themes) >= 1, f"{sector} has no theme codes"
            for t in themes:
                assert isinstance(t, str) and len(t) > 0


class TestFetchNewsHeadlines:
    """Tests for fetch_news_headlines — GDELT API interaction."""

    def _mock_response(self, articles):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"articles": articles}
        return resp

    @patch("src.data.news_sentiment.requests.get")
    def test_returns_headlines_per_sector(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Oil prices surge", "seendate": "20260717T120000Z",
             "domain": "reuters.com", "sourcecountry": "United States"},
            {"title": "Gas exports rise", "seendate": "20260717T110000Z",
             "domain": "bbc.co.uk", "sourcecountry": "United Kingdom"},
        ])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert "Energy" in result
        assert result["Energy"] == ["Oil prices surge", "Gas exports rise"]

    @patch("src.data.news_sentiment.requests.get")
    def test_empty_response(self, mock_get):
        mock_get.return_value = self._mock_response([])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_no_articles_key(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        mock_get.return_value = resp
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_http_error_retries(self, mock_get):
        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = Exception("429 Too Many Requests")
        ok_resp = self._mock_response([{"title": "Recovery", "seendate": "20260717T120000Z",
                                        "domain": "cnn.com", "sourcecountry": "United States"}])
        mock_get.side_effect = [error_resp, ok_resp]
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0, max_retries=2)
        assert result["Energy"] == ["Recovery"]

    @patch("src.data.news_sentiment.requests.get")
    def test_all_retries_exhausted(self, mock_get):
        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = Exception("429")
        mock_get.return_value = error_resp
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0, max_retries=2)
        assert result["Energy"] == []

    @patch("src.data.news_sentiment.requests.get")
    def test_multiple_sectors(self, mock_get):
        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            if "ENV_OIL" in query:
                return self._mock_response([
                    {"title": "Oil up", "seendate": "20260717T120000Z",
                     "domain": "a.com", "sourcecountry": "US"},
                ])
            return self._mock_response([
                {"title": "Banks rally", "seendate": "20260717T120000Z",
                 "domain": "b.com", "sourcecountry": "US"},
            ])
        mock_get.side_effect = side_effect
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy", "Financials"], sleep_s=0)
        assert "Energy" in result and "Financials" in result

    @patch("src.data.news_sentiment.requests.get")
    def test_deduplicates_titles(self, mock_get):
        mock_get.return_value = self._mock_response([
            {"title": "Same headline", "seendate": "20260717T120000Z",
             "domain": "a.com", "sourcecountry": "US"},
            {"title": "Same headline", "seendate": "20260717T110000Z",
             "domain": "b.com", "sourcecountry": "US"},
            {"title": "Different", "seendate": "20260717T100000Z",
             "domain": "c.com", "sourcecountry": "US"},
        ])
        from src.data.news_sentiment import fetch_news_headlines

        result = fetch_news_headlines(sectors=["Energy"], sleep_s=0)
        assert result["Energy"] == ["Same headline", "Different"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_news_sentiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.news_sentiment'`

- [ ] **Step 4: Implement GDELT fetch**

Create `src/data/news_sentiment.py`:

```python
"""FinBERT news sentiment via GDELT headlines.

Fetches recent English-language headlines per GICS sector from the GDELT DOC
2.0 API, scores them with ProsusAI/finbert, and aggregates to a single
cross-sectionally z-scored polarity value per sector.
"""

from __future__ import annotations

import logging
import math
import time

import requests

logger = logging.getLogger(__name__)

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

MIN_ARTICLES = 5

GDELT_SECTOR_THEMES: dict[str, list[str]] = {
    "Energy": [
        "ENV_OIL", "ENV_NATURALGAS", "ENV_COAL",
        "ECON_OILPRICE", "ECON_GASOLINEPRICE", "ECON_NATGASPRICE",
    ],
    "Materials": ["ENV_MINING", "ENV_METALS", "ENV_FORESTRY"],
    "Industrials": ["WB_1281_MANUFACTURING", "WB_1068_MANUFACTURING_DEVELOPMENT"],
    "Consumer Discretionary": ["ECON_HOUSING_PRICES", "TOURISM"],
    "Consumer Staples": ["AGRICULTURE", "WB_435_AGRICULTURE_AND_FOOD_SECURITY"],
    "Health Care": ["GENERAL_HEALTH", "MEDICAL"],
    "Financials": [
        "ECON_STOCKMARKET", "ECON_CENTRALBANK",
        "ECON_INTEREST_RATES", "ECON_DEBT",
    ],
    "Technology": [
        "CYBER_ATTACK", "TECH_AUTOMATION", "TECH_BIGDATA",
        "WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES",
    ],
    "Communication Services": ["MEDIA", "WB_1286_TELECOMMUNICATIONS"],
    "Utilities": ["WB_508_POWER_SYSTEMS", "WB_137_WATER", "WATER_SECURITY"],
    "Real Estate": [
        "WB_904_HOUSING_MARKETS", "WB_870_HOUSING_CONSTRUCTION",
        "ECON_HOUSING_PRICES",
    ],
}


def _build_query(themes: list[str]) -> str:
    """Build the GDELT query string from a list of theme codes."""
    theme_clause = " OR ".join(f"theme:{t}" for t in themes)
    return f"({theme_clause}) sourcelang:english"


def fetch_news_headlines(
    sectors: list[str] | None = None,
    timespan: str = "24h",
    sleep_s: float = 5.0,
    max_retries: int = 3,
) -> dict[str, list[str]]:
    """Fetch recent English headlines per GICS sector from GDELT.

    Returns {sector: [headline, ...]}.  Deduplicates titles within each sector.
    Retries on HTTP errors with exponential backoff.
    """
    if sectors is None:
        sectors = list(GDELT_SECTOR_THEMES.keys())

    result: dict[str, list[str]] = {}
    for i, sector in enumerate(sectors):
        themes = GDELT_SECTOR_THEMES[sector]
        params = {
            "query": _build_query(themes),
            "mode": "ArtList",
            "maxrecords": 250,
            "format": "json",
            "timespan": timespan,
            "sort": "datedesc",
        }

        titles: list[str] = []
        for attempt in range(max_retries):
            try:
                resp = requests.get(GDELT_ENDPOINT, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = 60 * (2 ** attempt)
                    logger.warning("GDELT 429 for %s — backing off %ds", sector, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                articles = resp.json().get("articles", [])
                seen: set[str] = set()
                for art in articles:
                    title = art.get("title", "").strip()
                    if title and title not in seen:
                        seen.add(title)
                        titles.append(title)
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 60 * (2 ** attempt)
                    logger.warning("GDELT fetch failed for %s (%s) — retry in %ds", sector, exc, wait)
                    time.sleep(wait)
                else:
                    logger.warning("GDELT fetch failed for %s after %d retries — skipping", sector, max_retries)

        result[sector] = titles
        if i < len(sectors) - 1 and sleep_s > 0:
            time.sleep(sleep_s)

    return result
```

- [ ] **Step 5: Run GDELT fetch tests**

Run: `pytest tests/test_news_sentiment.py::TestGdeltSectorThemes tests/test_news_sentiment.py::TestFetchNewsHeadlines -v`
Expected: PASS

- [ ] **Step 6: Write the FinBERT scoring tests**

Append to `tests/test_news_sentiment.py`:

```python
class TestScoreHeadlines:
    """Tests for score_headlines — FinBERT inference + aggregation."""

    def _mock_pipeline_output(self, headlines):
        """Simulate FinBERT pipeline output."""
        results = []
        for h in headlines:
            if "surge" in h.lower() or "rally" in h.lower() or "up" in h.lower():
                results.append({"label": "positive", "score": 0.85})
            elif "crash" in h.lower() or "fall" in h.lower() or "down" in h.lower():
                results.append({"label": "negative", "score": 0.90})
            else:
                results.append({"label": "neutral", "score": 0.70})
        return results

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_basic_scoring(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: self._mock_pipeline_output(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({
            "Energy": ["Oil prices surge", "Gas exports up", "Energy news today",
                        "Prices fall hard", "Market update", "Sector news"],
        })
        assert "Energy" in result
        s = result["Energy"]
        assert s["count"] == 6
        assert isinstance(s["mean_polarity"], float)
        assert 0.0 <= s["positive_pct"] <= 1.0
        assert 0.0 <= s["negative_pct"] <= 1.0
        assert abs(s["positive_pct"] + s["negative_pct"] + (1 - s["positive_pct"] - s["negative_pct"])) < 0.01

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_below_min_articles(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: self._mock_pipeline_output(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines, MIN_ARTICLES

        result = score_headlines({"Energy": ["One headline"] * (MIN_ARTICLES - 1)})
        assert math.isnan(result["Energy"]["mean_polarity"])
        assert result["Energy"]["count"] == MIN_ARTICLES - 1

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_empty_headlines(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: []
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": []})
        assert math.isnan(result["Energy"]["mean_polarity"])
        assert result["Energy"]["count"] == 0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_polarity_sign(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "positive", "score": 0.9}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": ["good"] * 10})
        assert result["Energy"]["mean_polarity"] > 0
        assert result["Energy"]["positive_pct"] == 1.0
        assert result["Energy"]["negative_pct"] == 0.0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_negative_polarity(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "negative", "score": 0.8}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({"Energy": ["bad"] * 10})
        assert result["Energy"]["mean_polarity"] < 0
        assert result["Energy"]["negative_pct"] == 1.0

    @patch("src.data.news_sentiment._load_finbert_pipeline")
    def test_multiple_sectors(self, mock_load):
        pipe = MagicMock()
        pipe.side_effect = lambda texts, **kw: [{"label": "positive", "score": 0.9}] * len(texts)
        mock_load.return_value = pipe
        from src.data.news_sentiment import score_headlines

        result = score_headlines({
            "Energy": ["good"] * 10,
            "Financials": ["great"] * 10,
        })
        assert "Energy" in result and "Financials" in result


class TestZscorePolarity:
    """Tests for zscore_polarity — cross-sectional z-score of mean_polarity."""

    def test_basic_zscore(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": -0.3, "count": 40, "positive_pct": 0.2, "negative_pct": 0.6},
            "Technology": {"mean_polarity": 0.1, "count": 60, "positive_pct": 0.5, "negative_pct": 0.3},
        }
        result = zscore_polarity(scores)
        assert len(result) == 3
        assert result["Energy"] > result["Technology"] > result["Financials"]
        total = sum(result.values())
        assert abs(total) < 1e-10

    def test_nan_excluded(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": float("nan"), "count": 2, "positive_pct": 0.0, "negative_pct": 0.0},
            "Technology": {"mean_polarity": -0.5, "count": 60, "positive_pct": 0.2, "negative_pct": 0.7},
        }
        result = zscore_polarity(scores)
        assert not math.isnan(result["Energy"])
        assert math.isnan(result["Financials"])
        assert not math.isnan(result["Technology"])

    def test_single_valid_sector(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            "Energy": {"mean_polarity": 0.5, "count": 50, "positive_pct": 0.8, "negative_pct": 0.1},
            "Financials": {"mean_polarity": float("nan"), "count": 0, "positive_pct": 0.0, "negative_pct": 0.0},
        }
        result = zscore_polarity(scores)
        assert result["Energy"] == 0.0

    def test_all_same_polarity(self):
        from src.data.news_sentiment import zscore_polarity

        scores = {
            s: {"mean_polarity": 0.3, "count": 50, "positive_pct": 0.6, "negative_pct": 0.2}
            for s in ["Energy", "Financials", "Technology"]
        }
        result = zscore_polarity(scores)
        for v in result.values():
            assert v == 0.0
```

- [ ] **Step 7: Implement FinBERT scoring + z-score**

Append to `src/data/news_sentiment.py`:

```python
_finbert_pipeline = None


def _load_finbert_pipeline():
    """Load ProsusAI/finbert pipeline (cached after first call)."""
    global _finbert_pipeline
    if _finbert_pipeline is None:
        from transformers import pipeline
        logger.info("Loading ProsusAI/finbert model (CPU) …")
        _finbert_pipeline = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            device="cpu",
        )
        logger.info("FinBERT model loaded.")
    return _finbert_pipeline


def _signed_polarity(label: str, score: float) -> float:
    """Convert FinBERT output to signed polarity float."""
    if label == "positive":
        return score
    if label == "negative":
        return -score
    return 0.0


def score_headlines(
    headlines_by_sector: dict[str, list[str]],
    batch_size: int = 32,
) -> dict[str, dict]:
    """Score headlines per sector with FinBERT.

    Returns {sector: {mean_polarity, count, positive_pct, negative_pct}}.
    Sectors with < MIN_ARTICLES headlines get NaN for mean_polarity.
    """
    pipe = _load_finbert_pipeline()
    nan = float("nan")

    all_headlines: list[str] = []
    sector_slices: list[tuple[str, int, int]] = []
    for sector, titles in headlines_by_sector.items():
        start = len(all_headlines)
        all_headlines.extend(titles)
        sector_slices.append((sector, start, len(all_headlines)))

    if not all_headlines:
        return {
            sector: {"mean_polarity": nan, "count": 0, "positive_pct": nan, "negative_pct": nan}
            for sector in headlines_by_sector
        }

    logger.info("Scoring %d headlines with FinBERT (batch_size=%d) …", len(all_headlines), batch_size)
    raw_results = pipe(all_headlines, batch_size=batch_size, truncation=True)

    result: dict[str, dict] = {}
    for sector, start, end in sector_slices:
        sector_results = raw_results[start:end]
        count = len(sector_results)

        if count < MIN_ARTICLES:
            result[sector] = {"mean_polarity": nan, "count": count, "positive_pct": nan, "negative_pct": nan}
            continue

        polarities = [_signed_polarity(r["label"], r["score"]) for r in sector_results]
        pos_count = sum(1 for r in sector_results if r["label"] == "positive")
        neg_count = sum(1 for r in sector_results if r["label"] == "negative")

        result[sector] = {
            "mean_polarity": sum(polarities) / count,
            "count": count,
            "positive_pct": pos_count / count,
            "negative_pct": neg_count / count,
        }

    return result


def zscore_polarity(scores: dict[str, dict]) -> dict[str, float]:
    """Cross-sectional z-score of mean_polarity values.

    NaN inputs (sectors below MIN_ARTICLES) excluded from mean/std calculation.
    Returns {sector: z_float}.
    """
    raw = {s: d["mean_polarity"] for s, d in scores.items()}
    valid = {s: v for s, v in raw.items() if not math.isnan(v)}

    if len(valid) < 2:
        return {s: 0.0 if not math.isnan(v) else float("nan") for s, v in raw.items()}

    arr = list(valid.values())
    mean = sum(arr) / len(arr)
    std = (sum((x - mean) ** 2 for x in arr) / (len(arr) - 1)) ** 0.5

    if std == 0.0:
        return {s: 0.0 for s in raw}

    return {
        s: (v - mean) / std if not math.isnan(v) else float("nan")
        for s, v in raw.items()
    }
```

- [ ] **Step 8: Run all Task 1 tests**

Run: `pytest tests/test_news_sentiment.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/data/news_sentiment.py tests/test_news_sentiment.py requirements.txt
git commit -m "feat: add FinBERT news sentiment module with GDELT fetch

New module src/data/news_sentiment.py:
- GDELT DOC 2.0 API fetch (11 GICS sectors, English-only, 24h window)
- ProsusAI/finbert inference (CPU, batched)
- Per-sector aggregation (mean polarity, article count, pos/neg %)
- Cross-sectional z-score of polarity

Adds transformers and torch to requirements.txt."
```

---

### Task 2: scan.py integration + backlog

**Files:**
- Modify: `scan.py:54-83` (CLI args), `scan.py:485-498` (after step 8c, before scoring)
- Modify: `config/weights.yaml:1-4` (comment update)
- Modify: `BACKLOG.md` (delete Queued section, add Done entry)

**Interfaces:**
- Consumes from Task 1:
  - `fetch_news_headlines(sectors=None, timespan="24h", sleep_s=5.0, max_retries=3) -> dict[str, list[str]]`
  - `score_headlines(headlines_by_sector, batch_size=32) -> dict[str, dict]`
  - `zscore_polarity(scores) -> dict[str, float]`
- Produces: step 8d block in `scan.py` that overwrites `sentiment_score` and appends signal rows to `sentiment_signals_df`

- [ ] **Step 1: Add `--no-finbert` CLI flag**

In `scan.py`, after the `--no-alerts` argument (line 82), add:

```python
    parser.add_argument(
        "--no-finbert",
        action="store_true",
        help="Skip FinBERT news sentiment step (avoids ~400MB model download).",
    )
```

- [ ] **Step 2: Add step 8d block**

In `scan.py`, after the trends cache save (line 488: `trends_cache.save_cache(...)`) and before the scoring block (line 490: `logger.info("Scoring sectors …")`), insert:

```python
    # ------------------------------------------------------------------
    # Step 8d: FinBERT news sentiment (signed polarity from GDELT headlines)
    # ------------------------------------------------------------------
    if not args.no_finbert:
        logger.info("Fetching GDELT headlines + FinBERT scoring …")
        try:
            from src.data.news_sentiment import (
                fetch_news_headlines, score_headlines, zscore_polarity,
            )
            _headlines = fetch_news_headlines()
            _total_articles = sum(len(h) for h in _headlines.values())
            logger.info("GDELT: %d headlines across %d sectors",
                        _total_articles, len(_headlines))

            _finbert_scores = score_headlines(_headlines)
            _finbert_z = zscore_polarity(_finbert_scores)

            _live_finbert = sum(1 for v in _finbert_z.values() if not math.isnan(v))
            logger.info("FinBERT: %d/%d sectors scored", _live_finbert, len(_finbert_z))

            if _live_finbert >= 2:
                for key in sentiment_score.index:
                    _region, _, _sector = key.partition("|")
                    if _sector in _finbert_z and not math.isnan(_finbert_z[_sector]):
                        sentiment_score[key] = _finbert_z[_sector]
                logger.info("sentiment_score overwritten with FinBERT polarity z-scores")

            _finbert_signal_rows = []
            for _sector, _sc in _finbert_scores.items():
                for _region in ("US", "EU"):
                    _finbert_signal_rows.extend([
                        {"region": _region, "gics_sector": _sector,
                         "signal_name": "news_polarity", "value": _sc["mean_polarity"]},
                        {"region": _region, "gics_sector": _sector,
                         "signal_name": "news_count", "value": float(_sc["count"])},
                        {"region": _region, "gics_sector": _sector,
                         "signal_name": "news_positive_pct", "value": _sc["positive_pct"]},
                        {"region": _region, "gics_sector": _sector,
                         "signal_name": "news_negative_pct", "value": _sc["negative_pct"]},
                    ])
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_finbert_signal_rows)],
                ignore_index=True,
            )
        except Exception as exc:
            logger.warning("FinBERT sentiment failed (%s) — continuing with Trends score", exc)
    else:
        logger.info("FinBERT sentiment skipped (--no-finbert)")
```

Also add `import math` to the top of `scan.py` if it is not already imported (check — it was noted as removed in a prior cleanup; the `math` module is needed for `math.isnan`).

- [ ] **Step 3: Update `config/weights.yaml` comment**

Replace lines 1-4 of `config/weights.yaml`:

```yaml
# Sentiment uses FinBERT news polarity (ProsusAI/finbert over GDELT headlines).
# The composite score is pure data by default (blend_sentiment=False); the
# dashboard toggle lets the user blend sentiment at a configurable weight.
# Google Trends signals remain info-only on the sentiment page.
```

- [ ] **Step 4: Update `BACKLOG.md`**

Delete the entire "Multilingual news sentiment (FinBERT)" Queued section.

Add at the top of Done:

```markdown
- **FinBERT news sentiment** — signed (positive/negative) news polarity per
  GICS sector using ProsusAI/finbert over GDELT DOC 2.0 API headlines
  (English, 24h window, 11 sector queries via GDELT theme codes). Replaces
  the directionless Google Trends slope as `sentiment_score` in the composite
  scoring path, making the dashboard's blend toggle meaningful. Google Trends
  derived signals stay info-only. Four new info columns on the sentiment page:
  Polarity, Articles, Pos%, Neg%. Non-fatal step 8d in scan.py with
  `--no-finbert` CLI flag; Trends z-score is the fallback if FinBERT fails.
  `src/data/news_sentiment.py` handles GDELT fetch, FinBERT inference, and
  cross-sectional z-scoring. Sectors only — themes stay Trends-only. EN+SV
  i18n. No DDL changes. *(2026-07-17)*
```

- [ ] **Step 5: Commit**

```bash
git add scan.py config/weights.yaml BACKLOG.md
git commit -m "feat: wire FinBERT sentiment into scan pipeline (step 8d)

Non-fatal step 8d: fetches GDELT headlines, scores with FinBERT,
overwrites sentiment_score with polarity z-scores. --no-finbert CLI
flag to skip. Trends z-score is the fallback if FinBERT fails.
Appends news_polarity/count/positive_pct/negative_pct to
sentiment_signals_df."
```

---

### Task 3: Dashboard surfacing + i18n

**Files:**
- Modify: `dashboard/sentiment.py:9-63` (add four new columns to `_build_sentiment_signal_rows`)
- Modify: `dashboard/templates/sentiment.html.j2:19-50` (add four columns to the macro)
- Modify: `dashboard/templates/i18n/_sentiment.js.j2` (add SV translations)

**Interfaces:**
- Consumes: signal rows with `signal_name` in `{"news_polarity", "news_count", "news_positive_pct", "news_negative_pct"}` from `sentiment_signals_df` (produced by Task 2)
- Produces: four new columns visible on the sentiment page for sectors (themes unaffected)

- [ ] **Step 1: Modify `_build_sentiment_signal_rows` in `dashboard/sentiment.py`**

In `dashboard/sentiment.py`, modify the `_build_sentiment_signal_rows` function. In the `rows.append({...})` dict (lines 49-61), add four new keys after `"seasonal_ratio"` and before `"rising_queries"`:

```python
        rows.append({
            "region": region,
            "sector": sector,
            "_momentum": vals.get("momentum") or 0.0,
            "momentum": _fmt(vals.get("momentum")),
            "acceleration": _fmt(vals.get("acceleration")),
            "range_position": _fmt(vals.get("range_position"), pct=True),
            "spike": _fmt(vals.get("spike")),
            "volatility": _fmt(vals.get("volatility"), pct=True),
            "attention": _fmt_attn(vals.get("attention_level")),
            "seasonal_ratio": _fmt_seasonal(vals.get("seasonal_ratio")),
            "news_polarity": _fmt(vals.get("news_polarity")),
            "news_count": str(int(vals["news_count"])) if vals.get("news_count") is not None and not (isinstance(vals.get("news_count"), float) and math.isnan(vals["news_count"])) else "—",
            "news_positive_pct": _fmt(vals.get("news_positive_pct"), pct=True),
            "news_negative_pct": _fmt(vals.get("news_negative_pct"), pct=True),
            "rising_queries": rising,
        })
```

- [ ] **Step 2: Modify `sentiment.html.j2` macro**

In `dashboard/templates/sentiment.html.j2`, update the `sentiment_table` macro.

Update the `span` calculation (line 20) to add 4:

```jinja2
{% set span = 14 if show_region else 13 %}
```

Add four new `<th>` elements after the Seasonal header (line 34):

```html
        <th data-i18n="sent_col_seasonal">Seasonal</th>
        <th data-i18n="sent_col_news_polarity">Polarity</th>
        <th data-i18n="sent_col_news_count">Articles</th>
        <th data-i18n="sent_col_news_pos">Pos%</th>
        <th data-i18n="sent_col_news_neg">Neg%</th>
```

Add four new `<td>` elements after the seasonal cell (line 49):

```html
        <td class="seasonal{% if r.seasonal_ratio != '—' %} {% if r.seasonal_ratio.replace('x','')|float > 1.0 %}seasonal-hi{% elif r.seasonal_ratio.replace('x','')|float < 1.0 %}seasonal-lo{% endif %}{% endif %}">{{ r.seasonal_ratio }}</td>
        <td class="{% if r.news_polarity != '—' %}{% if r.news_polarity.startswith('+') %}signal-hi{% elif r.news_polarity.startswith('-') %}signal-lo{% endif %}{% endif %}">{{ r.news_polarity }}</td>
        <td>{{ r.news_count }}</td>
        <td>{{ r.news_positive_pct }}</td>
        <td>{{ r.news_negative_pct }}</td>
```

- [ ] **Step 3: Add i18n translations**

In `dashboard/templates/i18n/_sentiment.js.j2`, add four new keys to the `Object.assign(SV, {...})` block:

```javascript
  sent_col_news_polarity: "Polaritet",
  sent_col_news_count: "Artiklar",
  sent_col_news_pos: "Pos%",
  sent_col_news_neg: "Neg%",
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/sentiment.py dashboard/templates/sentiment.html.j2 dashboard/templates/i18n/_sentiment.js.j2
git commit -m "feat: surface FinBERT signals on sentiment dashboard

Four new columns in the sentiment signal table: Polarity (signed,
green/red), Articles (count), Pos%, Neg%. EN+SV i18n. Themes table
unaffected (no FinBERT data for themes)."
```
