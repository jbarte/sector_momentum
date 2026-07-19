# Google Trends Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dead Google Trends sentiment pipeline entirely, leaving FinBERT as the sole sentiment source, and harden the GDELT fetch so FinBERT covers more sectors per scan.

**Architecture:** Pure removal + a small parameter change. Delete both Trends modules, their tests, their config, the scan.py Trends passes, and the pytrends dependency. FinBERT (already shipped) keeps filling `sentiment_score`; when it fails, scores stay NULL (honest). Historical DB rows in `sentiment_signals`/`theme_sentiment_signals` are preserved (no DDL change); the scan just stops writing Trends-derived rows.

**Tech Stack:** Python 3.11, pandas, pytest, Jinja2 dashboard, Supabase/Postgres, uv lockfiles.

## Global Constraints

- Never commit `docs/` from a feature branch (CI owns it). Build locally to verify only.
- Branch is `feature/trends-retirement` (already created off main).
- Lockfiles are CI-only (Ubuntu): regenerate with `uv pip compile <in> --python-version 3.11 --python-platform x86_64-unknown-linux-gnu --upgrade -o <lock>`.
- `sentiment_signals` and `theme_sentiment_signals` DDL and existing rows stay; `_SCAN_CHILD_TABLES` in `src/state.py` stays unchanged.
- FinBERT info rows (`news_polarity`, `news_count`, `news_positive_pct`, `news_negative_pct`) continue to be written to `sentiment_signals` and read by the dashboard via `get_sentiment_signals_for_latest_scan` — keep that getter.
- Skip local full-suite pytest as a gate only where noted (CI runs it); still run targeted tests per task.

---

### Task 1: GDELT fetch hardening (`src/data/news_sentiment.py`)

**Files:**
- Modify: `src/data/news_sentiment.py` (`fetch_news_headlines`, ~lines 57-113)
- Test: `tests/test_news_sentiment.py`

**Interfaces:**
- Produces: `fetch_news_headlines(sectors=None, timespan="24h", sleep_s=20.0, max_retries=4)` — new defaults; behavior otherwise unchanged.

- [ ] **Step 1: Change the defaults and fix the silent final-attempt give-up.**

In `fetch_news_headlines`, change the signature defaults `sleep_s: float = 5.0` → `sleep_s: float = 20.0` and `max_retries: int = 3` → `max_retries: int = 4`.

In the 429 branch, the final attempt currently falls through silently. Change the 429 block so the last attempt logs a give-up:

```python
                if resp.status_code == 429:
                    if attempt < max_retries - 1:
                        wait = 60 * (2 ** attempt)
                        logger.warning("GDELT 429 for %s — backing off %ds", sector, wait)
                        if sleep_s > 0:
                            time.sleep(wait)
                        continue
                    logger.warning("GDELT 429 for %s after %d retries — skipping", sector, max_retries)
                    break
```

- [ ] **Step 2: Add a regression test for the new defaults.**

Append to `tests/test_news_sentiment.py`:

```python
def test_fetch_news_headlines_defaults_are_hardened():
    import inspect
    from src.data.news_sentiment import fetch_news_headlines
    sig = inspect.signature(fetch_news_headlines)
    assert sig.parameters["sleep_s"].default == 20.0
    assert sig.parameters["max_retries"].default == 4
```

- [ ] **Step 3: Run the test.**

Run: `python3 -m pytest tests/test_news_sentiment.py -q`
Expected: PASS (all existing + new test).

- [ ] **Step 4: Commit.**

```bash
git add src/data/news_sentiment.py tests/test_news_sentiment.py
git commit -m "fix: harden GDELT fetch pacing and log final-attempt give-up"
```

---

### Task 2: Gut the Trends passes from `scan.py`

**Files:**
- Modify: `scan.py` (delete `_fetch_theme_sentiment`, Steps 8/8b/8c, `--no-cache`, theme-sentiment wiring)

**Interfaces:**
- Produces: a `scan.py` with no `trends_*` imports; `sentiment_score` initialized empty and filled by FinBERT only.

- [ ] **Step 1: Delete the `--no-cache` CLI argument** (`scan.py` ~lines 74-78, the `parser.add_argument("--no-cache", ...)` block).

- [ ] **Step 2: Delete `_fetch_theme_sentiment`** entirely (~lines 205-281).

- [ ] **Step 3: Replace Step 8 (Trends sentiment) through Step 8c** (~lines 373-490, from the `# Step 8:` banner up to and including the `if _use_cache: trends_cache.save_cache(...)` block) with a NULL-initialized sentiment series and empty signals frame:

```python
    # ------------------------------------------------------------------
    # Step 8: Sentiment — FinBERT only (Google Trends retired 2026-07-19)
    # ------------------------------------------------------------------
    sentiment_score = pd.Series(float("nan"), index=wide_df.index, dtype=float)
    sentiment_signals_df = pd.DataFrame(
        columns=["region", "gics_sector", "signal_name", "value"]
    )
```

- [ ] **Step 4: Update the FinBERT block's failure log** (~line 527): change
`logger.warning("FinBERT sentiment failed (%s) — continuing with Trends score", exc)` to
`logger.warning("FinBERT sentiment failed (%s) — sentiment stays NULL for this scan", exc)`.

- [ ] **Step 5: Remove theme-sentiment wiring in the themes block** (~lines 605-634). Replace the `_theme_sentiment`/`_theme_sent_df` fetch+finally block and the `save_theme_scan(..., sentiment_signals_df=_theme_sent_df)` call with price-only theme scoring:

```python
                    _theme_scored = score_all(
                        _theme_wide, sentiment_score=None, blend_sentiment=False,
                    )
                    _theme_scores_df = _build_scored_df_for_db(_theme_scored)
                    _theme_z = zscore_cross_section(_theme_wide)
                    _theme_signals_df = _build_long_signals_df(_theme_rows, z_wide_df=_theme_z)
                    save_theme_scan(
                        conn, scan_id, _theme_scores_df, _theme_signals_df,
                    )
```

- [ ] **Step 6: Confirm no Trends references remain in scan.py.**

Run: `grep -nE "trends|Trends|no_cache|_use_cache|_cache|_anchor|_region_geos|_fetch_theme_sentiment" scan.py`
Expected: no matches (or only unrelated substrings — inspect each).

- [ ] **Step 7: Byte-compile and smoke the arg parser.**

Run: `python3 -c "import ast; ast.parse(open('scan.py').read())"` → no error.
Run: `python3 scan.py --help` → prints usage without `--no-cache`.

- [ ] **Step 8: Commit.**

```bash
git add scan.py
git commit -m "refactor: remove Google Trends passes from scan pipeline"
```

---

### Task 3: Delete Trends modules, tests, config, and the pytrends dependency

**Files:**
- Delete: `src/data/trends_symbols.py`, `src/data/trends_cache.py`
- Delete: `tests/test_trends_cache.py`, `tests/test_trends_symbols_entities.py`, `tests/test_trends_symbols_fetch.py`, `tests/test_trends_symbols_map.py`, `tests/test_trends_symbols_region.py`, `tests/test_trends_symbols_score.py`, `tests/test_trends_symbols_transforms.py`
- Delete: `config/trends_geo.yaml`, `config/trends_entities.yaml`, `config/trends_blocklist.yaml`, `scripts/resolve_trends_entities.py`
- Modify: `config/themes.yaml` (remove `trends:` and `trends_entities:` sections, ~lines 34-53)
- Modify: `requirements.txt` (remove `pytrends==4.9.2`), regenerate `requirements.lock` + `requirements-dev.lock`
- Modify: `src/state.py` (delete `get_theme_sentiment_signals_for_latest_scan`)

- [ ] **Step 1: Delete the modules, tests, config, and script.**

```bash
git rm src/data/trends_symbols.py src/data/trends_cache.py \
  tests/test_trends_cache.py tests/test_trends_symbols_entities.py \
  tests/test_trends_symbols_fetch.py tests/test_trends_symbols_map.py \
  tests/test_trends_symbols_region.py tests/test_trends_symbols_score.py \
  tests/test_trends_symbols_transforms.py \
  config/trends_geo.yaml config/trends_entities.yaml config/trends_blocklist.yaml \
  scripts/resolve_trends_entities.py
```

- [ ] **Step 2: Trim `config/themes.yaml`** — delete the `trends:` block and the `trends_entities: {}` line plus their comment header (lines 34-53), leaving `themes:`, `benchmark:`, and `ucits:` intact.

- [ ] **Step 3: Remove pytrends from `requirements.txt`** (delete the `pytrends==4.9.2` line).

- [ ] **Step 4: Regenerate both lockfiles.**

```bash
uv pip compile requirements.txt --python-version 3.11 --python-platform x86_64-unknown-linux-gnu --upgrade -o requirements.lock
uv pip compile requirements-dev.txt --python-version 3.11 --python-platform x86_64-unknown-linux-gnu --upgrade -o requirements-dev.lock
```

Run: `grep -c pytrends requirements.lock requirements-dev.lock` → both `0`.

- [ ] **Step 5: Delete `get_theme_sentiment_signals_for_latest_scan` from `src/state.py`** (keep `get_sentiment_signals_for_latest_scan`).

- [ ] **Step 6: Grep-clean check** — no live code references remain:

Run:
```bash
grep -rnE "trends_symbols|trends_cache|pytrends|fetch_comparative_interest|fetch_rising_queries|get_theme_sentiment_signals_for_latest_scan" \
  --include="*.py" --include="*.yaml" . | grep -v -E "design/|BACKLOG.md"
```
Expected: no matches.

- [ ] **Step 7: Run the full suite (Trends tests gone).**

Run: `python3 -m pytest -q`
Expected: PASS, collection succeeds with the 7 Trends test files removed. (If `dashboard/build.py` import chain fails here, defer to Task 4 — but state/scan/pipeline tests must pass.)

- [ ] **Step 8: Commit.**

```bash
git add -A
git commit -m "chore: delete Trends modules, tests, config, and pytrends dep"
```

---

### Task 4: Dashboard — FinBERT-only sentiment page

**Files:**
- Modify: `dashboard/sentiment.py` (`_build_sentiment_signal_rows`, `build_page_context`)
- Modify: `dashboard/build.py` (drop `theme_sentiment_signals_df` load + `get_theme_sentiment_signals_for_latest_scan` import, ~lines 244-297)
- Modify: `dashboard/templates/sentiment.html.j2` (FinBERT-only table, drop themes cohort/toggle/rising JS, rewrite guide)
- Modify: `dashboard/templates/i18n/_sentiment.js.j2` (drop dead keys)

- [ ] **Step 1: Rewrite `dashboard/sentiment.py`.** Reduce `_build_sentiment_signal_rows` to FinBERT-only columns and drop the theme context. New file:

```python
"""Sentiment-specific data builders (FinBERT news sentiment only)."""

from __future__ import annotations

import math


def _build_sentiment_signal_rows(sent_df) -> list[dict]:
    """One display row per sector-key with FinBERT news columns.

    Returns [] when no sentiment_signals rows exist (older scans / dry runs).
    """
    if sent_df is None or sent_df.empty:
        return []

    def _fmt(v, pct=False):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v * 100:.0f}%" if pct else f"{v:+.2f}"

    rows = []
    for (region, sector), grp in sent_df.groupby(["region", "gics_sector"]):
        vals = dict(zip(grp["signal_name"], grp["value"]))
        news_count = vals.get("news_count")
        has_count = news_count is not None and not (
            isinstance(news_count, float) and math.isnan(news_count)
        )
        rows.append({
            "region": region,
            "sector": sector,
            "_polarity": vals.get("news_polarity") or 0.0,
            "news_polarity": _fmt(vals.get("news_polarity")),
            "news_count": str(int(news_count)) if has_count else "—",
            "news_positive_pct": _fmt(vals.get("news_positive_pct"), pct=True),
            "news_negative_pct": _fmt(vals.get("news_negative_pct"), pct=True),
        })
    rows.sort(key=lambda r: r["_polarity"], reverse=True)
    return rows


def build_page_context(shared: dict) -> dict:
    """Assemble sentiment page context (sectors only; FinBERT)."""
    from dashboard.figures import _build_sentiment_scatter_figure

    return {
        "sentiment_scatter_json": _build_sentiment_scatter_figure(shared["history_df"]),
        "sentiment_signal_rows": _build_sentiment_signal_rows(shared["sentiment_signals_df"]),
    }
```

- [ ] **Step 2: Update `dashboard/build.py`** — remove `get_theme_sentiment_signals_for_latest_scan` from the state import (~line 246), delete the `theme_sentiment_signals_df = get_theme_sentiment_signals_for_latest_scan(conn)` line (~254), and delete the `"theme_sentiment_signals_df": theme_sentiment_signals_df,` entry from the shared dict (~297). Leave `sentiment_signals_df` and its getter intact.

- [ ] **Step 3: Rewrite `dashboard/templates/sentiment.html.j2`** to a FinBERT-only, single-cohort page:
  - Replace the `sentiment_table` macro with a FinBERT-only version: columns Sector, Region, Polarity, Articles, Pos%, Neg% (drop all Trends columns and the rising-queries expandable rows/`id_prefix`/`show_news`/`show_region` complexity — one fixed table).
  - Delete the cohort toggle (`sent-cohort-toggle` div, lines ~107-111).
  - Delete the entire Themes cohort panel (lines ~191-202).
  - In the Sectors panel, drop the `{% if %}` cohort wrapper; render scatter + footnote + the FinBERT table (heading i18n `sent_news_heading` = "News sentiment").
  - Delete `THEME_SENTIMENT_DATA` and the cohort-toggle JS IIFE (lines ~206-274) and the rising-queries JS IIFE (lines ~276-299).
  - Rewrite the guide body (`guide_body_sentiment`) to describe FinBERT only — delete the "Google Trends derived signals", "Themes", and Trends column bullets; keep the FinBERT source paragraph but change the last sentence "If FinBERT or GDELT is unavailable, the score falls back to the Google Trends slope z-score." → "If FinBERT or GDELT is unavailable, the score is left blank for that scan."
  - Update the scatter footnote: drop "or EU-only" wording; "Hollow points = no news sentiment this scan."

