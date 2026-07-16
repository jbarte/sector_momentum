# Dashboard UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress the dashboard chrome from 7 layers to 3 (command bar → card tab strip → digest line) and lift content onto white card surfaces over a deeper cream canvas.

**Architecture:** Rewrite `_header.html.j2` as a single-row command bar with inline macro chips. Wrap each page's tab strip + panels in a `.card` container. Delete `_guide_modal.html.j2`, `_macro_bar.html.j2`, Guide tabs, and their CSS/i18n keys. Create `_footer.html.j2` with disclaimer + RSS link. Restyle `.tab-guide` as a compact `ⓘ How to read` link and move the sentiment control into a `<details>` popover.

**Tech Stack:** Jinja2 templates, vanilla CSS, vanilla JS (no new dependencies)

## Global Constraints

- `docs/` is a CI-generated artifact — do NOT `git add docs/` on a feature branch.
- No information-architecture changes (3 pages, same tabs within each page).
- No changes to scan/scoring/build data logic, `dashboard/build.py` Python logic, reports pages, or `feed.xsl`.
- No dark mode.
- No Plotly figure or table-column content changes.
- Element IDs `#sentiment-toggle`, `#sentiment-weight`, `#scan-digest-banner`, `digest-chips-entries`, `digest-chips-up`, `digest-chips-down` must be preserved so `rescore.js`, `scan-digest.js`, `scan-history.js` keep working.
- `_tabs.js.j2` logic is untouched.
- CSS custom property names for the existing warm palette (`--beige-*`, `--green-*`, `--terra-*`, `--up`, `--down`, `--font-*`, etc.) are preserved.
- `data-i18n` / `data-i18n-html` i18n pattern stays; add `data-i18n-title` support.
- All SV translations must be updated for new/changed keys and deleted for removed keys.
- Per-tab accordion guide content (`guide_body_*` keys) stays intact (text updated only for the macro-bar wording change).
- Responsive target: command bar wraps to ≤ 2 rows at 375 px; cards go full-width.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `dashboard/templates/_header.html.j2` | **Rewrite** | Command bar: h1, segment nav, macro chips, scan meta, lang toggle |
| `dashboard/templates/_footer.html.j2` | **Create** | Site footer: disclaimer, RSS link |
| `dashboard/templates/_macro_bar.html.j2` | **Delete** | Superseded by command bar chips |
| `dashboard/templates/_guide_modal.html.j2` | **Delete** | Superseded by per-tab accordions |
| `dashboard/templates/_style.html.j2` | **Modify** | Add new tokens/components, prune dead styles |
| `dashboard/templates/_i18n.html.j2` | **Modify** | Add `data-i18n-title`, new keys, delete guide-modal/guide-tab keys, update macro wording |
| `dashboard/templates/index.html.j2` | **Modify** | Wire card shell, remove macro-bar/guide-modal includes, remove Guide tab, restructure leaderboard (digest + rank-settings popover + utility row) |
| `dashboard/templates/themes.html.j2` | **Modify** | Wire card shell, remove macro-bar/guide-modal includes, remove Guide tab |
| `dashboard/templates/sentiment.html.j2` | **Modify** | Wire card shell, remove macro-bar/guide-modal include |

---

### Task 1: CSS foundation — new tokens, card shell, command bar, footer, chip styles

**Files:**
- Modify: `dashboard/templates/_style.html.j2`

**Interfaces:**
- Produces: CSS classes `.command-bar`, `.command-bar h1`, `.command-bar .meta-cluster`, `.chip`, `.chip-up`, `.chip-down`, `.chip-calm`, `.chip-elevated`, `.chip-stressed`, `.card`, `.card .tabs`, `.tab-guide` (restyled compact variant), `.digest-collapsed`, `.digest-toggle`, `.rank-settings`, `.site-footer`, `.utility-row`
- Produces: CSS tokens `--canvas`, `--surface`, `--shadow-card`

- [ ] **Step 1: Add new CSS custom properties**

In `_style.html.j2`, add three new tokens inside the `:root` block, after `--shadow-sm`:

```css
--canvas:      #f1ecdf;
--surface:     #ffffff;
--shadow-card: 0 1px 5px rgba(60,55,40,0.10);
```

Change the `--bg` value from `var(--beige-100)` to `var(--canvas)` so the page background becomes the deeper cream.

- [ ] **Step 2: Replace header styles with command bar styles**

