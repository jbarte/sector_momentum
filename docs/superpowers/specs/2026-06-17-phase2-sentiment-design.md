# Phase 2 — Sentiment Pillar Design

**Date:** 2026-06-17  
**Status:** Approved  
**Branch:** feature/phase-2-sentiment

## Context

Phase 1 delivered a fully working data-only ranking engine. The composite score is currently `data_score * 1.0` — the sentiment pillar exists in the schema and scoring code but contributes nothing. Phase 2 activates it at 30% weight using three free, credential-light sources: Reddit public JSON, Google Trends (pytrends), and StockTwits public API.

## Data Sources

| Source | Auth | Coverage | Caching |
|--------|------|----------|---------|
| Reddit public JSON | User-Agent header only | US + EU sectors via keyword search across 8 subreddits | `data/cache/reddit_<date>.json` |
| Google Trends (pytrends) | None | US + EU sectors, 5 EU countries + US | `data/cache/trends_<date>.json` |
| StockTwits public API | None | US sectors only (EU ETFs not on platform) | `data/cache/stocktwits_<date>.json` |

**Subreddits:** `r/stocks`, `r/investing`, `r/wallstreetbets`, `r/aktier`, `r/Finanzen`, `r/vosfinances`, `r/eupersonalfinance`, `r/EuropeFIRE`

**Resilience:** every loader returns `None` on any exception. Downstream treats `None` as "source unavailable this run" and the sector gets a neutral `0.0` sentiment score rather than crashing the scan.

## Keyword Config

`config/sentiment_keywords.yaml` — sector → search terms, one entry per GICS sector. Includes both plain-language terms and ETF tickers so posts mentioning "XLK" or "EXV3.DE" are caught alongside "semiconductor" or "cloud."

```yaml
Technology: [semiconductor, AI, cloud, software, XLK, EXV3]
Financials: [bank, interest rate, Fed, ECB, XLF, EXV1]
Energy: [oil, gas, crude, XLE, EXV4]
Health Care: [pharma, biotech, healthcare, XLV, EXV6]
Industrials: [manufacturing, aerospace, defense, XLI, EXV8]
Consumer Discretionary: [retail, consumer, auto, XLY, EXH2]
Consumer Staples: [food, beverage, staples, XLP, EXH3]
Utilities: [utility, power, grid, XLU, EXH8]
Materials: [mining, chemicals, materials, XLB, EXV5]
Real Estate: [REIT, property, real estate, XLRE, IPRP]
Communication Services: [telecom, media, streaming, XLC, EXV2]
```

## New Files

### `src/data/reddit.py`
For each sector, sends **one** multireddit search query combining all 8 subreddits:
```
https://www.reddit.com/r/stocks+investing+wallstreetbets+aktier+Finanzen+vosfinances+eupersonalfinance+EuropeFIRE/search.json?q={keywords_OR}&sort=new&limit=100
```
`keywords_OR` = sector keywords joined with `+OR+` (e.g. `semiconductor+OR+AI+OR+cloud`). This is 11 requests total (~6 seconds at 0.5s sleep), well within the 10 req/min limit. Collects post counts split into last 7 days vs last 30 days by filtering on `created_utc`. Returns `dict[sector, {7d: int, 30d: int}]` or `None` on failure.

- User-Agent: read from `REDDIT_USER_AGENT` env var (defaults to a sensible fallback so it works without `.env`).

### `src/data/trends.py`
Fetches 13-week Google Trends interest for each sector's primary keyword across countries SE, DE, FR, NL, IT, US. Batches into groups of 5 keywords per pytrends request to avoid 429s. Returns `dict[sector, pd.Series]` (13-week interest) or `None` on failure.

### `src/data/stocktwits.py`
Hits `https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json` for each US ETF ticker. Counts `bullish` / `bearish` sentiment tags from messages in the response. EU sectors → `NaN` (not on platform). Returns `dict[sector, {bull: int, bear: int}]` or `None` on failure.

