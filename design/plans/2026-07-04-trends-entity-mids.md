# Trends entity-mid resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Query Google Trends by disambiguated Knowledge Graph entity mids (where a ticker has a human-approved one) instead of ambiguous raw ticker strings, eliminating collision false-positives like `VOX`→Vox Media and `LOGS`→the word.

**Architecture:** A committed `config/trends_entities.yaml` maps ticker→mid. `fetch_symbol_trends` gains an optional `entities` param; per batch it substitutes each ticker's mid as the query term (raw string fallback where no mid exists), then re-keys the returned columns back to tickers so all downstream code (`_normalize_by_anchor`, `_aggregate`, `score_symbol_sentiment`) is untouched. A dev-only script bootstraps candidate mids for human review; the scan path never calls `suggestions()`.

**Tech Stack:** Python 3.13, pytrends, pandas, PyYAML, pytest.

## Global Constraints

- Sentiment stays **toggle-only** — this feature does not touch the composite/ranking.
- The change is **strictly additive**: an empty/absent `entities` map must reproduce today's exact query terms (regression guard in Task 2).
- The **scan path must never call `pytrends.suggestions()`** — resolution is read from the committed config only. The build script is dev-only, lives under `scripts/`, and is never imported by `scan.py` or run in CI.
- Downstream functions (`_normalize_by_anchor`, `_aggregate`, `score_symbol_sentiment`) must remain **unchanged** — they stay ticker-keyed.
- Spec: `design/specs/2026-07-04-trends-entity-mids-design.md`.
- Commit style: conventional commits, subject < 72 chars, footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do **not** `git add docs/` (generated artifact owned by CI).
- Baseline test suite must stay green. This branch is cut from `main` (does **not** include the derived-signals work on `feature/sentiment-derived-signals`). Record the branch baseline with `python3 -m pytest -q` before Task 1; the 6 skips are the psycopg2-less DB modules (or install `psycopg2-binary` to run them). This plan adds **9** new tests in `tests/test_trends_symbols_entities.py`.

## File Structure

- `src/data/trends_symbols.py` — add `_resolve_query_terms`, `_rekey_by_ticker`, `load_entities`; add `entities` param to `fetch_symbol_trends`. (add `import yaml`)
- `config/trends_entities.yaml` — new committed config (scaffold header + commented example; real mids populated later via the build script + human review).
- `scripts/resolve_trends_entities.py` — new dev-only bootstrap script.
- `scan.py` — load the config, pass `entities=` to `fetch_symbol_trends`, log resolved/fallback counts.
- `dashboard/templates/sentiment.html.j2` + `dashboard/templates/_i18n.html.j2` — explainer text mentioning entity disambiguation (EN + SV).
- `tests/test_trends_symbols_entities.py` — new test module for the helpers + fake-client integration.
- `BACKLOG.md` — move the Topics bullet to Done.

---

### Task 1: Pure term-resolution + re-keying helpers

**Files:**
- Modify: `src/data/trends_symbols.py` (add two functions after `_aggregate`, near line 112)
- Test: `tests/test_trends_symbols_entities.py` (create)

**Interfaces:**
- Consumes: nothing (pure functions).
- Produces:
  - `_resolve_query_terms(tickers: list[str], entities: dict[str, str]) -> tuple[list[str], dict[str, str]]` — returns `(query_terms, term_to_ticker)`. `query_terms[i] = entities.get(tickers[i], tickers[i])`. `term_to_ticker` maps each query term back to its ticker.
  - `_rekey_by_ticker(raw_by_term: dict[str, list[float]], anchor: str, term_to_ticker: dict[str, str]) -> dict[str, list[float]]` — re-keys a `{term: series}` dict to `{ticker: series}`, leaving the `anchor` key unchanged and passing through any unmapped key as-is.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trends_symbols_entities.py
from src.data.trends_symbols import _resolve_query_terms, _rekey_by_ticker


def test_resolve_query_terms_substitutes_mid_else_ticker():
    terms, rev = _resolve_query_terms(["XLK", "VOX"], {"XLK": "/m/abc"})
    assert terms == ["/m/abc", "VOX"]
    assert rev == {"/m/abc": "XLK", "VOX": "VOX"}