Delete the entire `/* Header */` block (lines ~67–112 of the current file: `header { … }` through `.lang-toggle:hover { … }`). Replace with:

```css
/* =========================================================
   Command bar
   ========================================================= */
.command-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 28px;
  flex-wrap: wrap;
}
.command-bar h1 {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--fg1);
  letter-spacing: -0.02em;
  line-height: 1;
  white-space: nowrap;
}
.meta-cluster {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.scan-meta {
  font-size: 11px;
  color: var(--fg4);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.lang-toggle {
  font-family: var(--font-body);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--fg3);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  padding: 3px 12px;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-standard),
              border-color var(--duration-fast) var(--ease-standard);
}
.lang-toggle:hover { color: var(--brand-strong); border-color: var(--brand-strong); }
```

- [ ] **Step 3: Add chip styles**

After the command bar block, add:

```css
/* =========================================================
   Chips (macro indicators in command bar)
   ========================================================= */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: default;
  font-variant-numeric: tabular-nums;
}
.chip-up   { background: color-mix(in srgb, var(--up) 12%, transparent); color: var(--up); }
.chip-down { background: color-mix(in srgb, var(--down) 12%, transparent); color: var(--down); }
.chip-calm      { background: color-mix(in srgb, var(--up) 12%, transparent); color: var(--up); }
.chip-elevated  { background: color-mix(in srgb, var(--fg1) 8%, transparent); color: var(--fg1); }
.chip-stressed  { background: color-mix(in srgb, var(--down) 12%, transparent); color: var(--down); }
```

- [ ] **Step 4: Add card shell styles**

After the chip styles, add:

```css
/* =========================================================
   Card shell
   ========================================================= */
.card {
  background: var(--surface);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
  margin: 16px 28px 24px;
  overflow: hidden;
}
.card > .tabs {
  background: var(--surface);
  border-bottom: 1px solid var(--border-soft);
  padding: 0 20px;
  border-radius: 10px 10px 0 0;
}
.card > .tab-panel { padding: 20px 24px; }
```

- [ ] **Step 5: Restyle tab-guide as compact info link**

Replace the existing `.tab-guide` block (lines ~387–434 currently) with:

```css
/* Tab interpretation guides (compact ⓘ variant) */
.utility-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  padding: 6px 0 0;
}
.tab-guide {
  border: none;
  background: none;
  margin: 0;
  padding: 0;
  font-size: 0.8rem;
}
.tab-guide summary {
  padding: 0;
  cursor: pointer;
  color: var(--fg4);
  font-weight: 500;
  font-size: 12px;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}
.tab-guide summary::before {
  content: "ⓘ";
  font-size: 1em;
}
.tab-guide summary:hover { color: var(--fg2); }
.tab-guide[open] summary { color: var(--fg2); }
.tab-guide[open] summary::before {
  content: "ⓘ";
}
.tab-guide-body {
  padding: 12px 0 8px;
  color: var(--fg2);
  line-height: 1.65;
  font-size: 0.875rem;
}
.tab-guide-body h4 {
  margin: 12px 0 4px;
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fg4);
}
.tab-guide-body ul {
  margin: 4px 0 0 0;
  padding-left: 18px;
}
.tab-guide-body li { margin-bottom: 3px; }
.tab-guide-body .signal-hi { color: var(--up); font-weight: 600; }
.tab-guide-body .signal-lo { color: var(--down); font-weight: 600; }
```

- [ ] **Step 6: Add rank-settings popover styles**

After the tab-guide styles:

```css
/* =========================================================
   Rank settings popover (sentiment control)
   ========================================================= */
.rank-settings {
  position: relative;
  border: none;
  background: none;
  margin: 0;
  padding: 0;
}
.rank-settings summary {
  cursor: pointer;
  color: var(--fg4);
  font-size: 12px;
  font-weight: 500;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}
.rank-settings summary:hover { color: var(--fg2); }
.rank-settings .sentiment-control {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 12px 16px;
  z-index: 100;
  white-space: nowrap;
  min-width: 260px;
}
```

- [ ] **Step 7: Add digest-collapsed styles**

After `#scan-digest-banner` styles, add:

```css
.digest-collapsed .digest-cluster[data-cluster="up"],
.digest-collapsed .digest-cluster[data-cluster="down"] { display: none; }
.digest-toggle {
  cursor: pointer;
  color: var(--fg4);
  font-size: 12px;
  margin-left: 4px;
  user-select: none;
}
.digest-toggle:hover { color: var(--fg2); }
```

