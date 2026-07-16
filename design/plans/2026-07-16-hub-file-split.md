# Hub-file split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate recurring merge conflicts by splitting three hub files (`_style.html.j2`, `_i18n.html.j2`, `build.py`) into per-module partials and context builders.

**Architecture:** CSS and i18n move to per-component partial files under `dashboard/templates/css/` and `dashboard/templates/i18n/`, included by thin orchestrator files. `build.py` delegates context assembly to module-level `build_page_context()` functions, assembling each page's context via `.update()` calls. Imports are alphabetized so additions from concurrent branches land at different positions.

**Tech Stack:** Jinja2 templates, vanilla JS, Python

## Global Constraints

- No user-visible change — rendered HTML must produce identical dashboard behavior.
- Do not commit `docs/` from feature branches (CI owns it).
- `{% include %}` paths are relative to `dashboard/templates/`.
- Existing tests must pass unchanged.

---

### Task 1: Split CSS into per-component partials

**Files:**
- Create: `dashboard/templates/css/_foundation.css.j2`
- Create: `dashboard/templates/css/_chrome.css.j2`
- Create: `dashboard/templates/css/_tables.css.j2`
- Create: `dashboard/templates/css/_charts.css.j2`
- Create: `dashboard/templates/css/_guides.css.j2`
- Create: `dashboard/templates/css/_sentiment.css.j2`
- Modify: `dashboard/templates/_style.html.j2` (replace 755 lines with 6 includes)

**Interfaces:**
- Consumes: current `_style.html.j2` content (755 lines)
- Produces: 6 CSS partial files + orchestrator. All three page templates (`index.html.j2`, `sentiment.html.j2`, `themes.html.j2`) already `{% include "_style.html.j2" %}` — no template changes needed.

- [ ] **Step 1: Save baseline HTML for diffing**

```bash
cd /Users/jonasbarte/AI\ Projects/sector_momentum
python3 dashboard/build.py
cp docs/index.html /tmp/baseline_index.html
cp docs/sentiment.html /tmp/baseline_sentiment.html
cp docs/themes.html /tmp/baseline_themes.html
```

- [ ] **Step 2: Create `dashboard/templates/css/` directory**

```bash
mkdir -p dashboard/templates/css
```

- [ ] **Step 3: Create the 6 CSS partial files**

Read `dashboard/templates/_style.html.j2` and create each partial by copying
the specified line ranges. Each file gets the raw CSS content (no `<style>` tags
— the orchestrator wraps them).

| Partial | Source lines from `_style.html.j2` |
|---|---|
| `css/_foundation.css.j2` | Lines 1–67 (tokens, reset, body), then lines 515–527 (responsive media queries) |
| `css/_chrome.css.j2` | Lines 69–231 (command bar through tab panels), then lines 738–755 (site footer) |
| `css/_tables.css.j2` | Lines 233–373 (leaderboard table through trajectory badges), then lines 529–693 (breakdown panel) |
| `css/_charts.css.j2` | Lines 375–420 (chart containers, drill-down controls) |
| `css/_guides.css.j2` | Lines 422–477 (tab guides, utility row, `.signal-hi`/`.signal-lo`) |
| `css/_sentiment.css.j2` | Lines 479–513 (rank settings popover), then lines 696–736 (sentiment toggle/seasonal/rising) |

Copy the lines exactly, preserving all whitespace and comments. Each partial
is a self-contained block of CSS rules.

- [ ] **Step 4: Rewrite `_style.html.j2` as an orchestrator**

Replace the entire content of `dashboard/templates/_style.html.j2` with:

```css
{% include "css/_foundation.css.j2" %}
{% include "css/_chrome.css.j2" %}
{% include "css/_tables.css.j2" %}
{% include "css/_charts.css.j2" %}
{% include "css/_guides.css.j2" %}
{% include "css/_sentiment.css.j2" %}
```

- [ ] **Step 5: Verify output is byte-identical**

```bash
python3 dashboard/build.py
diff /tmp/baseline_index.html docs/index.html
diff /tmp/baseline_sentiment.html docs/sentiment.html
diff /tmp/baseline_themes.html docs/themes.html
```