def test_resolve_query_terms_empty_entities_is_identity():
    # additivity guard: no entities -> terms are exactly the tickers
    terms, rev = _resolve_query_terms(["XLK", "XLF"], {})
    assert terms == ["XLK", "XLF"]
    assert rev == {"XLK": "XLK", "XLF": "XLF"}


def test_rekey_by_ticker_maps_terms_and_keeps_anchor():
    raw = {"SPY": [1.0, 2.0], "/m/abc": [3.0, 4.0]}
    out = _rekey_by_ticker(raw, "SPY", {"/m/abc": "XLK"})
    assert out == {"SPY": [1.0, 2.0], "XLK": [3.0, 4.0]}


def test_rekey_by_ticker_passes_through_unmapped_term():
    raw = {"SPY": [1.0], "VOX": [2.0]}
    out = _rekey_by_ticker(raw, "SPY", {"VOX": "VOX"})
    assert out == {"SPY": [1.0], "VOX": [2.0]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_query_terms'`

- [ ] **Step 3: Implement the helpers**

Add after `_aggregate` (after line 112) in `src/data/trends_symbols.py`:

```python
def _resolve_query_terms(
    tickers: list[str],
    entities: dict[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Map a batch of tickers to Trends query terms.

    Each ticker becomes its approved entity mid if present in ``entities``,
    otherwise the raw ticker string (fallback). Returns the query-term list
    (aligned with ``tickers``) plus a term->ticker map for re-keying the
    fetched columns back to tickers.
    """
    terms: list[str] = []
    term_to_ticker: dict[str, str] = {}
    for t in tickers:
        term = entities.get(t, t)
        terms.append(term)
        term_to_ticker[term] = t
    return terms, term_to_ticker


def _rekey_by_ticker(
    raw_by_term: dict[str, list[float]],
    anchor: str,
    term_to_ticker: dict[str, str],
) -> dict[str, list[float]]:
    """Re-key a {query-term: series} dict to {ticker: series}.

    The ``anchor`` key is left as-is (it is normalized/dropped downstream).
    Any term missing from ``term_to_ticker`` passes through unchanged.
    """
    out: dict[str, list[float]] = {}
    for term, series in raw_by_term.items():
        key = anchor if term == anchor else term_to_ticker.get(term, term)
        out[key] = series
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_entities.py
git commit -m "feat: term-resolution helpers for Trends entity mids" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire `entities` into `fetch_symbol_trends`

**Files:**
- Modify: `src/data/trends_symbols.py` — `fetch_symbol_trends` (lines 114-156)
- Test: `tests/test_trends_symbols_entities.py` (append)

**Interfaces:**
- Consumes: `_resolve_query_terms`, `_rekey_by_ticker` (Task 1); existing `_normalize_by_anchor`, `_aggregate`.
- Produces: `fetch_symbol_trends(..., entities: dict[str, str] | None = None)` — new final keyword-only-ish param, defaulting to `None` (treated as `{}`). Return type unchanged (`dict[str, pd.Series]`, region|sector-keyed).

- [ ] **Step 1: Write the failing integration test (fake client, no network)**

Append to `tests/test_trends_symbols_entities.py`:

```python
import pandas as pd
from src.data.trends_symbols import fetch_symbol_trends


class _FakeClient:
    """Records the terms passed to build_payload; returns a fixed frame."""
    def __init__(self, frame):
        self._frame = frame
        self.calls: list[list[str]] = []

    def build_payload(self, kw_list, timeframe="", geo="", **kwargs):
        self.calls.append(list(kw_list))

    def interest_over_time(self):
        return self._frame


def test_fetch_substitutes_mid_and_rekeys_to_ticker():
    # One sector, one live ticker (XLK) resolved to a mid. Frame columns are
    # keyed by the *query terms* the client received (anchor + mid).
    frame = pd.DataFrame({
        "SPY": [10.0, 10.0, 10.0],
        "/m/abc": [5.0, 10.0, 20.0],
    })
    fake = _FakeClient(frame)
    smap = {"US|Technology": ["XLK"]}
    out = fetch_symbol_trends(
        smap, client=fake, window=3, batch_size=4, sleep_s=0.0,
        entities={"XLK": "/m/abc"},
    )
    # the client was queried with the mid, not "XLK"
    assert fake.calls == [["SPY", "/m/abc"]]
    # XLK normalized by SPY: [5/10,10/10,20/10]*100 = [50,100,200]; sector = mean
    assert list(out["US|Technology"]) == [50.0, 100.0, 200.0]


def test_fetch_without_entities_uses_raw_ticker_terms():
    # additivity guard: no entities -> query terms are the raw tickers
    frame = pd.DataFrame({
        "SPY": [10.0, 10.0],
        "XLF": [10.0, 20.0],
    })
    fake = _FakeClient(frame)
    smap = {"US|Financials": ["XLF"]}
    out = fetch_symbol_trends(
        smap, client=fake, window=2, batch_size=4, sleep_s=0.0,
    )
    assert fake.calls == [["SPY", "XLF"]]
    assert list(out["US|Financials"]) == [100.0, 200.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -k fetch -v`
Expected: FAIL — `test_fetch_substitutes_mid_and_rekeys_to_ticker` fails because `entities` is not yet a param (TypeError) / the query uses `"XLK"` not the mid.

- [ ] **Step 3: Implement the change**

In `src/data/trends_symbols.py`, update the signature (add final param):

```python
def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = "SPY",
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
) -> dict[str, pd.Series]:
```

Immediately after the `if client is None:` block (after line 129), normalize the param:

```python
    entities = entities or {}
```

Replace the batch loop body (lines 135-152) so terms come from `_resolve_query_terms` and columns are re-keyed before normalization:

```python
    for bi, batch in enumerate(batches):
        query_terms, term_to_ticker = _resolve_query_terms(batch, entities)
        terms = [anchor] + query_terms
        df = None
        for attempt in range(max_retries):
            try:
                client.build_payload(terms, timeframe=timeframe, geo="")
                df = client.interest_over_time()
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                else:
                    logger.warning("Trends batch %d failed (%s) — %d symbols neutral",
                                   bi + 1, exc, len(batch))
        if df is not None and not df.empty:
            raw_by_term = {t: [float(v) for v in df[t].tolist()[-window:]]
                           for t in terms if t in df.columns}
            raw = _rekey_by_ticker(raw_by_term, anchor, term_to_ticker)
            norm_by_symbol.update(_normalize_by_anchor(raw, anchor))
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)
```

Note: `symbols = sorted({...})` and `batches = [...]` (lines 131-132) and the trailing `return _aggregate(...)` are unchanged. Batches are still built from tickers; substitution happens per batch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -v`
Expected: PASS (6 tests total)