- [ ] **Step 8: Add site-footer styles**

At the end of the file (before the closing comment or after the last responsive block):

```css
/* =========================================================
   Site footer
   ========================================================= */
.site-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 28px;
  margin-top: 8px;
  border-top: 1px solid var(--border-soft);
  font-size: 11px;
  color: var(--fg4);
}
.site-footer a {
  color: var(--fg3);
  text-decoration: none;
}
.site-footer a:hover { color: var(--brand-strong); }
```

- [ ] **Step 9: Delete dead CSS blocks**

Remove the following style blocks entirely:
- The `/* Macro regime bar */` block (~lines 117–128): `.macro-bar`, `.macro-item`, `.macro-label`, `.macro-value`, `.macro-sub`, `.macro-sep`, `.macro-up`, `.macro-down`, `.macro-band`, `.macro-band-calm`, `.macro-band-elevated`, `.macro-band-stressed`.
- The `/* Guide modal overlay */` block (~lines 643–868): everything from `.guide-open-btn` through `.guide-hint kbd` and the `prefers-reduced-motion` rule.
- The `/* Guide panel */` block (~lines 438–456): `.guide`, `.guide h2`, `.guide h2:first-child`, `.guide p`, `.guide ul`, `.guide li`, `.guide strong`.
- The old `.disclaimer` styles (inside the old header block — already removed in step 2).

- [ ] **Step 10: Update responsive rule**

Replace the `@media (max-width: 600px)` block:

```css
@media (max-width: 600px) {
  .command-bar { padding: 10px 16px; }
  .card { margin: 12px 12px 16px; }
  .card > .tab-panel { padding: 14px 16px; }
  .card > .tabs { padding: 0 8px; }
  .site-footer { padding: 14px 16px; }
}
@media (max-width: 420px) {
  .scan-meta { display: none; }
}
```

- [ ] **Step 11: Verify by running the dashboard build**

Run: `python3 dashboard/build.py`

Expected: builds clean with no errors. The CSS changes are structural; template changes in later tasks will exercise them.

- [ ] **Step 12: Commit**

```bash
git add dashboard/templates/_style.html.j2
git commit -m "refactor: CSS foundation for dashboard redesign

Add --canvas/--surface/--shadow-card tokens, .command-bar, .chip,
.card, .rank-settings, .site-footer components. Delete guide-modal,
macro-bar, old header, and guide-panel styles.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Command bar + footer templates, delete macro bar

**Files:**
- Rewrite: `dashboard/templates/_header.html.j2`
- Create: `dashboard/templates/_footer.html.j2`
- Delete: `dashboard/templates/_macro_bar.html.j2`

**Interfaces:**
- Consumes: Template context variables `active_segment` (str), `active_scan_id` (int|None), `scan_date` (str), `macro` (object|None with `.spy_above`, `.spy_distance_pct`, `.vix_last`, `.vix_band`)
- Produces: `_header.html.j2` — renders the `.command-bar` with segment nav, macro chips, scan meta, lang toggle
- Produces: `_footer.html.j2` — renders `.site-footer` with disclaimer text and RSS link

- [ ] **Step 1: Rewrite _header.html.j2**

Replace the entire file with:

```jinja2
<div class="command-bar">
  <h1 data-i18n="title">Sector momentum</h1>
  <div class="segment-toggle" role="tablist" aria-label="Dashboard segment">
    {% if active_segment == "sectors" %}
    <span class="segment-btn active" aria-current="page" data-i18n="segment_sectors">Sectors</span>
    {% else %}
    <a class="segment-btn" href="index.html" data-i18n="segment_sectors">Sectors</a>
    {% endif %}
    {% if active_segment == "themes" %}
    <span class="segment-btn active" aria-current="page" data-i18n="segment_themes">Themes</span>
    {% else %}
    <a class="segment-btn" href="themes.html" data-i18n="segment_themes">Themes</a>
    {% endif %}
    {% if active_segment == "sentiment" %}
    <span class="segment-btn active" aria-current="page" data-i18n="segment_sentiment">Sentiment</span>
    {% else %}
    <a class="segment-btn" href="sentiment.html" data-i18n="segment_sentiment">Sentiment</a>
    {% endif %}
  </div>
  <div class="meta-cluster">
    {% if macro %}
    <span class="chip {{ 'chip-up' if macro.spy_above else 'chip-down' }}"
          title="vs 200-DMA — {{ 'above' if macro.spy_above else 'below' }}"
          data-i18n-title="{{ 'macro_chip_spy_above' if macro.spy_above else 'macro_chip_spy_below' }}">SPY {{ "%+.1f"|format(macro.spy_distance_pct) }}%</span>
    <span class="chip chip-{{ macro.vix_band|lower }}"
          title="{{ macro.vix_band }}"
          data-i18n-title="macro_vix_{{ macro.vix_band|lower }}">VIX {{ "%.1f"|format(macro.vix_last) }}</span>
    {% endif %}
    <span class="scan-meta">{% if active_scan_id %}#{{ active_scan_id }} · {% endif %}{{ scan_date }}</span>
    <button id="lang-toggle" class="lang-toggle" type="button" onclick="toggleLang()" aria-label="Switch language">SV</button>
  </div>
