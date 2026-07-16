# Hub-file split — eliminate merge-conflict hotspots

## Problem

Three files are modified by every dashboard feature, causing merge conflicts
whenever branches overlap:

| File | Conflict pattern |
|---|---|
| `dashboard/build.py` | Every feature adds imports (top block), computation calls (middle), and context dict entries (render calls) — all adjacent-line additions that git cannot auto-merge |
| `dashboard/templates/_i18n.html.j2` | Every feature appends keys to one monolithic JS `SV` / `SV_HTML` dict |
| `dashboard/templates/_style.html.j2` | Every feature appends CSS rules to a single 755-line file |

Recent history: PRs #89–95 (July 15–16) each touched all three files. Every
merge required manual conflict resolution.

## Goal

Restructure so that **adding a new dashboard feature creates new files** and at
most adds single, independently-positioned lines to orchestrator files. No
user-visible change — the rendered HTML is identical.

## Success criteria

1. `python3 dashboard/build.py` produces output identical to the pre-refactor
   build (verified by diffing `docs/*.html`).
2. Future features touch the orchestrator files with at most one line each
   (an `{% include %}` or a `.update()` call).
3. Existing tests pass unchanged.

---

## Design

### 1. CSS — per-component partials

`_style.html.j2` becomes a list of `{% include %}` directives. The actual CSS
moves to `dashboard/templates/css/`:

| Partial | Contents |
|---|---|
| `_foundation.css.j2` | Design tokens (`:root` vars), reset, typography, responsive media queries |
| `_chrome.css.j2` | Command bar, macro chips, card shell, segment toggle, tab bar, tab panels, footer |
| `_tables.css.j2` | Leaderboard table, scan-index, banners, rank/trajectory badges, breakdown panel |
| `_charts.css.j2` | Plotly chart containers, drill-down controls |
| `_guides.css.j2` | Tab guides, utility row, `.signal-hi` / `.signal-lo` |
| `_sentiment.css.j2` | Sentiment controls, seasonal, rising queries |

**Convention**: new features create `css/_<feature>.css.j2` and add one
`{% include "css/_<feature>.css.j2" %}` line to `_style.html.j2`.

### 2. i18n — per-module includes inside the IIFE

The IIFE wrapper and toggle logic stay in `_i18n.html.j2`. The `SV` and
`SV_HTML` dict contents move to per-module partials under
`dashboard/templates/i18n/`. Each partial uses `Object.assign()`:

```
_i18n.html.j2 (orchestrator):
  (function () {
    var SV = {};
    var SV_HTML = {};

    {% include "i18n/_core.js.j2" %}
    {% include "i18n/_sentiment.js.j2" %}
    {% include "i18n/_backtest.js.j2" %}
    {% include "i18n/_badges.js.j2" %}
    {% include "i18n/_macro.js.j2" %}
    {% include "i18n/_guides.js.j2" %}

    // — toggle logic (stable, stays inline) —
    var els = document.querySelectorAll("[data-i18n]");
    ...
  })();
```

Each partial (e.g. `i18n/_badges.js.j2`):
```javascript
Object.assign(SV, {
  badge_scorecard_title: "Badgepoäng",
  badge_sc_badge: "Badge",
  ...
});
```

`SV_HTML` guide bodies go in `i18n/_guides.js.j2`:
```javascript
Object.assign(SV_HTML, {
  guide_body_leaderboard: `...`,
  ...
});
```

**Convention**: new features create `i18n/_<feature>.js.j2` and add one
`{% include "i18n/_<feature>.js.j2" %}` line to `_i18n.html.j2`.

### 3. build.py — module context builders

Each dashboard module that provides template context exports:

```python
def build_page_context(shared: dict) -> dict:
```

`shared` is a dict of common dependencies assembled once in `build.py`:
- `project_root` (Path)
- `all_scores_df`, `history_df`, `theme_history_df` (DataFrames)
- `universe`, `weights`, `sector_etfs`, `themes_cfg` (config dicts)
- `signals_df`, `sentiment_signals_df`, `theme_signals_df`,
  `theme_sentiment_signals_df` (DataFrames)
- `rrg_df`, `theme_rrg_df` (DataFrames)
- `scan_index`, `active_scan_id` (scan metadata)

Modules that export `build_page_context`:

| Module | Returns | Used by pages |
|---|---|---|
| `badges` | `badge_scorecard` | sectors |
| `macro` | `macro` | sectors, sentiment, themes |
| `feed` | (feed is rendered separately, no change) | — |
| `figures` | all figure JSON + drilldown data | sectors, themes |
| `sentiment` | sentiment scatter/signal rows (both cohorts) | sentiment |

Modules that don't need a context builder (their outputs are consumed
internally or have complex per-page logic): `rows`, `breakdown`, `reports`.
These stay as direct calls in `build.py`.

`build.py` assembles each page context via `.update()`:
```python
shared = { ... }

sectors_ctx = { "scan_date": scan_date, ... }  # stable base
sectors_ctx.update(figures.build_sectors_context(shared))
sectors_ctx.update(badges.build_page_context(shared))
sectors_ctx.update(macro.build_page_context(shared))
_render(template_path=..., out_path=..., context=sectors_ctx)
```

The import block is **alphabetized** so additions from concurrent branches
land at different positions instead of clustering at the end.

**Convention**: new features add a module file with `build_page_context()`,
one alphabetized import line, and one `.update()` call per target page.

---

## Scope

### In scope
- Split `_style.html.j2` into 6 CSS partials + orchestrator
- Split `_i18n.html.j2` SV/SV_HTML contents into 6 i18n partials + orchestrator
- Add `build_page_context()` to `badges`, `macro`, `figures`, `sentiment`
- Refactor `build.py` to use `shared` dict + `.update()` assembly
- Alphabetize imports in `build.py`

### Out of scope
- Template HTML restructuring (templates just `{% include %}` the same files)
- Changing scan.py, src/, or any non-dashboard code
- New tests (verified by output diffing; existing tests cover module functions)
- Changing generated `docs/` output

## Verification

1. Before refactoring: `python3 dashboard/build.py` → save `docs/*.html` as baseline
2. After refactoring: `python3 dashboard/build.py` → diff against baseline
3. `pytest` — all existing tests pass
4. Visual spot-check in browser (dev server preview)