- [ ] **Step 5: Run the existing trends suite (no regressions)**

Run: `python3 -m pytest tests/test_trends_symbols_fetch.py tests/test_trends_symbols_transforms.py tests/test_trends_symbols_map.py tests/test_trends_symbols_score.py -v`
Expected: PASS (all existing trends tests green — proves additivity for the default path)

- [ ] **Step 6: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_entities.py
git commit -m "feat: query Trends by entity mid with string fallback" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Config loader + committed scaffold

**Files:**
- Modify: `src/data/trends_symbols.py` — add `import yaml` (top) and `load_entities`
- Create: `config/trends_entities.yaml`
- Test: `tests/test_trends_symbols_entities.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `load_entities(path: str = "config/trends_entities.yaml") -> dict[str, str]` — flattens the nested config to `{ticker: mid}`, skipping entries without a `mid`. Missing file → `{}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trends_symbols_entities.py`:

```python
from src.data.trends_symbols import load_entities


def test_load_entities_flattens_and_skips_midless(tmp_path):
    p = tmp_path / "ents.yaml"
    p.write_text(
        "XLK:\n  mid: /m/abc\n  title: Tech Fund\n"
        "VOX:\n  mid: /m/def\n  title: Comm Fund\n"
        "XLF:\n  title: no mid here\n"  # skipped: no mid
    )
    ents = load_entities(str(p))
    assert ents == {"XLK": "/m/abc", "VOX": "/m/def"}


def test_load_entities_missing_file_returns_empty():
    assert load_entities("config/does_not_exist_xyz.yaml") == {}