</div>
```

- [ ] **Step 2: Create _footer.html.j2**

Create the file with:

```jinja2
<footer class="site-footer">
  <span data-i18n="disclaimer">Analytical tooling, not investment advice.</span>
  <a href="feed.xml">RSS</a>
</footer>
```

- [ ] **Step 3: Delete _macro_bar.html.j2**

```bash
git rm dashboard/templates/_macro_bar.html.j2
```

- [ ] **Step 4: Verify the build still works**

Run: `python3 dashboard/build.py`

Expected: build succeeds. The templates still include `_macro_bar.html.j2` in the page files — this will break the build. We need to update the page files first. So defer verification to after Task 3.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/_header.html.j2 dashboard/templates/_footer.html.j2
git commit -m "feat: rewrite header as command bar, add footer, delete macro bar

Command bar: single flex row with h1, segment nav, inline SPY/VIX
chips (with data-i18n-title tooltips), scan meta, lang toggle.
Footer: disclaimer text + RSS link.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Restructure index.html.j2 — card shell, guide tab removal, digest collapse, rank-settings popover

**Files:**
- Modify: `dashboard/templates/index.html.j2`

**Interfaces:**
- Consumes: `.card`, `.command-bar`, `.utility-row`, `.rank-settings`, `.digest-collapsed`, `.digest-toggle`, `.site-footer` CSS classes (Task 1)
- Consumes: `_header.html.j2` (Task 2), `_footer.html.j2` (Task 2)
- Preserves: element IDs `#sentiment-toggle`, `#sentiment-weight`, `#scan-digest-banner`, `digest-chips-entries`, `digest-chips-up`, `digest-chips-down`, `#leaderboard-table`

- [ ] **Step 1: Remove macro-bar and guide-modal includes**

In `index.html.j2`, delete:
- Line `{% include '_macro_bar.html.j2' %}` (line ~19)
- Line `{% include "_guide_modal.html.j2" %}` (line ~667)

- [ ] **Step 2: Add footer include**

Before `</body>`, add:
```jinja2
{% include '_footer.html.j2' %}
```

- [ ] **Step 3: Wrap the tab bar + tab panels in a card div**

Insert `<div class="card">` right before the `<!-- Tab bar -->` comment (line ~21 area), and close `</div>` after the last `</section>` for tab-guide (line ~379 area), but before the `<!-- JavaScript -->` comment.

- [ ] **Step 4: Remove the Guide tab button and Guide tab panel**

Delete the Guide tab button from the `<nav class="tabs">`:
```html
  <button class="tab-btn" onclick="switchTab('guide', this)" role="tab" aria-selected="false" aria-controls="tab-guide" data-i18n="tab_guide">Guide</button>
```

Delete the entire `<!-- Tab: Guide -->` section (lines ~284–379): `<section id="tab-guide" ...>` through its closing `</section>`.

- [ ] **Step 5: Add utility row to leaderboard tab**

Inside `<section id="tab-leaderboard">`, before the `<details class="tab-guide">`, insert:

```html
  <div class="utility-row">
    <details class="rank-settings">
      <summary>⚙</summary>
      <div id="sentiment-control" class="sentiment-control">
        <label>
          <input type="checkbox" id="sentiment-toggle"> <span data-i18n="includeSentiment">Include sentiment in ranking</span>
        </label>
        <span class="sw-weight">
          <span data-i18n="weight">Weight:</span> <input type="number" id="sentiment-weight" min="0" max="100" step="1" value="30" disabled>%
        </span>
      </div>
    </details>
    <details class="tab-guide">
      <summary data-i18n="guide_summary">How to read this tab</summary>
      <div class="tab-guide-body" data-i18n-html="guide_body_leaderboard">
```

