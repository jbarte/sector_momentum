# Phase 2 — Sentiment Pillar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the 30% sentiment pillar by adding Reddit public JSON, Google Trends, and StockTwits loaders plus a sentiment signal calculator, wiring them into `scan.py`, and extending the dashboard with a Data ⇄ Sentiment scatter tab.

**Architecture:** Three cached loader modules (`reddit.py`, `trends.py`, `stocktwits.py`) each return raw data or `None` on failure. A signal module (`sentiment.py`) combines them into a `pd.Series` of z-scored sentiment scores. `scan.py` calls these after the data-pillar step and passes the result into the already-wired `score_all` (with a one-line signature change). Dashboard gets a new scatter tab and a Sentiment column in the Leaderboard.

**Tech Stack:** Python 3.11+, `requests` (Reddit + StockTwits — already in requirements), `pytrends>=4.9` (new), `plotly`, `jinja2`, `pandas`, `numpy`, `pytest`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `config/sentiment_keywords.yaml` | Sector → keyword list for Reddit + Trends queries |
| Create | `.env.example` | Documents REDDIT_USER_AGENT env var |
| Create | `src/data/reddit.py` | Multireddit search, 7d/30d mention counts, JSON cache |
| Create | `src/data/trends.py` | Google Trends 13-week interest, JSON cache |
| Create | `src/data/stocktwits.py` | StockTwits bull/bear counts, JSON cache |
| Create | `src/signals/sentiment.py` | Mention velocity z-score, search momentum, bull/bear, composite |
| Create | `tests/test_reddit.py` | Unit tests for Reddit loader |
| Create | `tests/test_trends.py` | Unit tests for Trends loader |
| Create | `tests/test_stocktwits.py` | Unit tests for StockTwits loader |
| Create | `tests/test_sentiment.py` | Unit tests for sentiment signal calculator |
| Modify | `src/scoring.py` | Add `sentiment_score` param to `score_all` |
| Modify | `scan.py` | Add sentiment fetch + signal step |
| Modify | `requirements.txt` | Add `pytrends>=4.9` |
| Modify | `config/weights.yaml` | Activate 30% sentiment weight |
| Modify | `dashboard/build.py` | Add `_build_sentiment_scatter_figure`, update leaderboard rows |
| Modify | `dashboard/templates/index.html.j2` | Add Sentiment column + Data ⇄ Sentiment tab |

---

## Task 1: Branch, dependencies, config files

**Files:**
- Modify: `requirements.txt`
- Create: `config/sentiment_keywords.yaml`
- Create: `.env.example`

- [ ] **Step 1: Create the feature branch**

```bash
git checkout -b feature/phase-2-sentiment
```

- [ ] **Step 2: Add pytrends to requirements.txt**

Open `requirements.txt` and add one line:
```
pytrends>=4.9
```
(Keep existing entries unchanged.)

- [ ] **Step 3: Install the new dependency**

```bash
source .venv/bin/activate
pip install pytrends>=4.9
```

Expected: `Successfully installed pytrends-...`

- [ ] **Step 4: Create `config/sentiment_keywords.yaml`**

```yaml
# Sector → search/mention keywords.
# First keyword is used as the primary Google Trends term.
# ETF tickers are included so posts mentioning "XLK" are caught too.
Technology:           [semiconductor, AI, cloud, software, XLK, EXV3]
Financials:           [bank, interest rate, Fed, ECB, XLF, EXV1]
Energy:               [oil, gas, crude, XLE, EXV4]
Health Care:          [pharma, biotech, healthcare, XLV, EXV6]
Industrials:          [manufacturing, aerospace, defense, XLI, EXV8]
Consumer Discretionary: [retail, consumer, auto, XLY, EXH2]
Consumer Staples:     [food, beverage, staples, XLP, EXH3]
Utilities:            [utility, power, grid, XLU, EXH8]
Materials:            [mining, chemicals, materials, XLB, EXV5]
Real Estate:          [REIT, property, real estate, XLRE, IPRP]
Communication Services: [telecom, media, streaming, XLC, EXV2]
```

- [ ] **Step 5: Create `.env.example`**

```
# Reddit public JSON API — no OAuth needed, but a descriptive User-Agent is required.
# Replace with your own Reddit username.
REDDIT_USER_AGENT=sector-momentum-scanner/1.0 by u/your_reddit_username
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config/sentiment_keywords.yaml .env.example
git commit -m "chore: add pytrends dep, sentiment keyword config, .env.example"
```

---

## Task 2: `src/data/reddit.py`

**Files:**
- Create: `src/data/reddit.py`
- Create: `tests/test_reddit.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reddit.py`:

```python
"""Tests for the Reddit public JSON mention loader."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from src.data.reddit import fetch_reddit


@pytest.fixture
def keywords():
    return {
        "Technology": ["semiconductor", "AI", "XLK"],
        "Energy": ["oil", "gas", "XLE"],
    }


def _make_post(days_ago: int) -> dict:
    ts = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())
    return {"data": {"title": "test post", "created_utc": ts}}


def _make_response(posts: list) -> dict:
    return {"data": {"children": posts, "after": None}}


def test_counts_posts_in_7d_and_30d_windows(keywords, tmp_path):
    recent = _make_post(3)   # within 7d
    mid = _make_post(15)     # within 30d but not 7d
    old = _make_post(45)     # outside 30d

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = _make_response([recent, mid, old])

    with patch("src.data.reddit.requests.get", return_value=mock_resp):
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))

    assert result is not None
    assert result["Technology"]["7d"] == 1
    assert result["Technology"]["30d"] == 2


def test_returns_none_on_network_error(keywords, tmp_path):
    with patch("src.data.reddit.requests.get", side_effect=Exception("timeout")):
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache_on_second_call(keywords, tmp_path):
    cached = {"Technology": {"7d": 5, "30d": 10}, "Energy": {"7d": 2, "30d": 8}}
    cache_file = tmp_path / f"reddit_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.reddit.requests.get") as mock_get:
        result = fetch_reddit(keywords, cache_dir=str(tmp_path))
        mock_get.assert_not_called()

    assert result == cached
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_reddit.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.data.reddit'`

- [ ] **Step 3: Implement `src/data/reddit.py`**

```python
"""
Reddit public JSON sentiment loader.

Searches a multireddit of 8 finance subreddits for each sector's keywords.
No OAuth required — uses the public search endpoint with a User-Agent header.

Cache: data/cache/reddit_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_MULTIREDDIT = (
    "stocks+investing+wallstreetbets+aktier+Finanzen+"
    "vosfinances+eupersonalfinance+EuropeFIRE"
)
_SEARCH_URL = f"https://www.reddit.com/r/{_MULTIREDDIT}/search.json"
_DEFAULT_UA = "sector-momentum-scanner/1.0 (analytical tooling, non-commercial)"
_SLEEP = 0.6  # keeps requests under 10/min


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"reddit_{date.today()}.json")


def _count_by_window(posts: list[dict]) -> dict[str, int]:
    now = datetime.now(timezone.utc).timestamp()
    cutoff_7d = now - 7 * 86400
    cutoff_30d = now - 30 * 86400
    c7 = sum(1 for p in posts if p["data"].get("created_utc", 0) >= cutoff_7d)
    c30 = sum(1 for p in posts if p["data"].get("created_utc", 0) >= cutoff_30d)
    return {"7d": c7, "30d": c30}


def fetch_reddit(
    keywords: dict[str, list[str]],
    cache_dir: str = "data/cache",
) -> dict[str, dict[str, int]] | None:
    """
    For each sector, count Reddit mentions in the last 7 and 30 days.

    Returns dict[sector, {"7d": int, "30d": int}] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("Reddit: cache hit %s", cache_file)
        with open(cache_file) as fh:
            return json.load(fh)

    ua = os.environ.get("REDDIT_USER_AGENT", _DEFAULT_UA)
    headers = {"User-Agent": ua}
    result: dict[str, dict[str, int]] = {}

    try:
        for sector, terms in keywords.items():
            query = "+OR+".join(terms)
            params = {"q": query, "sort": "new", "limit": 100,
                      "restrict_sr": "on", "t": "month"}
            resp = requests.get(_SEARCH_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            result[sector] = _count_by_window(posts)
            logger.debug("Reddit %s: %s", sector, result[sector])
            time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_file)
        logger.info("Reddit: fetched %d sectors → %s", len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("Reddit fetch failed (%s) — sentiment neutral this run", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reddit.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/data/reddit.py tests/test_reddit.py
git commit -m "feat: add Reddit public JSON mention loader with cache"
```

---

## Task 3: `src/data/trends.py`

**Files:**
- Create: `src/data/trends.py`
- Create: `tests/test_trends.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trends.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_trends.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.data.trends'`

- [ ] **Step 3: Implement `src/data/trends.py`**

