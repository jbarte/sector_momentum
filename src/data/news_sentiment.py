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


# Memoised FinBERT pipeline. MUST stay at module scope: _load_finbert_pipeline
# declares `global _finbert_pipeline` and reads it before assigning, so without
# this line every real call raises `NameError: name '_finbert_pipeline' is not
# defined`. It was dropped by the sector-cohort retirement (1ff80d8,
# 2026-08-05) and, because scan.py catches the failure broadly, that silently
# wrote NULL sentiment for every scan from 153 onward instead of failing loudly.
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


def _build_keyword_query(keywords: list[str]) -> str:
    """Build a GDELT query from keyword phrases (quoted, OR-joined)."""
    clause = " OR ".join(f'"{kw}"' for kw in keywords)
    return f"({clause}) sourcelang:english"


def fetch_theme_headlines(
    themes_cfg: dict,
    timespan: str = "24h",
    sleep_s: float = 20.0,
    max_retries: int = 4,
) -> dict[str, list[str]]:
    """Fetch recent English headlines per theme from GDELT using keyword queries.

    Returns {theme_name: [headline, ...]}.  Skips themes without gdelt_keywords.
    """
    themes = themes_cfg.get("themes", {})
    queryable = {
        name: cfg["gdelt_keywords"]
        for name, cfg in themes.items()
        if isinstance(cfg, dict) and cfg.get("gdelt_keywords")
    }

    result: dict[str, list[str]] = {}
    items = list(queryable.items())
    for i, (name, keywords) in enumerate(items):
        params = {
            "query": _build_keyword_query(keywords),
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
                    if attempt < max_retries - 1:
                        wait = 60 * (2 ** attempt)
                        logger.warning("GDELT 429 for theme %s — backing off %ds", name, wait)
                        if sleep_s > 0:
                            time.sleep(wait)
                        continue
                    logger.warning("GDELT 429 for theme %s after %d retries — skipping", name, max_retries)
                    break
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
                    logger.warning("GDELT theme fetch failed for %s (%s) — retry in %ds", name, exc, wait)
                    if sleep_s > 0:
                        time.sleep(wait)
                else:
                    logger.warning("GDELT theme fetch failed for %s after %d retries — skipping", name, max_retries)

        result[name] = titles
        if i < len(items) - 1 and sleep_s > 0:
            time.sleep(sleep_s)

    return result


def fetch_headlines(themes_cfg: dict, *, sleep_s: float = 20.0) -> dict[str, list[str]]:
    """Theme headlines: GDELT bulk files first, query API only for the rest.

    The DOC API's limiter is stateful over a long window, so an 18-query
    batch reliably throttles itself into partial coverage (measured: 7 of 18
    themes returned nothing on 2026-08-15). Bulk files carry no limit at all,
    so they do the bulk of the work and the API is reserved for the handful
    of low-volume themes the files under-serve — few enough requests that the
    fallback actually succeeds when it is used.

    Always returns every keyworded theme, possibly with an empty list. Never
    raises: sentiment is alpha and non-fatal by design.
    """
    headlines: dict[str, list[str]] = {}
    try:
        from src.data.gdelt_gkg import fetch_theme_headlines_bulk, queryable_themes

        # Seed every keyworded theme first, so a theme that matched nothing is
        # still countable as covered-with-zero rather than silently absent.
        headlines = {name: [] for name in queryable_themes(themes_cfg)}
        headlines.update(fetch_theme_headlines_bulk(themes_cfg))
    except Exception as exc:                      # noqa: BLE001
        # Deliberately inside the try: an import error in gdelt_gkg must
        # degrade to the API, not raise. This function's contract is that it
        # never raises.
        logger.warning("GKG bulk fetch failed (%s) — API fallback", exc)

    if not headlines:
        # Bulk failed before it could even seed: hand the API the whole config
        # and let it apply its own queryable filter.
        sparse, sparse_cfg = ["(all themes)"], themes_cfg
    else:
        sparse = [n for n, v in headlines.items() if len(v) < MIN_ARTICLES]
        if not sparse:
            logger.info(
                "GKG bulk covered all %d themes — no API calls needed", len(headlines)
            )
            return headlines
        sparse_cfg = {"themes": {n: themes_cfg["themes"][n] for n in sparse}}

    logger.info("API fallback for %d theme(s): %s", len(sparse), ", ".join(sparse))
    try:
        api = fetch_theme_headlines(sparse_cfg, sleep_s=sleep_s, max_retries=3)
        for name, titles in api.items():
            # Keep whichever source found more. A throttled API returning a
            # short list must not clobber a longer bulk result.
            if len(titles) > len(headlines.get(name, [])):
                headlines[name] = titles
    except Exception as exc:                      # noqa: BLE001
        logger.warning("API fallback failed (%s) — keeping bulk results", exc)

    return headlines


def build_theme_news_signal_rows(
    finbert_scores: dict[str, dict],
) -> list[dict]:
    """Build theme_sentiment_signals rows from FinBERT scores.

    Returns list of {theme, signal_name, value, text_value} dicts.
    """
    rows: list[dict] = []
    for name, sc in finbert_scores.items():
        rows.extend([
            {"theme": name, "signal_name": "news_polarity",
             "value": sc["mean_polarity"], "text_value": None},
            {"theme": name, "signal_name": "news_count",
             "value": float(sc["count"]), "text_value": None},
            {"theme": name, "signal_name": "news_positive_pct",
             "value": sc["positive_pct"], "text_value": None},
            {"theme": name, "signal_name": "news_negative_pct",
             "value": sc["negative_pct"], "text_value": None},
        ])
    return rows
