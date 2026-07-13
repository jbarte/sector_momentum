# Sentiment Honesty Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Google-Trends sentiment signal honest about data quality — dead sectors get NULL instead of fake z-scores, the momentum window is pinned to 13 weeks, the health log reports real coverage, and a dead config weight is removed.

**Architecture:** Four independent fixes in `src/data/trends_symbols.py`, `scan.py`, `src/scoring.py`, and `config/weights.yaml`. The coverage guard (`_aggregate` + `score_symbol_sentiment`) is the core change; everything else is cleanup that prevents the old breakage from being hidden. No schema changes, no dashboard changes.

**Tech Stack:** Python 3, pandas, numpy, pytest

## Global Constraints

- Branch: `fix/sentiment-honesty` (already created from main)
- No schema changes — `sentiment_score` is already a nullable REAL
- No dashboard code changes — existing NaN→NULL→"—" rendering already correct
- Signal stays toggle-only / info-only — never blended into canonical composite
- Spec: `design/specs/2026-07-13-sentiment-honesty-design.md`

---

### Task 1: `_aggregate` — omit dead sector keys

**Files:**
- Modify: `src/data/trends_symbols.py:262-279` (`_aggregate` function)
- Test: `tests/test_trends_symbols_score.py`

**Interfaces:**
- Consumes: `norm_by_symbol` dict and `symbol_map` dict (unchanged inputs)
- Produces: `dict[str, pd.Series]` — keys with no live symbols are **absent**, not zero-filled. Consumed by `score_symbol_sentiment` (Task 2) and by `scan.py` step 8's derived-signals loop (Task 3).

- [ ] **Step 1: Write the failing tests**

Add two tests to `tests/test_trends_symbols_score.py`:

```python
def test_aggregate_omits_dead_key():
    """A sector-key with no live symbols is absent from _aggregate's result."""
    from src.data.trends_symbols import _aggregate
    norm_by_symbol = {
        "AAPL": [1.0, 2.0, 3.0],
        "DEAD": [0.0, 0.0, 0.0],
    }
    symbol_map = {
        "US|Technology": ["AAPL"],
        "US|Energy": ["DEAD"],
    }
    result = _aggregate(norm_by_symbol, symbol_map, window=3)
    assert "US|Technology" in result
    assert "US|Energy" not in result, "Dead key should be omitted, not zero-filled"


def test_aggregate_omits_key_with_missing_symbols():
    """A sector-key whose symbols aren't in norm_by_symbol at all is omitted."""
    from src.data.trends_symbols import _aggregate
    norm_by_symbol = {"AAPL": [1.0, 2.0, 3.0]}
    symbol_map = {
        "US|Technology": ["AAPL"],
        "US|Energy": ["XOM"],  # not in norm_by_symbol
    }
    result = _aggregate(norm_by_symbol, symbol_map, window=3)
    assert "US|Technology" in result
    assert "US|Energy" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trends_symbols_score.py::test_aggregate_omits_dead_key tests/test_trends_symbols_score.py::test_aggregate_omits_key_with_missing_symbols -v`
Expected: FAIL — `_aggregate` currently returns dead keys with a zero-series.

- [ ] **Step 3: Implement the fix**

In `src/data/trends_symbols.py`, change `_aggregate` (lines 262-279):

```python
def _aggregate(
    norm_by_symbol: dict[str, list[float]],
    symbol_map: dict[str, list[str]],
    window: int = 13,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for sector_key, symbols in symbol_map.items():
        live = [
            norm_by_symbol[s]
            for s in symbols
            if s in norm_by_symbol and any(v != 0 for v in norm_by_symbol[s])
        ]
        if live:
            arr = np.array(live, dtype=float)
            out[sector_key] = pd.Series(arr.mean(axis=0), dtype=float)
    return out
```

The only change: remove the `if not live: out[sector_key] = pd.Series([0.0] * window, …)` branch — dead keys are simply not added to `out`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trends_symbols_score.py -v`
Expected: All PASS (new tests + existing `test_rising_key_scores_above_falling` still passes since its keys all have live data).

- [ ] **Step 5: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_score.py
git commit -m "fix: omit dead sector keys from _aggregate instead of zero-filling"
```