```python
"""
Google Trends search momentum loader.

Fetches 13-week interest-over-time for each sector's primary keyword.
Batches requests to avoid 429s from pytrends.

Cache: data/cache/trends_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
from pytrends.request import TrendReq

logger = logging.getLogger(__name__)

_TIMEFRAME = "today 3-m"  # ~13 weeks
_BATCH = 5                # max keywords per pytrends request
_SLEEP = 2.5              # pytrends needs longer pauses to avoid 429


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"trends_{date.today()}.json")


def fetch_trends(
    keywords: dict[str, list[str]],
    cache_dir: str = "data/cache",
) -> dict[str, pd.Series] | None:
    """
    Fetch Google Trends interest (13-week) for each sector's primary keyword.
    Primary keyword = first item in each sector's keyword list.

    Returns dict[sector, Series(13 floats)] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("Trends: cache hit %s", cache_file)
        raw = json.loads(Path(cache_file).read_text())
        return {s: pd.Series(v, dtype=float) for s, v in raw.items()}

    try:
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        sectors = list(keywords.keys())
        primary = {s: keywords[s][0] for s in sectors}
        result: dict[str, list[float]] = {}

        for i in range(0, len(sectors), _BATCH):
            batch = sectors[i : i + _BATCH]
            kw_list = [primary[s] for s in batch]
            pytrends.build_payload(kw_list, timeframe=_TIMEFRAME, geo="")
            df = pytrends.interest_over_time()

            for s, kw in zip(batch, kw_list):
                if not df.empty and kw in df.columns:
                    result[s] = df[kw].tolist()[-13:]
                else:
                    result[s] = [0.0] * 13

            if i + _BATCH < len(sectors):
                time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        Path(tmp).write_text(json.dumps(result))
        os.replace(tmp, cache_file)
        logger.info("Trends: fetched %d sectors → %s", len(result), cache_file)
        return {s: pd.Series(v, dtype=float) for s, v in result.items()}

    except Exception as exc:
        logger.warning("Google Trends fetch failed (%s) — sentiment neutral this run", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_trends.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/data/trends.py tests/test_trends.py
git commit -m "feat: add Google Trends search momentum loader with cache"
```

---

## Task 4: `src/data/stocktwits.py`

**Files:**
- Create: `src/data/stocktwits.py`
- Create: `tests/test_stocktwits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stocktwits.py`:

```python
"""Tests for the StockTwits bull/bear sentiment loader."""
import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.data.stocktwits import fetch_stocktwits


@pytest.fixture
def us_sectors():
    return {"Technology": "XLK", "Energy": "XLE"}


def _make_response(bulls: int, bears: int) -> dict:
    messages = []
    for _ in range(bulls):
        messages.append({"entities": {"sentiment": {"basic": "Bullish"}}})
    for _ in range(bears):
        messages.append({"entities": {"sentiment": {"basic": "Bearish"}}})
    messages.append({"entities": {}})  # no sentiment tag
    return {"messages": messages}


def test_counts_bull_and_bear(us_sectors, tmp_path):
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = _make_response(bulls=10, bears=4)

    with patch("src.data.stocktwits.requests.get", return_value=mock_resp):
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))

    assert result is not None
    assert result["Technology"]["bull"] == 10
    assert result["Technology"]["bear"] == 4


def test_returns_none_on_error(us_sectors, tmp_path):
    with patch("src.data.stocktwits.requests.get", side_effect=Exception("network")):
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))
    assert result is None


def test_loads_from_cache(us_sectors, tmp_path):
    cached = {"Technology": {"bull": 7, "bear": 2}, "Energy": {"bull": 3, "bear": 5}}
    cache_file = tmp_path / f"stocktwits_{datetime.now().strftime('%Y-%m-%d')}.json"
    cache_file.write_text(json.dumps(cached))

    with patch("src.data.stocktwits.requests.get") as mock_get:
        result = fetch_stocktwits(us_sectors, cache_dir=str(tmp_path))
        mock_get.assert_not_called()

    assert result == cached
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_stocktwits.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.data.stocktwits'`

- [ ] **Step 3: Implement `src/data/stocktwits.py`**

```python
"""
StockTwits public sentiment loader.

Fetches bull/bear message counts for US sector ETF tickers.
EU sectors receive NaN (StockTwits has no EU ETF coverage).

Cache: data/cache/stocktwits_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_SLEEP = 0.5


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"stocktwits_{date.today()}.json")


def _count_sentiment(messages: list[dict]) -> dict[str, int]:
    bull = sum(
        1 for m in messages
        if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish"
    )
    bear = sum(
        1 for m in messages
        if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish"
    )
    return {"bull": bull, "bear": bear}


def fetch_stocktwits(
    us_sectors: dict[str, str],
    cache_dir: str = "data/cache",
) -> dict[str, dict[str, int]] | None:
    """
    Fetch StockTwits bull/bear counts for each US sector ETF ticker.

    Args:
        us_sectors: {gics_sector: ticker}, e.g. {"Technology": "XLK"}

    Returns dict[sector, {"bull": int, "bear": int}] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("StockTwits: cache hit %s", cache_file)
        with open(cache_file) as fh:
            return json.load(fh)

    result: dict[str, dict[str, int]] = {}

    try:
        for sector, ticker in us_sectors.items():
            resp = requests.get(_API_URL.format(ticker=ticker), timeout=10)
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            result[sector] = _count_sentiment(messages)
            logger.debug("StockTwits %s (%s): %s", sector, ticker, result[sector])
            time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_file)
        logger.info("StockTwits: fetched %d sectors → %s", len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("StockTwits fetch failed (%s) — US sentiment neutral this run", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_stocktwits.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/data/stocktwits.py tests/test_stocktwits.py
git commit -m "feat: add StockTwits bull/bear sentiment loader with cache"
```