All three diffs must be empty (Jinja `{% include %}` inlines the content
identically to having it all in one file).

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/css/ dashboard/templates/_style.html.j2
git commit -m "refactor: split _style.html.j2 into per-component CSS partials"
```

---

### Task 2: Split i18n into per-module includes

**Files:**
- Create: `dashboard/templates/i18n/_core.js.j2`
- Create: `dashboard/templates/i18n/_sentiment.js.j2`
- Create: `dashboard/templates/i18n/_backtest.js.j2`
- Create: `dashboard/templates/i18n/_badges.js.j2`
- Create: `dashboard/templates/i18n/_macro.js.j2`
- Create: `dashboard/templates/i18n/_guides.js.j2`
- Modify: `dashboard/templates/_i18n.html.j2`

**Interfaces:**
- Consumes: current `_i18n.html.j2` (269 lines — one IIFE with `SV` dict, `SV_HTML` dict, toggle logic)
- Produces: 6 i18n partial files + orchestrator. The `SV` and `SV_HTML` variables are declared in the orchestrator; partials populate them via `Object.assign()`. Toggle logic stays in the orchestrator.

- [ ] **Step 1: Create `dashboard/templates/i18n/` directory**

```bash
mkdir -p dashboard/templates/i18n
```

- [ ] **Step 2: Create `i18n/_core.js.j2`**

Core chrome, navigation, table columns, scan-index, and digest keys:

```javascript
Object.assign(SV, {
  title: "Sektormomentum",
  disclaimer: "Analysverktyg, inte investeringsrådgivning.",
  tab_leaderboard: "Topplista",
  tab_rrg: "RRG",
  tab_drilldown: "Detaljvy",
  tab_movers: "Rörelser",
  tab_history: "Historik",
  tab_backtest: "Backtest",
  segment_sectors: "Sektorer",
  segment_themes: "Teman",
  segment_sentiment: "Sentiment",
  col_theme: "Tema",
  note_themes: "Tematiska ETF:er rankade med samma momentumpelare, mot ett globalt jämförelseindex (ACWI). Endast information; separat från sektortopplistan.",
  themes_empty: "Ingen temadata än — kör en skanning.",
  includeSentiment: "Inkludera sentiment i rankningen",
  weight: "Vikt:",
  col_rank: "#",
  col_sector: "Sektor",
  col_region: "Region",
  col_composite: "Komposit",
  col_level: "Nivå",
  col_change: "Förändring",
  col_data: "Data",
  col_sentiment: "Sentiment",
  col_rankdelta: "Rank Δ",
  col_trend: "Trend",
  note_sentiment: "Sentimentvikten påverkar endast topplistans rankning.",
  leaderboard_empty: "Ingen data tillgänglig ännu.",
  scans_empty: "Inga skanningar ännu.",
  guide_summary: "Hur du läser den här fliken",
  guide_summary_sentiment: "Hur beräknas sentimentpoängen?",
  scan_viewing: "Visar skanning #",
  scan_back_to_latest: "Tillbaka till senaste",
  si_scan: "Skanning",
  si_run: "Körning (UTC)",
  si_sectors: "Sektorer",
  si_top: "Topp-sektor",
  si_report: "Rapport",
  si_download: "ladda ner",
  digest_new_top5: "Nya i topp 5:",
  digest_gains: "Störst uppgång:",
  digest_drops: "Störst nedgång:"
});
```

- [ ] **Step 3: Create `i18n/_sentiment.js.j2`**

Sentiment-derived-signals and rising-queries keys:

```javascript
Object.assign(SV, {
  note_sentiment_themes: "Temasentiment hämtar världsomspännande sökintresse för varje temas nyckelord, z-poängsatt över temana. Endast information — påverkar aldrig någon rankning.",
  sent_themes_empty: "Ingen temasentimentdata än — kör en skanning.",
  sent_derived_heading: "Härledda Trends-signaler",
  sent_col_sector: "Sektor",
  sent_col_region: "Region",
  sent_col_momentum: "Momentum",
  sent_col_acceleration: "Acceleration",
  sent_col_range: "Intervallpos.",
  sent_col_spike: "Spik",
  sent_col_volatility: "Volatilitet",
  sent_col_attention: "Uppmärksamhet",
  sent_col_seasonal: "Säsong",
  rising_heading: "Stigande sökningar",
  rising_col_query: "Sökterm",
  rising_col_growth: "Tillväxt"
});
```

- [ ] **Step 4: Create `i18n/_backtest.js.j2`**

Backtest tab keys:

```javascript
Object.assign(SV, {
  note_backtest: "Månatlig rotation av topprankade sektorer, likaviktad, endast lång, utan kostnader. Endast prisbaserade signaler (bredd och sentiment exkluderade). Varje region poängsätts inom sin egen kohort.",
  note_backtest_themes: "Månatlig rotation av topprankade teman, likaviktad, endast lång. Endast prisbaserade signaler (sentiment exkluderat). Teman poängsätts inom sin egen kohort mot ACWI.",
  bt_themes_empty: "Ingen temabacktest ännu. Kör <code>python backtest.py</code>.",
  bt_track: "Spår",
  bt_window: "Period",
  bt_stratcagr: "Strategi CAGR",
  bt_benchcagr: "Index CAGR",
  bt_sharpe: "Sharpe (rf=0)",
  bt_maxdd: "Max nedgång",
  bt_hitrate: "Träffsäkerhet",
  bt_turnover: "Omsättning"
});
```

- [ ] **Step 5: Create `i18n/_badges.js.j2`**

Badge / trajectory / setup keys:

```javascript
Object.assign(SV, {
  badge_entry: "▲ Insteg",
  badge_exit: "▼ Ursteg",
  badge_scorecard_title: "Badgepoäng",
  badge_scorecard_desc: "5-dagars framåtavkastning efter varje badge.",
  badge_sc_badge: "Badge",
  badge_sc_count: "Antal",
  badge_sc_hit_rate: "Träffgrad",
  badge_sc_mean: "Medel",
  badge_sc_median: "Median",
  badge_rising_fast: "↑↑ Stiger snabbt",
  badge_rising: "↑ Stiger",
  badge_flat: "→ Flat",
  badge_falling: "↓ Faller",
  badge_falling_fast: "↓↓ Faller snabbt",
  badge_no_badge: "Ingen badge"
});
```

- [ ] **Step 6: Create `i18n/_macro.js.j2`**

Macro regime chip keys:

```javascript
Object.assign(SV, {
  macro_vix_calm: "Lugnt",
  macro_vix_elevated: "Förhöjt",
  macro_vix_stressed: "Stressat",
  macro_chip_spy_above: "mot 200-DMA — över",
  macro_chip_spy_below: "mot 200-DMA — under"
});
```

- [ ] **Step 7: Create `i18n/_guides.js.j2`**

The `SV_HTML` guide bodies. Copy the entire `SV_HTML` object contents from
`_i18n.html.j2` lines 98–228 into this file, wrapped in `Object.assign`:

```javascript
Object.assign(SV_HTML, {
  guide_body_leaderboard: `...`,
  guide_body_rrg: `...`,
  guide_body_rrg_themes: `...`,
  guide_body_drilldown: `...`,
  guide_body_drilldown_themes: `...`,
  guide_body_movers: `...`,
  guide_body_movers_themes: `...`,
  guide_body_history: `...`,
  guide_body_history_themes: `...`,
  sentiment_footnote: `...`,
  guide_body_sentiment: `...`
});
```

Copy the template-literal values **verbatim** from the existing `SV_HTML`
object in `_i18n.html.j2` (lines 99–228). Do not alter the HTML content.

- [ ] **Step 8: Rewrite `_i18n.html.j2` as orchestrator**

Replace the entire content with:

```javascript
(function () {
  var SV = {};
  var SV_HTML = {};

  {% include "i18n/_core.js.j2" %}
  {% include "i18n/_sentiment.js.j2" %}
  {% include "i18n/_backtest.js.j2" %}
  {% include "i18n/_badges.js.j2" %}
  {% include "i18n/_macro.js.j2" %}
  {% include "i18n/_guides.js.j2" %}

  var els = document.querySelectorAll("[data-i18n]");
  els.forEach(function (el) { el.setAttribute("data-en", el.textContent); });
  var htmlEls = document.querySelectorAll("[data-i18n-html]");
  var enHtml = new Map();
  htmlEls.forEach(function (el) { enHtml.set(el, el.innerHTML); });

  function apply(lang) {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      if (!el.getAttribute("data-en")) el.setAttribute("data-en", el.textContent);
      var key = el.getAttribute("data-i18n");
      el.textContent = (lang === "sv" && SV[key] != null) ? SV[key] : el.getAttribute("data-en");
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      if (!enHtml.has(el)) enHtml.set(el, el.innerHTML);
      var key = el.getAttribute("data-i18n-html");
      el.innerHTML = (lang === "sv" && SV_HTML[key] != null) ? SV_HTML[key] : enHtml.get(el);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      if (!el.getAttribute("data-en-title")) el.setAttribute("data-en-title", el.getAttribute("title") || "");
      var key = el.getAttribute("data-i18n-title");
      el.setAttribute("title", (lang === "sv" && SV[key] != null) ? SV[key] : el.getAttribute("data-en-title"));
    });
    document.documentElement.lang = (lang === "sv") ? "sv" : "en";
    var btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = (lang === "sv") ? "EN" : "SV";
    try { localStorage.setItem("lang", lang); } catch (e) {}
  }

  window.applyLang = apply;
  window.toggleLang = function () {
    var cur = "en";
    try { cur = localStorage.getItem("lang") || "en"; } catch (e) {}
    apply(cur === "sv" ? "en" : "sv");
  };

  var saved = "en";
  try { saved = localStorage.getItem("lang") || "en"; } catch (e) {}
  apply(saved);
})();
```

- [ ] **Step 9: Build and verify translations work**

```bash
python3 dashboard/build.py
```

The HTML will NOT be byte-identical to the baseline because `Object.assign(SV, {...})`
generates different JS than a single `var SV = {...}` literal. However, the runtime
behavior is identical. Verify:

1. Build succeeds with no errors.
2. Open `docs/index.html` in the browser, click the SV toggle — all translations
   render correctly.
3. Check `docs/sentiment.html` and `docs/themes.html` similarly.

- [ ] **Step 10: Commit**

```bash
git add dashboard/templates/i18n/ dashboard/templates/_i18n.html.j2
git commit -m "refactor: split _i18n.html.j2 into per-module translation partials"
```

---

### Task 3: Refactor build.py with module context builders

**Files:**
- Modify: `dashboard/figures.py` (add `build_sectors_context`, `build_themes_context`)
- Modify: `dashboard/sentiment.py` (add `build_page_context`)
- Modify: `dashboard/badges.py` (add `build_page_context`)
- Modify: `dashboard/macro.py` (add `build_page_context`)
- Modify: `dashboard/build.py` (use shared dict + `.update()` assembly, alphabetize imports)

**Interfaces:**
- Consumes: existing module functions (unchanged)
- Produces: each module exports `build_page_context(shared: dict) -> dict` (or page-specific variants for `figures`). `build.py` calls them and merges via `.update()`.

The `shared` dict contains:
```python
shared = {
    "project_root": Path,          # project root
    "all_scores_df": DataFrame,    # full scan history (n_scans=None)
    "history_df": DataFrame,       # recent scan history (n_scans=20)
    "theme_history_df": DataFrame, # theme scan history
    "rrg_df": DataFrame,           # RRG data (n_scans=6)
    "theme_rrg_df": DataFrame,     # theme RRG data
    "universe": dict,              # config/universe.yaml
    "weights": dict,               # config/weights.yaml
    "sentiment_signals_df": DataFrame,
    "theme_sentiment_signals_df": DataFrame,
}
```

- [ ] **Step 1: Add `build_sectors_context` and `build_themes_context` to `figures.py`**

Append to the end of `dashboard/figures.py`:

```python
def build_sectors_context(shared: dict) -> dict:
    """Assemble all figure + backtest context entries for the sectors page."""
    import json as _json

    rrg_json = _build_rrg_figure(shared["rrg_df"])
    sector_signal_data, sector_keys, signals_list = _build_drilldown_data(shared["history_df"])
    movers_json = _build_movers_figure(shared["history_df"])
    history_json = _build_history_figure(shared["history_df"])
    rescore_data_json = _json.dumps(_build_rescore_data(shared["history_df"]))
    scan_history_json = _json.dumps(_build_scan_history_data(shared["all_scores_df"]))
    bt = _build_backtest_context(str(shared["project_root"] / "backtests"))

    return {
        "rrg_data_json": rrg_json,
        "drilldown_data": _json.dumps(sector_signal_data),
        "sector_keys": sector_keys,
        "signals_list": signals_list,
        "movers_json": movers_json,
        "history_json": history_json,
        "rescore_data_json": rescore_data_json,
        "scan_history_json": scan_history_json,
        "backtest_json": bt["backtest_json"],
        "backtest_metrics": bt["backtest_metrics"],
        "has_backtest": bt["has_backtest"],
        "rotation_json": bt["rotation_json"],
        "has_rotations": bt["has_rotations"],
    }