- [ ] **Step 4: Clean `dashboard/templates/i18n/_sentiment.js.j2`** — remove keys only used by deleted markup: `segment_sectors`, `segment_themes`, `note_sentiment_themes`, `sent_themes_empty`, `sent_derived_heading`, `sent_col_momentum`, `sent_col_acceleration`, `sent_col_range`, `sent_col_spike`, `sent_col_volatility`, `sent_col_attention`, `sent_col_seasonal`, `col_theme`, `rising_heading`, `rising_col_query`, `rising_col_growth`. Add `sent_news_heading` (EN "News sentiment" / SV "Nyhetssentiment") if the heading uses a new key. Keep the `sent_col_news_*`, `sent_col_sector`, `sent_col_region`, `note_sentiment`, `guide_*_sentiment`, `sentiment_footnote` keys. (Verify each key's usage with grep against `sentiment.html.j2` before deleting.)

- [ ] **Step 5: Build the dashboard locally against the live DB.**

Run: `python3 dashboard/build.py`
Expected: exits 0, no Jinja `UndefinedError`, `docs/sentiment.html` regenerated.

- [ ] **Step 6: Verify the rendered page has no Trends artifacts.**

Run: `grep -iE "trends|cohort|rising|Momentum|Attention|Seasonal|Themes" docs/sentiment.html | grep -ivE "sector momentum|momentum report|momentum scanner"`
Expected: no Trends/cohort/rising matches (inspect any hit).

- [ ] **Step 7: Restore generated docs (do NOT commit docs/).**

```bash
git checkout docs/ 2>/dev/null; git clean -fd docs/reports 2>/dev/null || true
```
(Only source templates/py are committed.)

- [ ] **Step 8: Commit source only.**

```bash
git add dashboard/sentiment.py dashboard/build.py dashboard/templates/sentiment.html.j2 dashboard/templates/i18n/_sentiment.js.j2
git commit -m "feat: FinBERT-only sentiment page; remove Trends cohort and columns"
```

---

### Task 5: Docs and backlog

**Files:**
- Modify: `BACKLOG.md` (delete Queued item, add Done entry)
- Modify: `ARCHITECTURE.md`, `README.md` (remove Trends from data-flow description if present)

- [ ] **Step 1: BACKLOG.md** — delete the entire `## Retire (or demote) Google Trends sentiment` Queued section, and add this entry at the **top of Done**:

```markdown
- **Retired Google Trends sentiment** — removed the Trends pipeline entirely
  (fetch, day-cache, derived signals, comparative attention, rising queries)
  after it was 429-blocked from CI since ~2026-07-14 and FinBERT (2026-07-17)
  took over `sentiment_score`. Deleted `src/data/trends_symbols.py`,
  `src/data/trends_cache.py`, 7 Trends test files, `config/trends_*.yaml`,
  `scripts/resolve_trends_entities.py`, the `trends:` sections of
  `config/themes.yaml`, and the pytrends dependency (~2,200 lines). Themes lose
  sentiment (were Trends-only); `theme_sentiment_signals` goes dormant.
  Historical `sentiment_signals`/`theme_sentiment_signals` rows and DDL kept.
  Same PR hardened the GDELT fetch (inter-query pause 5s→20s, retries 3→4,
  final-attempt give-up now logged) to lift FinBERT sector coverage. Sentiment
  page is now FinBERT-only. *(2026-07-19)*
```

- [ ] **Step 2: ARCHITECTURE.md / README.md** — grep for "Trends" and update any data-flow description to say sentiment is FinBERT/GDELT only.

Run: `grep -niE "trends|pytrends" README.md ARCHITECTURE.md`
Fix each hit (or confirm it's historical/none).

- [ ] **Step 3: Commit.**

```bash
git add BACKLOG.md ARCHITECTURE.md README.md
git commit -m "docs: record Trends retirement in backlog and architecture"
```

---

## Notes for the reviewer / finishing

- The `trends-cache` Supabase Storage bucket is now unused; deleting it is an optional manual post-merge step in the Supabase dashboard.
- After all tasks: run `/code-review`, then `superpowers:finishing-a-development-branch` to push and open the PR against `main`.
- CI (Ubuntu) runs the full suite from `requirements-dev.lock`; the local suite may skip if torch/model deps aren't installed locally — that's acceptable per project convention.