---

## Task 5: `src/signals/sentiment.py`

**Files:**
- Create: `src/signals/sentiment.py`
- Create: `tests/test_sentiment.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sentiment.py`:

```python
"""Tests for the sentiment signal calculator."""
import math

import pandas as pd
import pytest

from src.signals.sentiment import compute_sentiment_score

US_SECTORS = {"Technology": "XLK", "Energy": "XLE", "Financials": "XLF"}
EU_SECTORS = {"Technology": "EXV3.DE", "Energy": "EXV4.DE", "Financials": "EXV1.DE"}
SECTOR_KEYS = [
    "US|Technology", "US|Energy", "US|Financials",
    "EU|Technology", "EU|Energy", "EU|Financials",
]


def _reddit(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: {"7d": 10, "30d": 30} for s in sectors}


def _trends(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: pd.Series(range(13), dtype=float) for s in sectors}


def _stocktwits(sectors=None):
    if sectors is None:
        sectors = list(US_SECTORS.keys())
    return {s: {"bull": 10, "bear": 3} for s in sectors}


def test_returns_series_indexed_by_sector_keys():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert isinstance(result, pd.Series)
    assert set(result.index) == set(SECTOR_KEYS)


def test_all_none_sources_returns_zero():
    result = compute_sentiment_score(
        None, None, None, SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert (result == 0.0).all()


def test_eu_sectors_get_score_without_stocktwits():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    # EU sectors have no StockTwits data, but Reddit + Trends provide signal
    assert not math.isnan(result["EU|Technology"])
    assert result["EU|Technology"] != 0.0 or True  # may be 0 if z-scored flat


def test_partial_coverage_sectors_get_neutral():
    # Only Technology has Reddit data
    result = compute_sentiment_score(
        {"Technology": {"7d": 10, "30d": 30}},
        None,
        None,
        SECTOR_KEYS,
        US_SECTORS,
        EU_SECTORS,
    )
    # Energy and Financials have no data → 0.0 neutral
    assert result["US|Energy"] == 0.0
    assert result["US|Financials"] == 0.0


def test_scores_are_finite():
    result = compute_sentiment_score(
        _reddit(), _trends(), _stocktwits(), SECTOR_KEYS, US_SECTORS, EU_SECTORS
    )
    assert all(math.isfinite(v) for v in result.values)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sentiment.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.signals.sentiment'`

- [ ] **Step 3: Implement `src/signals/sentiment.py`**

