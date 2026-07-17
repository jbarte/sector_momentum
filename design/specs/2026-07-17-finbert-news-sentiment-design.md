# FinBERT News Sentiment — Design Spec

## Goal

Add signed (positive/negative) news sentiment per GICS sector using a
finance-tuned BERT model (ProsusAI/finbert) over GDELT headlines, replacing
the directionless Google Trends slope as the `sentiment_score` in the
composite scoring path and making the dashboard's "Include sentiment in
ranking" toggle meaningful.

## Background

The 2026-06-26 Trends validation showed search-attention is noisy,
directionless, and contaminated by ambiguous tickers. FinBERT sidesteps
search-term ambiguity entirely and adds polarity (positive vs negative), which
is what the scoring toggle was always waiting for — `config/weights.yaml`
says "Revisit pillar blending only if/when FinBERT provides signed polarity."

Google Trends derived signals (`momentum`, `acceleration`, `spike`, etc.)
remain as info-only columns on the sentiment page. They are not removed or
replaced — FinBERT augments, not supplants.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| News source | GDELT DOC 2.0 API | Free, no API key, global, ~250k articles/day, 15-min freshness |
| Inference | Local CPU (`ProsusAI/finbert` via `transformers`) | No external API dependency, free, ~2-3 min inference |
| Language | English-only | Finance news is heavily English; GDELT has good English coverage globally; base FinBERT is English-only |
| Scoring | Blendable into composite | Replaces `sentiment_score` so the existing toggle blends real polarity |
| Scope | Sectors only (not themes) | GDELT theme codes map to GICS sectors; thematic ETFs stay Trends-only |

---

## Architecture

### 1. Data Source — GDELT DOC 2.0 API

**Endpoint:** `https://api.gdeltproject.org/api/v2/doc/doc`

**Query strategy:** One query per GICS sector (11 total), no region filter.
Financial news sentiment for a sector is global — a Reuters article about oil
prices affects both US and EU Energy equally. Both `US|Energy` and `EU|Energy`
receive the same sentiment score.

**Query parameters per sector:**
- `query`: `theme:<CODE1> OR theme:<CODE2> ... sourcelang:english`
- `mode`: `ArtList`
- `maxrecords`: `250`
- `format`: `json`
- `timespan`: `24h`
- `sort`: `datedesc`

**Rate limiting:** ~1 request per 5 seconds (GDELT enforces IP-level
throttling). 11 queries at 5s spacing = ~55 seconds total. Implement
exponential backoff on HTTP 429 (60s, 120s, up to 3 retries).

**Response fields used:** `title` (headline text for FinBERT), `seendate`
(article timestamp), `domain` (source), `sourcecountry`. Per-article tone is
not available in the DOC API — we compute our own via FinBERT.

**No new library dependency** — raw `requests.get()` (already a dependency).

### GDELT Theme Code Mapping

Static dict mapping each GICS sector to GDELT theme codes:

| GICS Sector | GDELT Theme Codes |
|---|---|
| Energy | `ENV_OIL`, `ENV_NATURALGAS`, `ENV_COAL`, `ECON_OILPRICE`, `ECON_GASOLINEPRICE`, `ECON_NATGASPRICE` |
| Materials | `ENV_MINING`, `ENV_METALS`, `ENV_FORESTRY` |
| Industrials | `WB_1281_MANUFACTURING`, `WB_1068_MANUFACTURING_DEVELOPMENT` |
| Consumer Discretionary | `ECON_HOUSING_PRICES`, `TOURISM` |
| Consumer Staples | `AGRICULTURE`, `WB_435_AGRICULTURE_AND_FOOD_SECURITY` |
| Health Care | `GENERAL_HEALTH`, `MEDICAL` |
| Financials | `ECON_STOCKMARKET`, `ECON_CENTRALBANK`, `ECON_INTEREST_RATES`, `ECON_DEBT` |
| Information Technology | `CYBER_ATTACK`, `TECH_AUTOMATION`, `TECH_BIGDATA`, `WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES` |
| Communication Services | `MEDIA`, `WB_1286_TELECOMMUNICATIONS` |
| Utilities | `WB_508_POWER_SYSTEMS`, `WB_137_WATER`, `WATER_SECURITY` |
| Real Estate | `WB_904_HOUSING_MARKETS`, `WB_870_HOUSING_CONSTRUCTION`, `ECON_HOUSING_PRICES` |

This mapping lives in `src/data/news_sentiment.py` as a module-level constant.
It can be moved to a config file later if it needs tuning, but starting
in-code avoids premature abstraction.

### 2. FinBERT Inference

**Model:** `ProsusAI/finbert` — finance-tuned BERT, three-class output:
positive, negative, neutral. ~400MB model, cached in
`~/.cache/huggingface/hub/` after first download.

**Pipeline:**
1. Collect all headlines from the 11 GDELT queries (up to ~2,750 titles)
2. Load model: `transformers.pipeline("sentiment-analysis", model="ProsusAI/finbert", device="cpu")`
3. Batch headlines through the pipeline (batch_size=32)
4. Each headline → `{label: "positive"|"negative"|"neutral", score: 0.0-1.0}`
5. Convert to signed polarity: `+score` for positive, `-score` for negative, `0.0` for neutral

**Performance:** ~2,750 headlines at batch_size=32 on CPU ≈ 1-3 minutes.
Model load ≈ 5-10 seconds (cold). Acceptable within the daily scan's runtime
budget.