### `src/signals/sentiment.py`
Three signals, one public function:

**`mention_velocity_zscore`** (Reddit)  
`velocity = (7d_count/7) / (30d_count/30 + 1)` per sector, then cross-sectional z-score. NaN if Reddit unavailable.

**`search_momentum`** (Google Trends)  
Linear regression slope of 13-week interest series per sector, cross-sectional z-score. NaN if Trends unavailable.

**`bull_bear_score`** (StockTwits, US only)  
`(bull - bear) / (bull + bear + 1)`, bounded −1 to +1. EU sectors → NaN. No z-score needed.

**`compute_sentiment_score(reddit, trends, stocktwits, sectors) -> pd.Series`**  
Equal-weight average of available signals per sector (NaN signals skipped). Returns Series indexed by `"region|gics_sector"`. All-NaN sector → `0.0` (neutral).

## Modified Files

### `config/weights.yaml`
```yaml
pillars:
  data: 0.70        # was 1.0
  sentiment: 0.30   # was 0.0
```

### `scan.py`
New step after data-signal computation:
```python
reddit_raw     = fetch_reddit(universe, keywords)
trends_raw     = fetch_trends(universe, keywords)
stocktwits_raw = fetch_stocktwits(universe)
sentiment_scores = compute_sentiment_score(
    reddit_raw, trends_raw, stocktwits_raw, sectors
)
scores_df = score_all(signals_wide, sentiment_scores)
```
`--dry-run` covers sentiment: fetches and computes but skips DB write.

### `requirements.txt`
Add: `pytrends>=4.9`  
(`requests` already present for Reddit + StockTwits)

### `.env.example`
```
REDDIT_USER_AGENT=sector-momentum-scanner/1.0 by u/your_reddit_username
```

### `dashboard/templates/index.html.j2`

**Leaderboard tab** — add `Data` and `Sentiment` columns. Cells show `—` when NaN.

**New tab: Data ⇄ Sentiment scatter**  
- X = `data_score`, Y = `sentiment_score`  
- Points labeled by sector, colored by region (US=blue, EU=orange)  
- Four-quadrant overlay:
  - Top-right: *Agreement (bullish)*
  - Bottom-left: *Agreement (bearish)*
  - Top-left: *Sentiment ahead of data* — speculative/crowded
  - Bottom-right: *Data ahead of sentiment* — under the radar (early-momentum zone)
- Sectors with NaN sentiment rendered as hollow/faded points

## Data Flow

```
config/sentiment_keywords.yaml
        ↓
reddit.py → data/cache/reddit_<date>.json
trends.py → data/cache/trends_<date>.json        → sentiment.py → compute_sentiment_score()
stocktwits.py → data/cache/stocktwits_<date>.json                       ↓
                                                              scan.py → score_all(signals, sentiment)
                                                                              ↓
                                                                     momentum.db (sentiment_score col)
                                                                              ↓
                                                                     dashboard (scatter + leaderboard)
```

## Minimal Change to `src/scoring.py`

`score_all` needs one new optional parameter so `scan.py` can pass sentiment scores in:
```python
def score_all(
    signals_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
    sentiment_score: pd.Series | None = None,   # NEW — passed through to compute_composite
) -> pd.DataFrame:
```
`compute_composite` already accepts `sentiment_score` — this just threads it through. `src/state.py` and `src/report.py` are unchanged.

## Testing

- Unit tests for each signal function in `tests/test_sentiment.py` (mock loader outputs, verify z-score shape and NaN handling)
- Smoke test: `python scan.py --dry-run` must complete with sentiment scores populated for ≥ 1 sector
- Dashboard: `python dashboard/build.py` must render the new scatter tab without error

## Out of Scope for Phase 2

- Multilingual polarity (deferred to Phase 3 per architecture)
- Constituent-level Reddit mention aggregation (Phase 3)
- FRED macro data activation (separate concern, not part of sentiment pillar)