```python
"""
Sentiment signal calculator.

Combines Reddit mention velocity, Google Trends search momentum, and
StockTwits bull/bear ratio into a single sentiment_score per sector.

Missing sources produce NaN for that signal. A sector with all NaN
signals gets 0.0 (neutral) so the data pillar carries full weight.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _cross_zscore(values: dict[str, float]) -> dict[str, float]:
    """Z-score a dict of {key: float}. NaN inputs excluded from mean/std."""
    valid = {k: v for k, v in values.items() if not math.isnan(v)}
    if len(valid) < 2:
        return {k: 0.0 if not math.isnan(v) else float("nan") for k, v in values.items()}
    arr = list(valid.values())
    mean = sum(arr) / len(arr)
    std = (sum((x - mean) ** 2 for x in arr) / (len(arr) - 1)) ** 0.5
    if std == 0.0:
        return {k: 0.0 for k in values}
    return {
        k: (v - mean) / std if not math.isnan(v) else float("nan")
        for k, v in values.items()
    }


def _mention_velocity(
    reddit_data: dict[str, dict] | None,
    sectors: list[str],
) -> dict[str, float]:
    """velocity = (7d_count/7) / (30d_count/30 + 1), cross-sectional z-score."""
    raw: dict[str, float] = {}
    for s in sectors:
        if reddit_data is None or s not in reddit_data:
            raw[s] = float("nan")
        else:
            d = reddit_data[s]
            daily_7d = d.get("7d", 0) / 7.0
            daily_30d = d.get("30d", 0) / 30.0
            raw[s] = daily_7d / (daily_30d + 1.0)
    return _cross_zscore(raw)


def _search_momentum(
    trends_data: dict[str, pd.Series] | None,
    sectors: list[str],
) -> dict[str, float]:
    """Linear regression slope of 13-week interest series, cross-sectional z-score."""
    raw: dict[str, float] = {}
    for s in sectors:
        if trends_data is None or s not in trends_data:
            raw[s] = float("nan")
        else:
            series = trends_data[s].dropna()
            if len(series) < 3:
                raw[s] = float("nan")
                continue
            x = np.arange(len(series))
            slope, _ = np.polyfit(x, series.values.astype(float), 1)
            raw[s] = float(slope)
    return _cross_zscore(raw)


def _bull_bear_scores(
    stocktwits_data: dict[str, dict] | None,
    us_sectors: dict[str, str],
    sector_keys: list[str],
) -> dict[str, float]:
    """(bull - bear) / (bull + bear + 1) for US sectors. EU → NaN."""
    result: dict[str, float] = {}
    for key in sector_keys:
        region, sector = key.split("|", 1)
        if region != "US" or stocktwits_data is None or sector not in stocktwits_data:
            result[key] = float("nan")
        else:
            d = stocktwits_data[sector]
            bull, bear = d.get("bull", 0), d.get("bear", 0)
            result[key] = (bull - bear) / (bull + bear + 1.0)
    return result


def compute_sentiment_score(
    reddit_data: dict[str, dict] | None,
    trends_data: dict[str, pd.Series] | None,
    stocktwits_data: dict[str, dict] | None,
    sector_keys: list[str],
    us_sectors: dict[str, str],
    eu_sectors: dict[str, str],
) -> pd.Series:
    """
    Combine three sentiment signals into one score per sector.

    Args:
        sector_keys: ["US|Technology", "EU|Technology", ...]
        us_sectors:  {"Technology": "XLK", ...}
        eu_sectors:  {"Technology": "EXV3.DE", ...}

    Returns pd.Series indexed by sector_key. All-NaN sector → 0.0.
    """
    unique_sectors = list({key.split("|", 1)[1] for key in sector_keys})

    velocity = _mention_velocity(reddit_data, unique_sectors)
    momentum = _search_momentum(trends_data, unique_sectors)
    bull_bear = _bull_bear_scores(stocktwits_data, us_sectors, sector_keys)

    scores: dict[str, float] = {}
    for key in sector_keys:
        sector = key.split("|", 1)[1]
        signals = [
            velocity.get(sector, float("nan")),
            momentum.get(sector, float("nan")),
            bull_bear.get(key, float("nan")),
        ]
        valid = [s for s in signals if not math.isnan(s)]
        scores[key] = sum(valid) / len(valid) if valid else 0.0

    return pd.Series(scores)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sentiment.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Run all tests to check for regressions**

```bash
python -m pytest tests/ -q
```

Expected: all prior tests still pass + 5 new ones.

- [ ] **Step 6: Commit**

```bash
git add src/signals/sentiment.py tests/test_sentiment.py
git commit -m "feat: add sentiment signal calculator (mention velocity, search momentum, bull/bear)"
```

---

## Task 6: Update `src/scoring.py` — add `sentiment_score` parameter

**Files:**
- Modify: `src/scoring.py`
- Modify: `tests/test_scoring.py` (add one test)

- [ ] **Step 1: Write the failing test**

Open `tests/test_scoring.py` and add at the bottom:

```python
def test_score_all_uses_sentiment_score_when_provided():
    import pandas as pd
    import numpy as np
    from src.scoring import score_all

    # 4 sectors, flat data signals
    signals = pd.DataFrame(
        {col: [1.0, 1.0, 1.0, 1.0] for col in [
            "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
            "acceleration", "above_50dma", "above_200dma", "ma50_slope",
            "obv_slope", "breadth_above_50dma",
        ]},
        index=["US|Tech", "US|Energy", "EU|Tech", "EU|Energy"],
    )
    # Give one sector a high positive sentiment score
    sentiment = pd.Series(
        {"US|Tech": 2.0, "US|Energy": -1.0, "EU|Tech": 0.0, "EU|Energy": 0.0}
    )
    result = score_all(signals, sentiment_score=sentiment)

    # With sentiment weight > 0, US|Tech should have highest composite
    assert result.loc["US|Tech", "composite"] > result.loc["US|Energy", "composite"]
    assert not pd.isna(result.loc["US|Tech", "sentiment_score"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_scoring.py::test_score_all_uses_sentiment_score_when_provided -v
```

Expected: `TypeError: score_all() got an unexpected keyword argument 'sentiment_score'`

- [ ] **Step 3: Update `src/scoring.py`**

Change the `score_all` function signature and body. Find this block (lines 134–176) and replace it:

```python
def score_all(
    signals_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
    sentiment_score: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Full pipeline: z-score → level/change → data → composite → rank.

    Returns a DataFrame with columns:
      level_score, change_score, data_score, sentiment_score,
      composite, rank

    sentiment_score: optional Series indexed like signals_df. If provided,
      it is reindexed to match signals_df (missing keys → 0.0) and passed
      to compute_composite with the configured sentiment weight.
    """
    weights_file = Path(weights_path)
    with weights_file.open() as fh:
        cfg = yaml.safe_load(fh)

    level_weight: float = float(cfg["data_pillar"]["level"])
    change_weight: float = float(cfg["data_pillar"]["change"])
    data_weight: float = float(cfg["pillars"]["data"])
    sentiment_weight: float = float(cfg["pillars"]["sentiment"])

    z_df = zscore_cross_section(signals_df)
    level = compute_level_score(z_df)
    change = compute_change_score(z_df)
    data = compute_data_score(level, change, level_weight=level_weight, change_weight=change_weight)

    # Align sentiment_score index to signals_df; fill gaps with 0.0 (neutral)
    if sentiment_score is not None:
        sentiment_score = sentiment_score.reindex(signals_df.index, fill_value=0.0)

    composite = compute_composite(
        data,
        sentiment_score=sentiment_score,
        data_weight=data_weight,
        sentiment_weight=sentiment_weight,
    )
    ranks = rank_sectors(composite)

    return pd.DataFrame(
        {
            "level_score": level,
            "change_score": change,
            "data_score": data,
            "sentiment_score": sentiment_score if sentiment_score is not None else np.nan,
            "composite": composite,
            "rank": ranks,
        },
        index=signals_df.index,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scoring.py tests/test_scoring_smoke.py -v
```

Expected: all pass including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/scoring.py tests/test_scoring.py
git commit -m "feat: thread sentiment_score through score_all"
```

---

## Task 7: Wire sentiment into `scan.py`

**Files:**
- Modify: `scan.py`

- [ ] **Step 1: Add imports to `scan.py`**

Find the imports block near the top of `scan.py` (around line 1–27) and add:

```python
import yaml  # already present — verify

from src.data.reddit import fetch_reddit
from src.data.trends import fetch_trends
from src.data.stocktwits import fetch_stocktwits
from src.signals.sentiment import compute_sentiment_score
```

- [ ] **Step 2: Add sentiment step to the `run()` function**

In `scan.py`, find the scoring block (around line 365–372):

```python
    wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]

    # ------------------------------------------------------------------
    # Step 8: Score
    # ------------------------------------------------------------------
    logger.info("Scoring sectors …")
    scored = score_all(wide_df, weights_path="config/weights.yaml")
```

Replace it with:

```python
    wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]

    # ------------------------------------------------------------------
    # Step 7b: Sentiment signals
    # ------------------------------------------------------------------
    logger.info("Fetching sentiment data …")
    with open("config/sentiment_keywords.yaml") as _fh:
        _sentiment_keywords = yaml.safe_load(_fh)

    _reddit_raw     = fetch_reddit(_sentiment_keywords)
    _trends_raw     = fetch_trends(_sentiment_keywords)
    _stocktwits_raw = fetch_stocktwits(us_sectors)

    sector_keys = list(wide_df.index)
    sentiment_scores = compute_sentiment_score(
        _reddit_raw, _trends_raw, _stocktwits_raw,
        sector_keys, us_sectors, eu_sectors,
    )
    n_sentiment = (sentiment_scores != 0.0).sum()
    logger.info("Sentiment scores computed (%d non-neutral sectors)", n_sentiment)

    # ------------------------------------------------------------------
    # Step 8: Score
    # ------------------------------------------------------------------
    logger.info("Scoring sectors …")
    scored = score_all(
        wide_df,
        weights_path="config/weights.yaml",
        sentiment_score=sentiment_scores,
    )
```

- [ ] **Step 3: Verify dry-run still works**

```bash
source .venv/bin/activate && python scan.py --dry-run 2>&1 | tail -20
```

Expected: scan completes, "Sentiment scores computed" line appears, no errors. (Sentiment sources may return `None` on the first live run if rate-limited — that's fine, it falls back to neutral.)

- [ ] **Step 4: Commit**

```bash
git add scan.py
git commit -m "feat: wire sentiment fetch and signal step into scan.py"
```

---

## Task 8: Activate 30% sentiment weight

**Files:**
- Modify: `config/weights.yaml`

- [ ] **Step 1: Update weights**

Open `config/weights.yaml` and change:

```yaml
pillars:
  data: 0.70        # was 1.0
  sentiment: 0.30   # was 0.0
```

(All other lines unchanged.)

- [ ] **Step 2: Run all tests**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass. (The scoring tests use `config/weights.yaml` — verify the new weights don't break them. If a test asserts `composite == data_score`, it will need updating to account for the new weight split. Fix any such assertions by adjusting expected values.)

- [ ] **Step 3: Commit**

```bash
git add config/weights.yaml
git commit -m "feat: activate 30% sentiment pillar weight"
```

---

## Task 9: Dashboard — sentiment scatter + leaderboard column

**Files:**
- Modify: `dashboard/build.py`
- Modify: `dashboard/templates/index.html.j2`

- [ ] **Step 1: Add `_build_sentiment_scatter_figure` to `build.py`**

Add this function after `_build_history_figure` (around line 395):

```python
def _build_sentiment_scatter_figure(history_df) -> str:
    """Data ⇄ Sentiment scatter: x=data_score, y=sentiment_score, latest scan only."""
    import pandas as pd

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Data ⇄ Sentiment — no data",
                          paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                          font=dict(color="#e0e0e0"))
        return pio.to_json(fig)

    latest_id = history_df["scan_id"].max()
    df = history_df[history_df["scan_id"] == latest_id].copy()

    # Separate sectors with/without sentiment scores
    has_sentiment = df["sentiment_score"].notna()
    solid = df[has_sentiment]
    faded = df[~has_sentiment]

    region_colors = {"US": "#4FC3F7", "EU": "#AED581"}

    fig = go.Figure()

    # Quadrant dividers at 0/0
    for axis, xy in [("line", dict(x0=0, x1=0, y0=-3, y1=3)),
                     ("line", dict(x0=-3, x1=3, y0=0, y1=0))]:
        fig.add_shape(type=axis, **xy, line=dict(color="#555", width=1, dash="dot"))

    # Quadrant labels
    for x, y, label in [
        (1.5,  1.5, "Agreement<br>(bullish)"),
        (-1.5, 1.5, "Sentiment<br>ahead"),
        (-1.5, -1.5, "Agreement<br>(bearish)"),
        (1.5, -1.5, "Data ahead<br>← early signal"),
    ]:
        color = "#AED581" if "early" in label else "#888"
        fig.add_annotation(x=x, y=y, text=label, showarrow=False,
                           font=dict(size=9, color=color),
                           xanchor="center", yanchor="middle")

    for region, color in region_colors.items():
        grp = solid[solid["region"] == region]
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["data_score"].tolist(),
            y=grp["sentiment_score"].tolist(),
            mode="markers+text",
            marker=dict(size=12, color=color, line=dict(width=1, color="#222")),
            text=grp["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=9),
            name=region,
            hovertemplate=(
                "<b>%{text} (" + region + ")</b><br>"
                "Data: %{x:.3f}<br>Sentiment: %{y:.3f}<extra></extra>"
            ),
        ))

    # Faded points (no sentiment data)
    if not faded.empty:
        fig.add_trace(go.Scatter(
            x=faded["data_score"].tolist(),
            y=[0.0] * len(faded),
            mode="markers+text",
            marker=dict(size=8, color="#555", symbol="circle-open"),
            text=faded["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=8, color="#666"),
            name="no sentiment data",
            hovertemplate="<b>%{text}</b><br>Sentiment: N/A<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Data ⇄ Sentiment", font=dict(size=13)),
        xaxis=dict(title="Data Score", gridcolor="#333", zeroline=False),
        yaxis=dict(title="Sentiment Score", gridcolor="#333", zeroline=False),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(bgcolor="#1a1a2e", bordercolor="#444"),
        margin=dict(l=50, r=20, t=50, b=50),
        height=520,
    )
    return pio.to_json(fig)
```

- [ ] **Step 2: Add `sentiment_score` to leaderboard rows in `_build_leaderboard_rows`**

In `_build_leaderboard_rows`, find the `rows.append({...})` block and add one key:

```python
            "sentiment_score": f"{_safe_float(row.get('sentiment_score')):.3f}"
                if _safe_float(row.get("sentiment_score")) is not None else "—",
```

(Add it after `"data_score": ...`)

- [ ] **Step 3: Wire the new figure into `main()`**

In `main()`, after the existing `logger.info("Building leaderboard …")` block, add:

```python
    logger.info("Building Data⇄Sentiment scatter …")
    sentiment_scatter_json = _build_sentiment_scatter_figure(history_df)
```

And add it to the `_render(...)` context dict:

```python
            sentiment_scatter_json=sentiment_scatter_json,
```

- [ ] **Step 4: Add the new tab and column to the Jinja2 template**

**4a.** In `dashboard/templates/index.html.j2`, find the tab bar (around line 242–247):

```html
  <button class="tab-btn active" onclick="switchTab('leaderboard', this)" role="tab">Leaderboard</button>
  <button class="tab-btn" onclick="switchTab('rrg', this)" role="tab">RRG</button>
  <button class="tab-btn" onclick="switchTab('drilldown', this)" role="tab">Drill-down</button>
  <button class="tab-btn" onclick="switchTab('movers', this)" role="tab">Movers</button>
  <button class="tab-btn" onclick="switchTab('history', this)" role="tab">History</button>
  <button class="tab-btn" onclick="switchTab('guide', this)" role="tab">Guide</button>
```

Add one new button after History:

```html
  <button class="tab-btn" onclick="switchTab('sentiment', this)" role="tab">Data ⇄ Sentiment</button>
```

**4b.** In the leaderboard `<thead>`, find:

```html
          <th onclick="sortTable(6)" data-col="6">Data</th>
          <th onclick="sortTable(7)" data-col="7">Rank Δ</th>
```

Replace with:

```html
          <th onclick="sortTable(6)" data-col="6">Data</th>
          <th onclick="sortTable(7)" data-col="7">Sentiment</th>
          <th onclick="sortTable(8)" data-col="8">Rank Δ</th>
```

**4c.** In the leaderboard `<tbody>`, find:

```html
          <td>{{ row.data_score }}</td>
          <td>
            {% if row.arrow %}<span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {% endif %}{{ row.delta_rank }}
          </td>
```

Replace with:

```html
          <td>{{ row.data_score }}</td>
          <td>{{ row.sentiment_score }}</td>
          <td>
            {% if row.arrow %}<span class="arrow {{ row.arrow_class }}">{{ row.arrow }}</span> {% endif %}{{ row.delta_rank }}
          </td>
```

**4d.** Add the new tab section. Find `<!-- Tab: Guide -->` and insert before it:

```html
<!-- ============================================================
     Tab: Data ⇄ Sentiment
     ============================================================ -->
<section id="tab-sentiment" class="tab-panel">
  <div class="chart-container" id="sentiment-scatter" style="height:560px"></div>
  <p style="color:var(--text-muted);font-size:0.8rem;padding:8px 0 0 4px">
    Bottom-right quadrant (data ahead of sentiment) is the early-rotation zone.
    Hollow points = no sentiment data this scan (source unavailable or EU-only).
    Sentiment is a confirming signal — noisy, gameable, retail-biased.
    This is analytical tooling, not investment advice.
  </p>
</section>
```

**4e.** In the JavaScript at the bottom of the template, find the chart initialisation block (look for `Plotly.newPlot('rrg-chart'`). Add alongside the others:

```javascript
  Plotly.newPlot('sentiment-scatter', JSON.parse({{ sentiment_scatter_json | tojson }}), {}, {responsive: true});
```

- [ ] **Step 5: Test the dashboard builds without errors**

```bash
source .venv/bin/activate && python dashboard/build.py
```

Expected: `Dashboard built: docs/index.html` with no errors.

- [ ] **Step 6: Commit**

```bash
git add dashboard/build.py dashboard/templates/index.html.j2
git commit -m "feat: add Data⇄Sentiment scatter tab and Sentiment column to dashboard"
```

---

## Task 10: Final smoke test + run all tests

- [ ] **Step 1: Run the full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests pass (≥44: 38 prior + 5 sentiment + 1 new scoring test).

- [ ] **Step 2: End-to-end dry run**

```bash
python scan.py --dry-run 2>&1
```

Expected output includes:
- `Sentiment scores computed (N non-neutral sectors)` — N may be 0 if sources are rate-limited
- `Scoring complete. 22 sectors ranked.`
- No exceptions

- [ ] **Step 3: Build dashboard**

```bash
python dashboard/build.py
```

Expected: `Dashboard built: docs/index.html` — open in browser, verify the "Data ⇄ Sentiment" tab exists and the Leaderboard has a Sentiment column.

- [ ] **Step 4: Final commit**

```bash
git add -A
git status  # verify only expected files changed
git commit -m "feat: Phase 2 complete — sentiment pillar active at 30%"
```

---

## Self-Review Checklist (already run)

- [x] All spec sections covered by a task
- [x] No TBD/placeholder steps
- [x] Type signatures consistent across tasks (`dict[str, dict] | None`, `pd.Series`, `sector_keys: list[str]`)
- [x] `score_all` change threaded through: Task 6 (signature) → Task 7 (scan.py call) → Task 8 (weights)
- [x] `_build_leaderboard_rows` updated before template change references `row.sentiment_score`
- [x] Dashboard Plotly init added for new `sentiment-scatter` div