### 3. Aggregation & Scoring

**Per-sector aggregation:** For each GICS sector (11 sectors):
- `mean_polarity` — mean of signed polarity scores across all articles (the main signal)
- `article_count` — number of articles scored
- `positive_pct` — fraction of positive labels
- `negative_pct` — fraction of negative labels

Sectors with fewer than **5 articles** produce `NaN` (too sparse).

**Cross-sectional z-score:** `mean_polarity` is z-scored across all 11
sectors (same pattern as `score_symbol_sentiment` in `trends_symbols.py`).
The resulting z-score becomes `sentiment_score` in the `scores` table.

**Region handling:** Both `US|<sector>` and `EU|<sector>` receive the same
`sentiment_score` for a given GICS sector (global news sentiment). If
region-specific sentiment is desired later, the GDELT query can add
`sourcecountry:` filters — but that doubles API calls, thins the article pool,
and is out of scope.

**Fallback:** If the FinBERT step fails (model download error, GDELT outage),
`sentiment_score` retains the Google Trends slope z-score from step 8. No
regression.

### 4. Integration into scan.py

**New step 8d** — after rising queries (8c), before scoring (step 9). Same
non-fatal try/except pattern as steps 8b/8c.

```
Step 8d: FinBERT news sentiment
  1. fetch_news_headlines() → dict[str, list[str]]  {sector: [titles]}
  2. score_headlines(headlines) → dict[str, dict]    {sector: {mean_polarity, count, ...}}
  3. z-score mean_polarity across sectors → sentiment_score per sector
  4. Overwrite sentiment_score in the scoring DataFrame (Trends z is the fallback)
  5. Append news_polarity/news_count/news_positive_pct/news_negative_pct
     rows to sentiment_signals_df
```

**CLI flag:** `--no-finbert` skips step 8d entirely (same pattern as
`--no-cache`). Useful for fast local runs without the 400MB model download.

**Theme sentiment:** Not affected. Themes stay Google Trends only. GDELT
theme codes map to GICS sectors, not thematic ETFs.

### 5. Dashboard Surfacing

**Sentiment page (`sentiment.html`):** Four new columns in the per-sector
signal table, rendered by `dashboard/sentiment.py`:

| Column | Source signal_name | Format | Example |
|---|---|---|---|
| Polarity | `news_polarity` | Signed float, green/red | `+0.15` |
| Articles | `news_count` | Integer | `142` |
| Pos% | `news_positive_pct` | Percentage | `62%` |
| Neg% | `news_negative_pct` | Percentage | `28%` |

EN+SV i18n for the new column headers.

**Leaderboard (sectors page):** No visual changes. The existing "Sentiment"
column already displays `sentiment_score` from the `scores` table — it now
reflects FinBERT polarity z instead of Trends slope z.

**Blend toggle:** The "Include sentiment in ranking" toggle (`rescore.js`)
blends `sentiment_score` at the configured weight (default 30%). No code
change needed — the toggle already works; it just blends a more meaningful
signal now.

**Themes page:** No changes. Theme sentiment stays Trends-only.

### 6. Dependencies & CI

**New runtime dependencies (added to `requirements.txt`):**
- `transformers>=4.30`
- `torch>=2.0` — CPU-only variant via `--index-url https://download.pytorch.org/whl/cpu` (~200MB wheel)

Regenerate `requirements.lock` after updating `requirements.txt`.

**CI impact (`scan.yml`):**
- Install: ~600MB additional download (torch-cpu + transformers + tokenizers). Adds ~1-2 min.
- Scan runtime: ~3-5 min additional (GDELT fetch ~55s + model load ~10s + inference ~2-3 min).
- Total daily scan: ~8 min → ~12 min. Within GitHub Actions limits.

**Model download in CI:** `ProsusAI/finbert` downloads to
`~/.cache/huggingface/` on first use (~400MB). Re-downloads each CI run
(ephemeral runner). Accepted for now; CI cache optimization is a future
improvement.

**Tests:** Mock the FinBERT model and GDELT responses. No actual model
download or network calls in test runs.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/data/news_sentiment.py` | Create | GDELT fetch, FinBERT inference, aggregation, GICS-to-theme mapping |
| `scan.py` | Modify | Step 8d integration, `--no-finbert` CLI flag |
| `dashboard/sentiment.py` | Modify | Four new columns in signal table builder |
| `dashboard/templates/i18n/_sentiment.js.j2` | Modify | EN+SV labels for new columns |
| `config/weights.yaml` | Modify | Update comment to note FinBERT is now active |
| `requirements.txt` | Modify | Add `transformers>=4.30`, `torch>=2.0` |
| `tests/test_news_sentiment.py` | Create | Unit tests for GDELT fetch, FinBERT scoring, aggregation |

## Schema

No DDL changes. FinBERT signals use existing `sentiment_signals` table
columns:
- `signal_name` TEXT — `news_polarity`, `news_count`, `news_positive_pct`, `news_negative_pct`
- `value` REAL — the numeric value

`scores.sentiment_score` REAL — overwritten with FinBERT z-score (was Trends slope z).

## Out of Scope

- Region-specific news sentiment (separate US/EU GDELT queries)
- Theme sentiment via GDELT (themes stay Trends-only)
- Multilingual FinBERT (translate-then-score or multilingual model)
- CI model caching (GitHub Actions cache or Supabase Storage)
- GDELT tone scores (we compute our own via FinBERT)
- Configurable theme-code mapping (starts as in-code constant)