Wait — the tab-guide `<details>` already exists. The structure should be: add the utility row wrapping both the rank-settings popover and the existing tab-guide.

Revised approach: restructure the leaderboard section as follows (inside `<section id="tab-leaderboard">`):

```html
  <div class="utility-row">
    <details class="rank-settings">
      <summary>⚙</summary>
      <div id="sentiment-control" class="sentiment-control">
        <label>
          <input type="checkbox" id="sentiment-toggle"> <span data-i18n="includeSentiment">Include sentiment in ranking</span>
        </label>
        <span class="sw-weight">
          <span data-i18n="weight">Weight:</span> <input type="number" id="sentiment-weight" min="0" max="100" step="1" value="30" disabled>%
        </span>
      </div>
    </details>
    <details class="tab-guide">
      <summary data-i18n="guide_summary">How to read this tab</summary>
      <div class="tab-guide-body" data-i18n-html="guide_body_leaderboard">
        <!-- existing guide content unchanged -->
      </div>
    </details>
  </div>
```

Remove the old standalone `<details class="tab-guide">` and the old standalone `<div id="sentiment-control" ...>` from the leaderboard section (they are now inside the utility row).

- [ ] **Step 6: Add digest collapse toggle**

Modify the `#scan-digest-banner` div:

Add `class="digest-collapsed"` to the banner div:
```html
<div id="scan-digest-banner" class="digest-collapsed" style="display:none">
```

After the `digest-cluster[data-cluster="entries"]` span, add the toggle:
```html
    <span class="digest-toggle" onclick="this.closest('#scan-digest-banner').classList.toggle('digest-collapsed'); this.textContent = this.textContent === 'more ▾' ? 'less ▴' : 'more ▾'">more ▾</span>
```

- [ ] **Step 7: Update the macro section wording in the leaderboard guide**

In the `guide_body_leaderboard` HTML content, change:
- "The thin strip above the leaderboard shows two broad-market indicators" → "The SPY and VIX chips in the header show two broad-market indicators"
- Delete the paragraph "The bar is **info-only**..." and replace with: "The chips are **info-only** — they do not affect sector scores or rankings. Use them as context: the same sector ranking can mean different things depending on whether the broad market is in a healthy uptrend with low volatility or a stressed, risk-off environment."
- Change the heading from "Macro regime bar" → "Macro regime chips"

- [ ] **Step 8: Verify the build**

Run: `python3 dashboard/build.py`
Expected: builds clean.

- [ ] **Step 9: Commit**

```bash
git add dashboard/templates/index.html.j2
git commit -m "feat: restructure sectors page with card shell, utility row, digest collapse

- Wrap tabs + panels in .card container
- Delete Guide tab (accordion-only help system)
- Move sentiment control into <details> rank-settings popover
- Add utility row with ⚙ + ⓘ links
- Collapsible digest line with 'more ▾' toggle
- Remove macro-bar and guide-modal includes
- Add footer include
- Update macro guide text (strip → chips)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Restructure themes.html.j2 — card shell, guide tab removal

**Files:**
- Modify: `dashboard/templates/themes.html.j2`

**Interfaces:**
- Consumes: `.card`, `.utility-row`, `.site-footer` CSS classes (Task 1)
- Consumes: `_header.html.j2` (Task 2), `_footer.html.j2` (Task 2)

- [ ] **Step 1: Remove macro-bar and guide-modal includes**

Delete:
- `{% include '_macro_bar.html.j2' %}`
- `{% include "_guide_modal.html.j2" %}`

- [ ] **Step 2: Add footer include**

Before `</body>`:
```jinja2
{% include '_footer.html.j2' %}
```

- [ ] **Step 3: Wrap tabs + panels in card div**

Insert `<div class="card">` before `<!-- Tab bar -->`.
Close `</div>` after the last tab-panel section (before `<!-- JavaScript -->`).

- [ ] **Step 4: Remove Guide tab button and Guide tab panel**

Delete the Guide tab button:
```html
  <button class="tab-btn" onclick="switchTab('guide', this)" role="tab" aria-selected="false" aria-controls="tab-guide" data-i18n="tab_guide">Guide</button>