def build_themes_context(shared: dict) -> dict:
    """Assemble all figure + backtest context entries for the themes page."""
    import json as _json

    theme_rrg_json = _build_rrg_figure(shared["theme_rrg_df"])
    theme_dd, theme_keys, _ = _build_drilldown_data(shared["theme_history_df"])
    theme_movers_json = _build_movers_figure(shared["theme_history_df"])
    theme_history_json = _build_history_figure(shared["theme_history_df"])
    bt = _build_theme_backtest_context(str(shared["project_root"] / "backtests_themes"))

    return {
        "theme_rrg_json": theme_rrg_json,
        "theme_drilldown_data": _json.dumps(theme_dd),
        "theme_keys": theme_keys,
        "theme_movers_json": theme_movers_json,
        "theme_history_json": theme_history_json,
        "theme_backtest_json": bt["theme_backtest_json"],
        "theme_backtest_metrics": bt["theme_backtest_metrics"],
        "has_theme_backtest": bt["has_theme_backtest"],
    }
```

- [ ] **Step 2: Add `build_page_context` to `sentiment.py`**

Append to the end of `dashboard/sentiment.py`:

```python
def build_page_context(shared: dict) -> dict:
    """Assemble sentiment page context (both sector and theme cohorts)."""
    from dashboard.figures import _build_sentiment_scatter_figure

    return {
        "sentiment_scatter_json": _build_sentiment_scatter_figure(shared["history_df"]),
        "sentiment_signal_rows": _build_sentiment_signal_rows(shared["sentiment_signals_df"]),
        "theme_sentiment_scatter_json": _build_sentiment_scatter_figure(shared["theme_history_df"]),
        "theme_sentiment_signal_rows": _build_sentiment_signal_rows(shared["theme_sentiment_signals_df"]),
    }