def test_load_entities_empty_file_returns_empty(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load_entities(str(p)) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -k load_entities -v`
Expected: FAIL with `ImportError: cannot import name 'load_entities'`

- [ ] **Step 3: Implement the loader**

At the top of `src/data/trends_symbols.py`, add `import yaml` alongside the other imports. Add the function (near `build_symbol_map`):

```python
def load_entities(path: str = "config/trends_entities.yaml") -> dict[str, str]:
    """Load {ticker: entity mid} from the entities config.

    The on-disk shape is ``{ticker: {mid: ..., title: ...}}``; this flattens to
    ``{ticker: mid}`` and skips any entry lacking a ``mid``. A missing or empty
    file yields ``{}`` (every ticker then falls back to a raw-string query).
    """
    try:
        with open(path, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    return {
        ticker: entry["mid"]
        for ticker, entry in cfg.items()
        if isinstance(entry, dict) and entry.get("mid")
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trends_symbols_entities.py -k load_entities -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Create the committed scaffold config**

Create `config/trends_entities.yaml` (no real mids yet — populated later via the build script + human review; empty = identical to current behavior):

```yaml
# Ticker -> approved Google Knowledge Graph entity for Google Trends queries.
# The scan queries `mid`; `title` is for human verification only.
#
# Any ticker NOT listed here is queried by its raw ticker string (fallback),
# exactly as before. Populate entries by running the dev-only helper and
# reviewing its candidates by hand:
#
#     python3 scripts/resolve_trends_entities.py
#
# Only commit a mid once you have confirmed the entity title/type is correct.
# Example (commented — replace with real, reviewed entries):
#
# VOX:
#   mid: /m/xxxxxxx
#   title: Communication Services Select Sector SPDR Fund
```

- [ ] **Step 6: Commit**

```bash
git add src/data/trends_symbols.py config/trends_entities.yaml tests/test_trends_symbols_entities.py
git commit -m "feat: load ticker->entity mid config with string fallback" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire the config into `scan.py`

**Files:**
- Modify: `scan.py` — the sentiment section (imports + `fetch_symbol_trends` call, around lines 277-290)

**Interfaces:**
- Consumes: `load_entities`, `fetch_symbol_trends(entities=...)` (Tasks 2-3).
- Produces: no new interface; wires config → fetch and logs coverage.

- [ ] **Step 1: Update the import**

In `scan.py`, extend the trends import (currently `build_symbol_map, fetch_symbol_trends, score_symbol_sentiment`):

```python
    from src.data.trends_symbols import (
        build_symbol_map, fetch_symbol_trends, score_symbol_sentiment,
        load_entities,
    )
```

- [ ] **Step 2: Load the config and pass it to the fetch**

Replace the `_symbol_map = ...` / `_trends_by_key = fetch_symbol_trends(_symbol_map)` lines with:

```python
    _symbol_map = build_symbol_map(universe, _sector_etfs, blocklist=_blocklist)
    _entities = load_entities("config/trends_entities.yaml")
    _resolved = sum(1 for syms in _symbol_map.values() for s in syms if s in _entities)
    _total = sum(len(syms) for syms in _symbol_map.values())
    logger.info("Trends entities: %d/%d ticker-slots resolved to a mid (rest fall back to strings)",
                _resolved, _total)
    _trends_by_key = fetch_symbol_trends(_symbol_map, entities=_entities)
```

- [ ] **Step 3: Verify scan wiring imports and byte-compiles**

Run: `python3 -c "import ast; ast.parse(open('scan.py').read()); print('scan.py parses')"`
Expected: `scan.py parses`

- [ ] **Step 4: Run the scan smoke suite (no regressions)**

Run: `python3 -m pytest tests/test_scan_smoke.py -v`
Expected: PASS (requires `psycopg2-binary`; if the module is missing locally, `pip3 install psycopg2-binary` first, then re-run)

- [ ] **Step 5: Commit**

```bash
git add scan.py
git commit -m "feat: wire trends_entities config into the scan" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Dev-only bootstrap script

**Files:**
- Create: `scripts/resolve_trends_entities.py`

**Interfaces:**
- Consumes: `build_symbol_map`, and `pytrends` at runtime.
- Produces: a CLI that prints proposed `ticker: {mid, title}` YAML to stdout for human review. Not imported anywhere; no test.

- [ ] **Step 1: Write the script**

Create `scripts/resolve_trends_entities.py`:

```python
#!/usr/bin/env python3
"""Dev-only: propose Google Knowledge Graph entities for each universe ticker.

Prints candidate `ticker: {mid, title}` YAML to stdout. Review by hand and copy
only the correct entries into config/trends_entities.yaml — do NOT pipe this
straight into the config. This script is never imported by scan.py or run in CI;
it exists solely to bootstrap/refresh the curated config.

Usage:
    python3 scripts/resolve_trends_entities.py
"""
import sys
import time

import yaml

sys.path.insert(0, ".")
from src.data.trends_symbols import build_symbol_map  # noqa: E402


def main() -> int:
    with open("config/universe.yaml") as fh:
        universe = yaml.safe_load(fh)
    with open("config/sector_etfs.yaml") as fh:
        sector_etfs = yaml.safe_load(fh) or {}
    try:
        with open("config/trends_blocklist.yaml") as fh:
            blocklist = set(yaml.safe_load(fh) or [])
    except FileNotFoundError:
        blocklist = set()

    symbol_map = build_symbol_map(universe, sector_etfs, blocklist=blocklist)
    tickers = sorted({s for syms in symbol_map.values() for s in syms})

    from pytrends.request import TrendReq
    client = TrendReq(hl="en-US", tz=0)

    print("# Proposed entities — REVIEW BY HAND before copying into "
          "config/trends_entities.yaml")
    for t in tickers:
        try:
            suggestions = client.suggestions(t)
        except Exception as exc:  # network/rate-limit — skip, note it
            print(f"# {t}: suggestions() failed ({exc})")
            time.sleep(2)
            continue
        if not suggestions:
            print(f"# {t}: no entity candidates (will fall back to string)")
            continue
        print(f"# {t} candidates:")
        for s in suggestions:
            print(f"#   mid={s.get('mid')}  type={s.get('type')}  title={s.get('title')}")
        top = suggestions[0]
        print(f"{t}:\n  mid: {top.get('mid')}\n  title: {top.get('title')}")
        time.sleep(2)  # be gentle with Trends
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it byte-compiles (no execution — needs network)**

Run: `python3 -c "import ast; ast.parse(open('scripts/resolve_trends_entities.py').read()); print('script parses')"`
Expected: `script parses`

- [ ] **Step 3: Commit**

```bash
git add scripts/resolve_trends_entities.py
git commit -m "chore: dev script to propose Trends entity mids" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Update the sentiment-page explainer (EN + SV)

**Files:**
- Modify: `dashboard/templates/sentiment.html.j2` (the `<h4>Method</h4>` paragraph)
- Modify: `dashboard/templates/_i18n.html.j2` (the Swedish `guide_body_sentiment` `<h4>Metod</h4>` paragraph)

**Interfaces:** none (copy change only).

- [ ] **Step 1: Update the English Method paragraph**

In `dashboard/templates/sentiment.html.j2`, find the `<h4>Method</h4>` paragraph. Append a sentence about entity disambiguation. The paragraph should read (append the final sentence):

```html
      <h4>Method</h4>
      <p>For each sector we pull ~13 weeks of worldwide search interest for its primary
         keyword (e.g. Technology&nbsp;→&nbsp;<em>semiconductor</em>,
         Energy&nbsp;→&nbsp;<em>oil</em>). The score is the <strong>trend (slope)</strong>
         of that interest, then standardized across the 11 sectors (z-score):
         <span class="signal-hi">positive = rising attention vs peers</span>,
         <span class="signal-lo">negative = fading</span>.
         Where a ticker maps to a known Google Knowledge&nbsp;Graph <strong>entity</strong>,
         we query that entity rather than the raw ticker string, to avoid name
         collisions (e.g. a ticker that is also a common word); tickers without a
         known entity fall back to the plain string.</p>
```

Note: if this file already differs on this branch (e.g. the derived-signals branch reworded it), adapt: keep the existing wording and just append the "Where a ticker maps to a known Google Knowledge Graph entity…" sentence to the Method paragraph.

- [ ] **Step 2: Update the Swedish Method paragraph**

In `dashboard/templates/_i18n.html.j2`, find `guide_body_sentiment` → `<h4>Metod</h4>` and append the Swedish equivalent sentence:

```html
      <h4>Metod</h4>
      <p>För varje sektor hämtar vi ~13 veckors världsomspännande sökintresse för dess primära nyckelord (t.ex. Teknik&nbsp;→&nbsp;<em>semiconductor</em>, Energi&nbsp;→&nbsp;<em>oil</em>). Poängen är <strong>trenden (lutningen)</strong> i det intresset, sedan standardiserad över de 11 sektorerna (z-värde): <span class="signal-hi">positivt = stigande uppmärksamhet mot jämnåriga</span>, <span class="signal-lo">negativt = avtagande</span>. Där en ticker motsvarar en känd Google Knowledge&nbsp;Graph-<strong>entitet</strong> frågar vi efter den entiteten i stället för den råa ticker-strängen, för att undvika namnkrockar; tickers utan känd entitet faller tillbaka på strängen.</p>
```

- [ ] **Step 3: Verify both templates still render**

Run:
```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('dashboard/templates'))
html = env.get_template('sentiment.html.j2').render(
    scan_date='x', active_scan_id=1, sentiment_scatter_json='{}',
    plotly_bundle='x')
assert 'Knowledge' in html
print('sentiment.html.j2 renders with entity note')
"
```
Expected: `sentiment.html.j2 renders with entity note`

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/sentiment.html.j2 dashboard/templates/_i18n.html.j2
git commit -m "docs: note entity disambiguation on the sentiment page" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Backlog hygiene

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:** none.

- [ ] **Step 1: Strike the Topics bullet in the queued enrichment item**

In `BACKLOG.md`, under "Sentiment page — enrichment", replace the first bullet ("Trends *topics* (entity mids) over raw ticker strings…") with a struck-through, shipped marker:

```markdown
- ~~**Trends *topics* (entity mids) over raw ticker strings.**~~ *(shipped — see Done)*
```

- [ ] **Step 2: Add the Done entry**

In `BACKLOG.md`, add to the top of the `## Done` list:

```markdown
- ~~Sentiment — Trends entity-mid resolution~~ — `fetch_symbol_trends` now queries a
  ticker's Google Knowledge Graph **entity mid** instead of the ambiguous raw string
  where one is curated in `config/trends_entities.yaml`, killing collision false-positives
  (the `VOX`→Vox Media / `LOGS`→the-word class). Per-ticker term substitution +
  column re-keying keep `_aggregate`/scoring unchanged (ticker-keyed); tickers without a
  curated mid fall back to strings, so the change is strictly additive. A dev-only
  `scripts/resolve_trends_entities.py` proposes candidates for human review; the scan
  path never calls `suggestions()`. Toggle-only. The committed config ships empty —
  real mids are added after running the script and eyeballing each entity. *(2026-07-04)*
```

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark Trends entity-mids done in backlog" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Full suite green:** `python3 -m pytest -q` → branch baseline + **9** new entity tests, 6 skipped (DB modules without psycopg2, or pass with `psycopg2-binary` installed). No pre-existing tests should regress.
- [ ] **No `docs/` staged:** `git status --porcelain docs/` → empty.
- [ ] **Diff is source-only:** `git diff --stat main...HEAD` touches only `src/`, `scripts/`, `config/`, `dashboard/templates/`, `tests/`, `BACKLOG.md`, `design/`.
- [ ] Run `/code-review`, address findings, then `git push -u origin feature/trends-entity-mids`. **Do not merge** (Jonas merges manually).

## Post-merge manual step (not part of this PR's code)

Populate `config/trends_entities.yaml` with real, reviewed mids:
1. Run `python3 scripts/resolve_trends_entities.py > /tmp/proposed_entities.yaml`.
2. Eyeball each candidate's `title`/`type`; copy only correct entries into `config/trends_entities.yaml`.
3. Commit the curated config on a small follow-up branch. Until then, behavior equals today (all string fallback).
