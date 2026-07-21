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

from src.sector_map import parent_sector

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
    sleep_s: float = 20.0,
    max_retries: int = 4,
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
                    if attempt < max_retries - 1:
                        wait = 60 * (2 ** attempt)
                        logger.warning("GDELT 429 for %s — backing off %ds", sector, wait)
                        if sleep_s > 0:
                            time.sleep(wait)
                        continue
                    logger.warning("GDELT 429 for %s after %d retries — skipping", sector, max_retries)
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
                    logger.warning("GDELT fetch failed for %s (%s) — retry in %ds", sector, exc, wait)
                    if sleep_s > 0:
                        time.sleep(wait)
                else:
                    logger.warning("GDELT fetch failed for %s after %d retries — skipping", sector, max_retries)

        result[sector] = titles
        if i < len(sectors) - 1 and sleep_s > 0:
            time.sleep(sleep_s)

    return result


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


def apply_polarity_to_keys(
    sentiment_score: "pd.Series",
    finbert_z: dict[str, float],
    parent_map: dict[str, str],
) -> "pd.Series":
    """Overwrite per-key sentiment with FinBERT z-scores, resolving sub-sectors
    to their GICS parent (identity fallback). Returns a copy; NaN/unscored
    parents leave the existing value untouched."""
    out = sentiment_score.copy()
    for key in out.index:
        _, _, sector = key.partition("|")
        parent = parent_sector(sector, parent_map)
        z = finbert_z.get(parent)
        if z is not None and not math.isnan(z):
            out[key] = z
    return out


def build_news_signal_rows(
    finbert_scores: dict[str, dict],
    universe: dict,
    parent_map: dict[str, str],
) -> list[dict]:
    """Info-only news signal rows keyed by the universe's actual sector names.
    Sub-sectors inherit their GICS parent's numbers; sectors whose parent has
    no headline scores emit nothing."""
    rows: list[dict] = []
    for region, cfg_key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for name in universe.get(cfg_key, {}):
            sc = finbert_scores.get(parent_sector(name, parent_map))
            if sc is None:
                continue
            rows.extend([
                {"region": region, "gics_sector": name,
                 "signal_name": "news_polarity", "value": sc["mean_polarity"]},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_count", "value": float(sc["count"])},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_positive_pct", "value": sc["positive_pct"]},
                {"region": region, "gics_sector": name,
                 "signal_name": "news_negative_pct", "value": sc["negative_pct"]},
            ])
    return rows


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