```

- [ ] **Step 3: Add `build_page_context` to `badges.py`**

Append to the end of `dashboard/badges.py`:

```python
def build_page_context(shared: dict) -> dict:
    """Assemble badge scorecard context for the sectors page."""
    return {
        "badge_scorecard": build_badge_scorecard(
            shared["all_scores_df"],
            shared["universe"],
            price_cache_dir=str(shared["project_root"] / "data/cache"),
        ),
    }
```

- [ ] **Step 4: Add `build_page_context` to `macro.py`**

Append to the end of `dashboard/macro.py`:

```python
def build_page_context(shared: dict) -> dict:
    """Assemble macro regime context (used by all pages)."""
    return {
        "macro": fetch_macro_data(
            cache_dir=str(shared["project_root"] / "data" / "cache"),
        ),
    }
```

- [ ] **Step 5: Rewrite `build.py` main() to use shared dict + .update()**

Replace `dashboard/build.py` with the refactored version below. Key changes:
1. Import block is alphabetized and trimmed (module internals no longer imported directly — context builders handle them).
2. A `shared` dict is assembled after DB reads + config loading.
3. Each page's context is built via `.update()` calls — one per contributing module.
4. The leaderboard enrichment loop and feed render stay inline (complex, stable, page-specific).

```python
"""
Static dashboard builder.

Reads Supabase/Postgres -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--out docs]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dashboard.build")

# Ensure project root is on sys.path so absolute imports work
# whether invoked as `python dashboard/build.py` or `python -m dashboard.build`
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# Re-export public API so existing imports keep working (alphabetized)
from dashboard.badges import (                      # noqa: E402, F401
    build_badge_scorecard,
    build_page_context as _badges_ctx,
)
from dashboard.breakdown import (                   # noqa: E402, F401
    _build_breakdown_html,
    _build_instruments_html,
    _SIGNAL_DESCRIPTIONS,
    _SIGNAL_META,
)
from dashboard.feed import (                        # noqa: E402, F401
    build_feed_entries,
    feed_updated_timestamp,
)
from dashboard.figures import (                     # noqa: E402, F401
    build_sectors_context as _figures_sectors_ctx,
    build_themes_context as _figures_themes_ctx,
    _build_rrg_figure,
    _build_sentiment_scatter_figure,
    _build_drilldown_data,
    _build_movers_figure,
    _build_history_figure,
    _build_backtest_figures,
    _build_rotation_figures,
    _build_backtest_context,
    _build_theme_backtest_context,
    _build_rescore_data,
    _build_scan_history_data,
    _SCORE_SIGNAL_COLORS,
    _WARM_PALETTE,
)
from dashboard.macro import (                       # noqa: E402, F401
    build_macro_context,
    build_page_context as _macro_ctx,
    fetch_macro_data,
)
from dashboard.reports import (                     # noqa: E402, F401
    build_scan_index,
    _generate_scan_reports,
)
from dashboard.rows import (                        # noqa: E402, F401
    _build_leaderboard_rows,
    _build_theme_leaderboard_rows,
    _compute_rank_trajectories,
    _compute_setup,
    _format_raw_value,
    _safe_float,
)
from dashboard.sentiment import (                   # noqa: E402, F401
    _build_sentiment_signal_rows,
    build_page_context as _sentiment_ctx,
)


# ---------------------------------------------------------------------------
# Plotly bundle management
# ---------------------------------------------------------------------------

PLOTLY_CDN = "https://cdn.plot.ly/plotly-basic-2.27.0.min.js"
_ASSETS_DIR = Path(__file__).parent / "assets"


def _ensure_plotly_bundle() -> Path:
    """Download plotly.min.js once to dashboard/assets/ if not present."""
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bundle = _ASSETS_DIR / "plotly.min.js"
    if not bundle.exists():
        import requests

        logger.info("Downloading Plotly bundle from %s …", PLOTLY_CDN)
        try:
            resp = requests.get(PLOTLY_CDN, timeout=30)
            resp.raise_for_status()
            bundle.write_bytes(resp.content)
            logger.info("Downloaded plotly bundle (%d KB)", len(resp.content) // 1024)
        except Exception as exc:
            logger.error(
                "Failed to download Plotly bundle from %s: %s\n"
                "Fix: manually download plotly.min.js from https://cdn.plot.ly/ "
                "and place it at dashboard/assets/plotly.min.js",
                PLOTLY_CDN, exc
            )
            sys.exit(1)
    return bundle


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _disable_jekyll(out_dir: Path) -> Path:
    """Write an empty ``.nojekyll`` so GitHub Pages serves the site as-is."""
    out_dir.mkdir(parents=True, exist_ok=True)
    nojekyll = out_dir / ".nojekyll"
    nojekyll.write_text("", encoding="utf-8")
    return nojekyll


def _render(
    template_path: Path,
    out_path: Path,
    context: dict,
) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )

    def js_json_filter(value):
        """Escape </ sequences in JSON for safe embedding in <script> tags."""
        if isinstance(value, str):
            return value.replace("</", r"<\/")
        return value
    env.filters["js_json"] = js_json_filter

    template = env.get_template(template_path.name)
    html = template.render(**context)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s (%d KB)", out_path, len(html) // 1024)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static dashboard from Supabase")
    parser.add_argument("--out", default="docs", metavar="DIR",
                        help="Output directory for docs/index.html (default: docs)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve paths relative to project root (parent of dashboard/)
    project_root = Path(__file__).parent.parent
    out_dir = project_root / args.out

    # 1. Ensure plotly bundle
    _ensure_plotly_bundle()

    # 2. Open DB + load history
    sys.path.insert(0, str(project_root))
    from src.state import (
        init_db, get_scan_history, get_signals_for_latest_scan, get_rrg_history,
        get_sentiment_signals_for_latest_scan,
        get_theme_signals_for_latest_scan, get_theme_scan_history,
        get_theme_rrg_history, get_theme_sentiment_signals_for_latest_scan,
    )

    conn = init_db()
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)
    sentiment_signals_df = get_sentiment_signals_for_latest_scan(conn)
    theme_signals_df = get_theme_signals_for_latest_scan(conn)
    theme_sentiment_signals_df = get_theme_sentiment_signals_for_latest_scan(conn)
    theme_history_df = get_theme_scan_history(conn)
    rrg_df = get_rrg_history(conn, n_scans=6)
    theme_rrg_df = get_theme_rrg_history(conn, n_scans=6)

    logger.info("Building scan index + per-scan reports …")
    all_scores_df = get_scan_history(conn, n_scans=None)
    scan_index = build_scan_index(all_scores_df)
    active_scan_id = scan_index[0]["scan_id"] if scan_index else None
    _generate_scan_reports(all_scores_df, out_dir / "reports")

    conn.close()

    if history_df.empty:
        print("No scans in database yet. Run scan.py first.")
        sys.exit(0)

    logger.info("Loaded %d rows from %d scans", len(history_df), history_df["scan_id"].nunique())

    # Load config for breakdown panel
    import yaml as _yaml
    with open(project_root / "config/universe.yaml") as _fh:
        _universe = _yaml.safe_load(_fh)
    with open(project_root / "config/weights.yaml") as _fh:
        _weights = _yaml.safe_load(_fh)
    _etfs_path = project_root / "config/sector_etfs.yaml"
    _sector_etfs = _yaml.safe_load(_etfs_path.read_text()) if _etfs_path.exists() else {}

    _themes_path = project_root / "config/themes.yaml"
    _themes_cfg = _yaml.safe_load(_themes_path.read_text()) if _themes_path.exists() else {}

    # ------------------------------------------------------------------
    # Shared dependencies for module context builders
    # ------------------------------------------------------------------
    shared = {
        "project_root": project_root,
        "all_scores_df": all_scores_df,
        "history_df": history_df,
        "theme_history_df": theme_history_df,
        "rrg_df": rrg_df,
        "theme_rrg_df": theme_rrg_df,
        "universe": _universe,
        "sentiment_signals_df": sentiment_signals_df,
        "theme_sentiment_signals_df": theme_sentiment_signals_df,
    }

    # ------------------------------------------------------------------
    # Page-specific context that stays in build.py (complex, stable)
    # ------------------------------------------------------------------

    # Themes leaderboard rows
    theme_trajectories = _compute_rank_trajectories(theme_history_df)
    theme_rows = _build_theme_leaderboard_rows(
        theme_history_df, theme_signals_df, _themes_cfg, _weights, theme_trajectories,
    )

    # Leaderboard rows + enrichment
    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(history_df)
    trajectories = _compute_rank_trajectories(history_df)

    latest_scan_id = history_df["scan_id"].max()
    latest_scores  = history_df[history_df["scan_id"] == latest_scan_id]
    for row in leaderboard_rows:
        key = f"{row['region']}|{row['sector']}"
        row["key"]       = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        row["trajectory_label"] = traj["label"]
        row["trajectory_state"] = traj["state"]
        _compute_setup(row)
        mask = (
            (latest_scores["region"]      == row["region"]) &
            (latest_scores["gics_sector"] == row["sector"])
        )
        score_slice = latest_scores[mask]
        score_row_dict = {} if score_slice.empty else score_slice.iloc[0].to_dict()
        if not signals_df.empty:
            sig_mask = (
                (signals_df["region"]      == row["region"]) &
                (signals_df["gics_sector"] == row["sector"])
            )
            row_signals = signals_df[sig_mask].to_dict("records")
        else:
            row_signals = []
        row["breakdown_html"] = _build_breakdown_html(
            key, score_row_dict, row_signals, _universe, _weights, _sector_etfs
        )

    # 4. Copy plotly.min.js into docs/assets/ so GitHub Pages can serve it
    import shutil
    docs_assets = out_dir / "assets"
    docs_assets.mkdir(exist_ok=True)
    plotly_src = _ASSETS_DIR / "plotly.min.js"
    if plotly_src.exists():
        shutil.copy2(plotly_src, docs_assets / "plotly.min.js")
    rescore_src = _ASSETS_DIR / "rescore.js"
    if rescore_src.exists():
        shutil.copy2(rescore_src, docs_assets / "rescore.js")
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
    scan_digest_src = _ASSETS_DIR / "scan-digest.js"
    if scan_digest_src.exists():
        shutil.copy2(scan_digest_src, docs_assets / "scan-digest.js")
    plotly_bundle_rel = "assets/plotly.min.js"

    # ------------------------------------------------------------------
    # 5. Assemble + render pages via module context builders
    # ------------------------------------------------------------------
    template_dir = Path(__file__).parent / "templates"

    # Compute cross-page contexts once (macro makes a network call)
    logger.info("Fetching macro regime data …")
    macro_page_ctx = _macro_ctx(shared)

    # --- Sectors page ---
    logger.info("Building sectors page context …")
    sectors_ctx = {
        "scan_date": scan_date,
        "scan_index": scan_index,
        "active_scan_id": active_scan_id,
        "leaderboard_rows": leaderboard_rows,
        "plotly_bundle": plotly_bundle_rel,
    }
    sectors_ctx.update(_figures_sectors_ctx(shared))
    sectors_ctx.update(_badges_ctx(shared))
    sectors_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "index.html.j2",
        out_path=out_dir / "index.html",
        context=sectors_ctx,
    )

    # --- Sentiment page ---
    logger.info("Building sentiment page context …")
    sentiment_ctx = {
        "scan_date": scan_date,
        "active_scan_id": active_scan_id,
        "plotly_bundle": plotly_bundle_rel,
    }
    sentiment_ctx.update(_sentiment_ctx(shared))
    sentiment_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "sentiment.html.j2",
        out_path=out_dir / "sentiment.html",
        context=sentiment_ctx,
    )

    # --- Themes page ---
    logger.info("Building themes page context …")
    themes_ctx = {
        "scan_date": scan_date,
        "active_scan_id": active_scan_id,
        "theme_rows": theme_rows,
        "plotly_bundle": plotly_bundle_rel,
    }
    themes_ctx.update(_figures_themes_ctx(shared))
    themes_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "themes.html.j2",
        out_path=out_dir / "themes.html",
        context=themes_ctx,
    )

    # 6. Atom feed
    logger.info("Building Atom feed …")
    feed_entries = build_feed_entries(all_scores_df, n_entries=30)
    dashboard_url = "https://jbarte.github.io/sector_momentum/"
    feed_url = dashboard_url + "feed.xml"

    from jinja2 import Environment, FileSystemLoader
    feed_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    feed_template = feed_env.get_template("feed.xml.j2")
    feed_xml = feed_template.render(
        entries=feed_entries,
        feed_updated=feed_updated_timestamp(feed_entries),
        dashboard_url=dashboard_url,
        feed_url=feed_url,
    )
    feed_path = out_dir / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")
    logger.info("Feed written to %s (%d entries)", feed_path, len(feed_entries))

    # 7. Disable Jekyll on GitHub Pages (the published artifact is static).
    _disable_jekyll(out_dir)

    print(f"Dashboard built: {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests**

```bash
pytest -v
```

All existing tests must pass. No new tests are needed — this is a pure
restructuring with identical outputs.

- [ ] **Step 7: Build and verify output**

```bash
python3 dashboard/build.py
```

Build must complete without errors. The rendered HTML pages are identical
in behavior to the pre-refactor output.

- [ ] **Step 8: Commit**

```bash
git add dashboard/build.py dashboard/figures.py dashboard/sentiment.py \
       dashboard/badges.py dashboard/macro.py
git commit -m "refactor: add module context builders, restructure build.py assembly"
```

- [ ] **Step 9: Push and open PR**

```bash
git push -u origin refactor/hub-file-split
gh pr create --title "refactor: split hub files to eliminate merge conflicts" --body "$(cat <<'EOF'
## Summary
- Split `_style.html.j2` (755 lines) into 6 per-component CSS partials under `css/`
- Split `_i18n.html.j2` into 6 per-module translation partials under `i18n/`
- Added `build_page_context()` to `badges`, `macro`, `sentiment`, and `figures` modules
- Refactored `build.py` to assemble page context via `.update()` calls
- Alphabetized import block in `build.py`

No user-visible change — identical dashboard output. Future features add files
instead of editing shared ones, eliminating the merge-conflict hotspots that
required manual resolution on every recent PR (#89–#95).

## Test plan
- [ ] `python3 dashboard/build.py` succeeds
- [ ] `pytest` passes (all existing tests)
- [ ] Dashboard renders correctly in browser (sectors, sentiment, themes pages)
- [ ] EN⇄SV toggle works on all pages

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