---

### Task 2: `score_symbol_sentiment` coverage guard + `_MOMENTUM_WINDOW` constant

**Files:**
- Modify: `src/data/trends_symbols.py:718-729` (`score_symbol_sentiment`), lines 226-242 (`derived_signals`), and add constants near top
- Test: `tests/test_trends_symbols_score.py`

**Interfaces:**
- Consumes: `trends_by_key: dict[str, pd.Series]` — now live-only (dead keys omitted by Task 1's `_aggregate`)
- Produces: `pd.Series` indexed by region|sector with z-scores for live keys (≥ threshold) or all-NaN (< threshold). Dead keys absent. Consumed by `scan.py` step 8 (Task 3). Also produces `_MIN_LIVE_SECTORS = 8` and `_MOMENTUM_WINDOW = 13` constants used by Task 3's health log.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trends_symbols_score.py`:

```python
def test_score_below_threshold_returns_all_nan():
    """Fewer than _MIN_LIVE_SECTORS live keys → all-NaN Series."""
    from src.data.trends_symbols import _MIN_LIVE_SECTORS
    # Build fewer keys than threshold
    trends = {
        f"US|Sector{i}": pd.Series([float(i)] * 4)
        for i in range(_MIN_LIVE_SECTORS - 1)
    }
    s = score_symbol_sentiment(trends)
    assert len(s) == len(trends)
    assert s.isna().all(), f"Expected all NaN, got {s.to_dict()}"


def test_score_at_threshold_returns_z_scores():
    """Exactly _MIN_LIVE_SECTORS live keys → valid z-scores (not NaN)."""
    from src.data.trends_symbols import _MIN_LIVE_SECTORS
    trends = {
        f"US|Sector{i}": pd.Series([float(i + 1) * (j + 1) for j in range(13)])
        for i in range(_MIN_LIVE_SECTORS)
    }
    s = score_symbol_sentiment(trends)
    assert len(s) == _MIN_LIVE_SECTORS
    assert not s.isna().any(), f"Expected no NaN, got {s.to_dict()}"
    assert abs(s.mean()) < 1e-9, "Cross-sectional z should be centred near zero"


def test_score_slopes_trailing_momentum_window_only():
    """score_symbol_sentiment slopes the trailing _MOMENTUM_WINDOW weeks, not the full series."""
    from src.data.trends_symbols import _MOMENTUM_WINDOW
    n = 10  # need enough keys to pass threshold
    # Build 52-point series where the first 39 weeks are noise but last 13 are
    # a clean ramp, so the trailing-13 slope is identical for all keys.
    trends_full = {}
    trends_tail = {}
    for i in range(n):
        ramp = [float(i + 1) * (j + 1) for j in range(_MOMENTUM_WINDOW)]
        prefix = [999.0] * (52 - _MOMENTUM_WINDOW)  # ignored junk
        trends_full[f"US|S{i}"] = pd.Series(prefix + ramp)
        trends_tail[f"US|S{i}"] = pd.Series(ramp)
    s_full = score_symbol_sentiment(trends_full)
    s_tail = score_symbol_sentiment(trends_tail)
    pd.testing.assert_series_equal(s_full, s_tail, atol=1e-9)


def test_empty_trends_returns_empty_series():
    """Empty input → empty Series (no crash)."""
    s = score_symbol_sentiment({})
    assert len(s) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trends_symbols_score.py::test_score_below_threshold_returns_all_nan tests/test_trends_symbols_score.py::test_score_at_threshold_returns_z_scores tests/test_trends_symbols_score.py::test_score_slopes_trailing_momentum_window_only tests/test_trends_symbols_score.py::test_empty_trends_returns_empty_series -v`
Expected: FAIL — `_MIN_LIVE_SECTORS` doesn't exist yet; `score_symbol_sentiment` slopes the full series; no threshold check.

- [ ] **Step 3: Implement the constants and `score_symbol_sentiment` changes**

Near the top of `src/data/trends_symbols.py` (after the `DEFAULT_*` constants, around line 23):

```python
_MIN_LIVE_SECTORS = 8
_MOMENTUM_WINDOW = 13
```

Replace `score_symbol_sentiment` (lines 718-729):

```python
def score_symbol_sentiment(trends_by_key: dict[str, pd.Series]) -> pd.Series:
    """Score symbol sentiment: slope of trailing _MOMENTUM_WINDOW weeks, z-scored.

    Returns a Series indexed by region|sector. If fewer than _MIN_LIVE_SECTORS
    keys are present, returns all-NaN (cross-section too thin to z-score).
    Dead keys (absent from trends_by_key) are not in the output.
    """
    if not trends_by_key:
        return pd.Series(dtype=float)
    if len(trends_by_key) < _MIN_LIVE_SECTORS:
        return pd.Series(float("nan"), index=list(trends_by_key.keys()), dtype=float)
    slopes = {
        key: _slope(list(series)[-_MOMENTUM_WINDOW:])
        for key, series in trends_by_key.items()
    }
    z = _cross_zscore(slopes)
    return pd.Series(z, dtype=float)
```

- [ ] **Step 4: Update `derived_signals` to use `_MOMENTUM_WINDOW`**

Replace the `recent` line in `derived_signals` (line 234):

```python
def derived_signals(series) -> dict[str, float]:
    vals = list(series)
    recent = vals[-_MOMENTUM_WINDOW:] if len(vals) >= _MOMENTUM_WINDOW else vals
    return {
        "momentum": _slope(recent),
        "acceleration": _acceleration(recent),
        "range_position": _range_position(recent),
        "spike": _spike_z(recent),
        "volatility": _volatility(recent),
        "seasonal_ratio": _seasonal_ratio(vals),
    }
```

The only change: `13` → `_MOMENTUM_WINDOW` (twice — the comparison and the slice).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_trends_symbols_score.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/data/trends_symbols.py tests/test_trends_symbols_score.py
git commit -m "fix: add coverage guard + pin momentum window to 13 weeks"
```

---

### Task 3: `scan.py` NaN plumbing + honest health log + fetch mitigations

**Files:**
- Modify: `scan.py:319-322` (sentiment score reindex + health log)
- Modify: `src/data/trends_symbols.py:686` (default `sleep_s` in `fetch_symbol_trends`)
- No new test file — the changes are in the scan runner (integration path), and the guard behavior is already tested in Tasks 1-2.

**Interfaces:**
- Consumes: `score_symbol_sentiment` output (Task 2) — now live-only or all-NaN
- Consumes: `_MIN_LIVE_SECTORS` constant (Task 2) — for the log message
- Produces: `sentiment_score` Series with NaN for dead sectors (consumed by `score_all`)

- [ ] **Step 1: Fix the reindex fill_value in `scan.py`**

Change line 320 from:
```python
sentiment_score = sentiment_score.reindex(wide_df.index, fill_value=0.0)
```
to:
```python
sentiment_score = sentiment_score.reindex(wide_df.index)
```

Removing `fill_value=0.0` means absent keys (dead sectors, or all keys when under threshold) become NaN — the default pandas fill for `.reindex()`.

- [ ] **Step 2: Replace the health log**

Replace lines 321-322:
```python
_live = int((sentiment_score != 0).sum())
logger.info("Symbol sentiment: %d/%d sector-keys non-neutral", _live, len(wide_df.index))
```

with:
```python
from src.data.trends_symbols import _MIN_LIVE_SECTORS
_live = len(_trends_by_key)
_total = len(wide_df.index)
logger.info(
    "Symbol sentiment: %d/%d sectors have live Trends data (guard threshold: %d)",
    _live, _total, _MIN_LIVE_SECTORS,
)
if _live < _MIN_LIVE_SECTORS:
    logger.warning(
        "Symbol sentiment: live count %d < threshold %d — all scores NULLed for this scan",
        _live, _MIN_LIVE_SECTORS,
    )
```

This counts live series *before* scoring (via `len(_trends_by_key)` — `_aggregate` now omits dead keys, so this count is the live count directly).

- [ ] **Step 3: Bump default inter-batch sleep**

In `src/data/trends_symbols.py`, change `fetch_symbol_trends`'s default `sleep_s` parameter from `20.0` to `25.0` (line 686):

```python
def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = DEFAULT_ANCHOR,
    client=None,
    timeframe: str = "today 12-m",
    window: int = 52,
    batch_size: int = 4,
    sleep_s: float = 25.0,
    ...
```

Also update the `scan.py` call site if it passes `sleep_s` explicitly — currently it does not (uses the default), so no call-site change needed.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: All PASS. The `test_score_all_*` tests in `tests/test_scoring.py` still pass because they provide explicit `sentiment_score` Series, not relying on the scan.py plumbing.

- [ ] **Step 5: Commit**

```bash
git add scan.py src/data/trends_symbols.py
git commit -m "fix: NaN plumbing for dead sectors + honest health log + bump sleep"
```

---

### Task 4: Remove dead pillar weight + lazy read in `score_all`

**Files:**
- Modify: `config/weights.yaml:1-7` (delete `pillars` block, add comment)
- Modify: `src/scoring.py:163-164` (lazy `.get()` for pillar weights)
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `config/weights.yaml` (removing the `pillars` block)
- Produces: `score_all` that doesn't `KeyError` when `pillars` is absent. Existing behavior on the `blend_sentiment=False` path is unchanged.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_scoring.py`:

```python
def test_score_all_without_pillars_block(tmp_path):
    """score_all works when weights.yaml lacks a 'pillars' block entirely."""
    import yaml

    weights = {
        "data_pillar": {"level": 0.5, "change": 0.5},
        # No 'pillars' key at all — this is the post-cleanup config shape
    }
    weights_file = tmp_path / "weights.yaml"
    weights_file.write_text(yaml.dump(weights))

    signals = pd.DataFrame(
        {col: [1.0, 2.0, 3.0] for col in [
            "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
            "acceleration", "above_50dma", "above_200dma", "ma50_slope",
            "obv_slope",
        ]},
        index=["US|Tech", "US|Energy", "EU|Tech"],
    )
    # Must not raise KeyError
    result = score_all(signals, weights_path=str(weights_file), blend_sentiment=False)
    assert "composite" in result.columns
    assert len(result) == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_scoring.py::test_score_all_without_pillars_block -v`
Expected: FAIL with `KeyError: 'pillars'` — `score_all` reads `cfg["pillars"]` unconditionally.

- [ ] **Step 3: Make the pillar read lazy in `score_all`**

In `src/scoring.py`, replace lines 163-164:
```python
    data_weight: float = float(cfg["pillars"]["data"])
    sentiment_weight: float = float(cfg["pillars"]["sentiment"])
```

with:
```python
    _pillars = cfg.get("pillars", {})
    data_weight: float = float(_pillars.get("data", 1.0))
    sentiment_weight: float = float(_pillars.get("sentiment", 0.0))
```

Defaults: `data=1.0` (pure data), `sentiment=0.0` (no blend) — matching the `blend_sentiment=False` behavior.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_scoring.py -v`
Expected: All PASS.

- [ ] **Step 5: Remove the `pillars` block from `weights.yaml`**

Replace `config/weights.yaml` content:

```yaml
# Sentiment is toggle-only by design: the composite score is pure data.
# Revisit pillar blending only if/when FinBERT provides signed polarity.
# (The old pillars: {data: 0.70, sentiment: 0.30} block was removed because
# scan.py always passes blend_sentiment=False and the weight was dead config.)

# Within data pillar: level vs change split
data_pillar:
  level: 0.50
  change: 0.50

# Signal display ordering for the dashboard breakdown panel.
# The actual equal-weight signal lists are hardcoded in src/scoring.py
# (_LEVEL_SIGNALS, _CHANGE_SIGNALS); these keys control column order in
# dashboard/build.py only. Values are ignored.
level_signals:
  rs_ratio: 1.0
  return_3m: 1.0
  return_6m: 1.0
  above_50dma: 1.0
  above_200dma: 1.0

change_signals:
  rs_momentum: 1.0
  acceleration: 1.0
  ma50_slope: 1.0
  obv_slope: 1.0

# Signal computation parameters
signal_params:
  rs_momentum_fast: 5     # RRG RS-Momentum lookback (was 1; standard RRG ~10)
```

- [ ] **Step 6: Commit**

```bash
git add config/weights.yaml src/scoring.py tests/test_scoring.py
git commit -m "fix: remove dead pillar weight, make score_all read lazy"
```

---

### Task 5: BACKLOG.md update + full test suite + push + PR

**Files:**
- Modify: `BACKLOG.md` — delete the Queued "Sentiment honesty fixes" section, add Done entry
- Copy: `design/specs/2026-07-13-sentiment-honesty-design.md` from `chore/backlog-sentiment-honesty` branch (it was committed there but not on main)

**Interfaces:**
- None — this is the wrap-up task.

- [ ] **Step 1: Copy the design spec into this branch**

The spec was committed on `chore/backlog-sentiment-honesty` at `e0e49dd`. Cherry-pick or copy it into this branch:

```bash
git checkout chore/backlog-sentiment-honesty -- design/specs/2026-07-13-sentiment-honesty-design.md
git add design/specs/2026-07-13-sentiment-honesty-design.md
```

- [ ] **Step 2: Update BACKLOG.md**

Delete the Queued "Sentiment honesty fixes" section entirely (per backlog lifecycle: shipping deletes Queued, adds Done). Add a Done entry at the top of the Done list:

```
- **Sentiment honesty fixes** — coverage guard (`_aggregate` omits dead keys, `score_symbol_sentiment` z-scores live subset with `_MIN_LIVE_SECTORS=8` threshold), pinned `_MOMENTUM_WINDOW=13`, honest health log, bumped fetch sleep, removed dead pillar weight from `config/weights.yaml`. `fix/sentiment-honesty` PR #__. (2026-07-13)
```

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: All PASS.

- [ ] **Step 4: Commit and push**

```bash
git add BACKLOG.md design/specs/2026-07-13-sentiment-honesty-design.md
git commit -m "chore: move sentiment honesty to Done in BACKLOG.md"
git push -u origin fix/sentiment-honesty
```

- [ ] **Step 5: Open PR**

```bash
gh pr create --title "fix: sentiment honesty — coverage guard, pinned window, honest log" --body "$(cat <<'EOF'
## Summary
- `_aggregate` omits dead sector keys instead of zero-filling → dead sectors no longer get fake z-scores
- `score_symbol_sentiment` adds `_MIN_LIVE_SECTORS = 8` threshold — below it, returns all-NaN (cross-section too thin)
- `_MOMENTUM_WINDOW = 13` shared constant pins the slope window (was silently changed to 52 by PR #79)
- `derived_signals` uses the same `_MOMENTUM_WINDOW` for its `momentum`/`recent` slice
- `scan.py` reindex drops `fill_value=0.0` → dead sectors become NaN → SQL NULL → dashboard "—"
- Health log counts live series *before* z-scoring (was counting post-z non-zeros, always 22/22)
- Default `sleep_s` bumped from 20→25 to reduce 429 pressure
- Deleted dead `pillars` block from `config/weights.yaml`; `score_all` reads with `.get()` defaults

## Test plan
- [ ] `pytest -v` — all existing + new tests pass
- [ ] Verify scan 131 (first production run with these guards) recovers coverage or honestly reports NULL
- [ ] Check dashboard renders "—" for dead sectors' sentiment column

## Post-merge manual check
Verify scan ~131 as first production run of these guard changes:
- Health log should show real live count (not "22/22 non-neutral")
- If coverage < 8, sentiment scores should be NULL in DB

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