```

Delete the entire `<!-- Tab: Guide -->` section (lines ~189–255).

- [ ] **Step 5: Add utility row to leaderboard tab**

Wrap the existing leaderboard tab-guide `<details>` in a utility row. The themes leaderboard has no sentiment control, so it's just the info link:

At the top of `<section id="tab-leaderboard">`, after the `<p class="tab-note"...>` element, replace the standalone `<details class="tab-guide">` (if one exists on the leaderboard — currently the themes leaderboard has no tab-guide, only a tab-note). So just add a utility row after the note if there's no guide:

Actually, looking at the themes leaderboard section (lines 32–76), it has no `<details class="tab-guide">` — it only has a `<p class="tab-note">`. The utility row is optional here. Skip it for the themes leaderboard.

For the other theme tabs that DO have `<details class="tab-guide">` (RRG, Drill-down, Movers, History), wrap each in a utility row:

```html
  <div class="utility-row">
    <details class="tab-guide">
      <!-- existing content unchanged -->
    </details>
  </div>
```

- [ ] **Step 6: Verify**

Run: `python3 dashboard/build.py`
Expected: builds clean.

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/themes.html.j2
git commit -m "feat: restructure themes page with card shell, remove Guide tab

- Wrap tabs + panels in .card container
- Delete Guide tab (accordion-only help system)
- Remove macro-bar and guide-modal includes
- Add footer include
- Wrap tab-guides in utility rows

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Restructure sentiment.html.j2 — card shell

**Files:**
- Modify: `dashboard/templates/sentiment.html.j2`

**Interfaces:**
- Consumes: `.card`, `.site-footer` CSS classes (Task 1)
- Consumes: `_header.html.j2` (Task 2), `_footer.html.j2` (Task 2)

- [ ] **Step 1: Remove macro-bar and guide-modal includes**

Delete:
- `{% include '_macro_bar.html.j2' %}`
- `{% include "_guide_modal.html.j2" %}`

- [ ] **Step 2: Add footer include**

Before `</body>`:
```jinja2
{% include '_footer.html.j2' %}
```

- [ ] **Step 3: Wrap the main section in a card div**

The sentiment page has no tab bar — it has one `<section class="tab-panel active">` with a cohort toggle inside. Wrap the section content in a `.card`:

Insert `<div class="card">` before `<section class="tab-panel active">`.
Close `</div>` after the closing `</section>`.

- [ ] **Step 4: Wrap tab-guide in utility row**

The sentiment page has a single `<details class="tab-guide">` inside the section. Wrap it:

```html
<div class="utility-row">
  <details class="tab-guide">
    <!-- existing content unchanged -->
  </details>
</div>
```

- [ ] **Step 5: Verify**

Run: `python3 dashboard/build.py`
Expected: builds clean.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/sentiment.html.j2
git commit -m "feat: restructure sentiment page with card shell

- Wrap content in .card container
- Remove macro-bar and guide-modal includes
- Add footer include
- Wrap tab-guide in utility row

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: i18n updates — new keys, deletions, data-i18n-title support, macro wording

**Files:**
- Modify: `dashboard/templates/_i18n.html.j2`

**Interfaces:**
- Consumes: `data-i18n-title` attributes added to macro chips in `_header.html.j2` (Task 2)
- Produces: `data-i18n-title` support in the `apply()` function; updated SV keys

- [ ] **Step 1: Add data-i18n-title support to the apply() function**

In `_i18n.html.j2`, in the `apply(lang)` function, after the `data-i18n-html` block (after the closing `});` on line ~339), add:

```javascript
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      if (!el.getAttribute("data-en-title")) el.setAttribute("data-en-title", el.getAttribute("title") || "");
      var key = el.getAttribute("data-i18n-title");
      el.setAttribute("title", (lang === "sv" && SV[key] != null) ? SV[key] : el.getAttribute("data-en-title"));
    });
```

- [ ] **Step 2: Add new SV keys for chip tooltips**

In the `SV` object, add:

```javascript
    macro_chip_spy_above: "mot 200-DMA — över",
    macro_chip_spy_below: "mot 200-DMA — under",
```

- [ ] **Step 3: Delete Guide-modal SV keys**

Remove all `guide_modal_*` keys from the `SV` object:
- `guide_modal_eyebrow_1` through `guide_modal_eyebrow_7`
- `guide_modal_title_1` through `guide_modal_title_7`
- `guide_modal_hint`
- `guide_modal_hint_close`

Remove all `guide_modal_*` keys from the `SV_HTML` object:
- `guide_modal_body_1`, `guide_modal_body_1b`
- `guide_modal_body_2`, `guide_modal_list_2`, `guide_modal_body_2b`
- `guide_modal_body_3`, `guide_modal_list_3`, `guide_modal_tip_3`
- `guide_modal_body_4`, `guide_modal_list_4`, `guide_modal_tip_4`
- `guide_modal_body_5a` through `guide_modal_body_5d`
- `guide_modal_body_6`, `guide_modal_list_6`, `guide_modal_tip_6`
- `guide_modal_body_7`, `guide_modal_list_7`, `guide_modal_body_7b`

- [ ] **Step 4: Delete Guide-tab SV keys**

Remove from the `SV` object:
- `tab_guide` (the tab button label)
- `guide_open_btn`

Remove from the `SV_HTML` object:
- `guide_tab`
- `guide_tab_themes`

- [ ] **Step 5: Update macro wording in guide_body_leaderboard SV translation**

In `SV_HTML.guide_body_leaderboard`, change:
- `<h4>Makroregim-remsa</h4>` → `<h4>Makroregim-chips</h4>`
- "Den tunna remsan ovanför topplistan" → "SPY- och VIX-chipsen i rubriken"
- "Remsan är <strong>enbart information</strong>" → "Chipsen är <strong>enbart information</strong>"

- [ ] **Step 6: Update macro wording in guide_tab SV translation**

The `guide_tab` SV_HTML key is being deleted entirely (Step 4), so this is already handled.

- [ ] **Step 7: Verify**

Run: `python3 dashboard/build.py`
Expected: builds clean with no errors.

- [ ] **Step 8: Commit**

```bash
git add dashboard/templates/_i18n.html.j2
git commit -m "feat: i18n updates for dashboard redesign

- Add data-i18n-title support for chip tooltips
- Add SV keys for SPY/VIX chip tooltips
- Delete all guide_modal_* and guide_tab* SV keys
- Update macro guide text (remsa → chips) in SV

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Delete guide modal, delete macro bar file, verify + browser test

**Files:**
- Delete: `dashboard/templates/_guide_modal.html.j2` (367 lines)

**Interfaces:**
- Consumes: all prior tasks complete — guide-modal includes removed from all pages (Tasks 3–5), guide-modal CSS removed (Task 1), guide-modal i18n keys removed (Task 6)

- [ ] **Step 1: Delete the guide modal template**

```bash
git rm dashboard/templates/_guide_modal.html.j2
```

- [ ] **Step 2: Verify the build**

Run: `python3 dashboard/build.py`
Expected: builds clean. All three pages render without errors.

- [ ] **Step 3: Browser verification**

Start the static server and verify in the browser:
1. All three pages load without errors.
2. Command bar renders: h1, segment nav pills, macro chips (if macro data present), scan meta, lang toggle.
3. Macro chips have correct colors (green for SPY above, terracotta for below; calm/elevated/stressed for VIX).
4. Hovering chips shows tooltip text.
5. Tab switching works on all pages.
6. `ⓘ How to read` link expands the guide accordion inside each tab.
7. `⚙` settings popover on the leaderboard opens and the sentiment toggle works (re-ranks the table).
8. Digest line shows "more ▾" toggle; clicking expands to full view.
9. Footer shows disclaimer text + RSS link on all pages.
10. SV toggle translates all chrome including chip tooltips.
11. No console errors.

- [ ] **Step 4: Mobile viewport test**

Resize browser to 375px width:
1. Command bar wraps to ≤ 2 rows.
2. Scan meta hides at narrowest width.
3. Cards are full-width with correct margins.
4. Tables scroll horizontally inside the card.

- [ ] **Step 5: Update the leaderboard guide EN text (macro wording)**

In `index.html.j2`, in the `guide_body_leaderboard` content (the EN source text):
- Change `<h4>Macro regime bar</h4>` → `<h4>Macro regime chips</h4>`
- Change "The thin strip above the leaderboard shows" → "The SPY and VIX chips in the header show"
- Change "The bar is **info-only**" → "The chips are **info-only**"
- Simplify to: "The chips are **info-only** — they do not affect sector scores or rankings. Use them as context: the same sector ranking can mean different things depending on whether the broad market is in a healthy uptrend with low volatility or a stressed, risk-off environment."

- [ ] **Step 6: Rebuild and re-verify**

Run: `python3 dashboard/build.py`
Reload browser. Confirm the updated guide text renders correctly in both EN and SV.

- [ ] **Step 7: Commit**

```bash
git add -A -- dashboard/templates/
git commit -m "feat: delete guide modal, update macro guide wording

Remove _guide_modal.html.j2 (367 lines). All guide content now
served by per-tab accordions only.
Update EN macro guide text: 'strip/bar' → 'chips in the header'.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Index page utility-row fixes for other tabs + final polish

**Files:**
- Modify: `dashboard/templates/index.html.j2`

**Interfaces:**
- Consumes: `.utility-row` CSS (Task 1)

- [ ] **Step 1: Wrap remaining tab-guides in utility rows**

For each tab in `index.html.j2` that has a standalone `<details class="tab-guide">` (RRG, Drill-down, Movers, History), wrap it in a utility row:

```html
<div class="utility-row">
  <details class="tab-guide">
    <!-- existing content -->
  </details>
</div>
```

Tabs affected: `tab-rrg`, `tab-drilldown`, `tab-movers`, `tab-history`.

The Backtest tab has no tab-guide — leave it as-is.

- [ ] **Step 2: Verify the build + browser**

Run: `python3 dashboard/build.py`
Reload browser. Confirm all tabs' `ⓘ How to read` links align right.

- [ ] **Step 3: Commit**

```bash
git add dashboard/templates/index.html.j2
git commit -m "refactor: wrap remaining sector tab-guides in utility rows

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Final verification, backlog update, push, and PR

**Files:**
- Modify: `BACKLOG.md` (if there's a relevant queued item to close)
- All template files touched in Tasks 1–8

- [ ] **Step 1: Full browser verification**

Start static server. Walk through all three pages:

**Sectors page:**
- Command bar: h1 (16px), segment pills, SPY chip (green/terracotta), VIX chip (calm/elevated/stressed), scan meta (#131 · date), SV toggle.
- Card: white background, 10px radius, soft shadow over cream canvas.
- Tab strip inside card header; 6 tabs (no Guide tab).
- Leaderboard: utility row top-right with ⚙ and ⓘ. ⚙ opens sentiment popover. ⓘ expands guide.
- Digest: one-line with "more ▾"; clicking shows full 3-cluster view.
- All other tabs render, charts plot.
- Footer: disclaimer + RSS link.

**Themes page:**
- Same command bar.
- Card with 6 tabs (no Guide tab).
- Tab guides expand correctly.
- Footer present.

**Sentiment page:**
- Command bar.
- Card wrapping the cohort toggle + content.
- Sectors/Themes toggle works, scatter plots render.
- Footer present.

**Cross-cutting:**
- SV toggle: all chrome translates, chip tooltips translate.
- 375px viewport: command bar ≤ 2 rows, cards full-width.
- No console errors on any page.

- [ ] **Step 2: Run pytest**

Run: `pytest`
Expected: all existing tests pass (no Python logic changed).

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feature/dashboard-redesign
```

Then open a PR against `main`:

```bash
gh pr create --title "feat: dashboard UX redesign — compact command bar + card surfaces" --body "$(cat <<'EOF'
## Summary
- Compress dashboard chrome from 7 layers to 3 (command bar → card tab strip → one-line digest)
- Replace macro regime bar with inline SPY/VIX chips in the command bar (tooltips with `data-i18n-title`)
- Lift content onto white `.card` surfaces over a deeper cream canvas
- Delete guide modal (367 lines), Guide tabs, macro bar template — accordion-only help system
- Move sentiment ranking control into a `⚙` popover; restyle tab-guides as compact `ⓘ How to read` links
- Add site footer with disclaimer + RSS link
- Full SV i18n coverage for new/changed chrome

## Spec
`design/specs/2026-07-15-dashboard-ux-redesign-design.md`

## Test plan
- [ ] `python3 dashboard/build.py` — builds clean
- [ ] All three pages render in browser: command bar, cards, tabs, accordions, footer
- [ ] Macro chips: correct colors, tooltips, absent when macro unavailable
- [ ] SV toggle: all chrome translates including chip tooltips
- [ ] Sentiment ⚙ popover: toggle re-ranks table, weight input works
- [ ] Digest `more ▾` toggle expands/collapses
- [ ] 375px viewport: ≤ 2 command bar rows, cards full-width
- [ ] `pytest` green
- [ ] No console errors

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — do not merge**

Jonas reviews and merges the PR manually.
