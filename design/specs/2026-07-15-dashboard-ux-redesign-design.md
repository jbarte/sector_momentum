# Dashboard UX redesign — compact command bar + card surfaces

**Date:** 2026-07-15
**Status:** approved direction (brainstormed with visual mockups; layout "A — compact command bar" + visual "B — card surfaces" chosen)

## Problem

The dashboard chrome has accreted: on the Sectors page a user stacks through **seven UI
layers before the first data row** — title row, page nav + disclaimer + Guide + SV buttons
(wrapping to three rows on narrow screens), the macro regime bar, a 7-tab bar, the
"How to read this tab" accordion, the sentiment ranking control, and the scan digest
banner. Help content is triplicated (guide modal carousel, a Guide tab per page, per-tab
accordions), and `_style.html.j2` has grown to ~915 lines of accumulated styles.

## Goals & decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Pain points | Too much chrome before content; visual clutter / dated look |
| Ambition | Moderate redesign — keep IA and warm-palette identity |
| Device | Desktop-first, graceful mobile collapse |
| Help system | **Accordions only** — delete Guide tab and guide modal |
| Layout | **A — compact command bar** (one header row; macro bar becomes inline chips) |
| Visual | **B — card surfaces** (content on white cards over the cream canvas) |

Result: **3 layers before the first data row** — command bar → card tab strip → one-line
digest.

Non-goals: no information-architecture changes (3 pages, same tabs), no dark mode, no
table-column or Plotly-figure-content changes, no changes to scan/scoring/build data
logic, reports pages (`docs/reports/`) and `feed.xsl` untouched.

## Design

### 1. Command bar (rewrite `_header.html.j2`)

One flex row on the cream canvas:

```
[Sector momentum]  [Sectors|Themes|Sentiment]        [SPY +8.6%] [VIX 16.5] [#131 · Jul 14] [SV]
```

- `h1` shrinks (~15–16px, still bold); scan meta moves to the right cluster as quiet text.
- Segment nav keeps the pill style (white active pill on muted track).
- **Macro chips replace the macro bar.** `_macro_bar.html.j2` is deleted and its includes
  removed from all three pages. Chips render from the same `macro` context:
  - SPY chip: `SPY +8.6%` — green tint when `spy_above`, terracotta tint when below.
    Tooltip (`title`): "vs 200-DMA — above" / "below".
  - VIX chip: `VIX 16.5` — background tint encodes band (calm = neutral green,
    elevated = amber, stressed = terracotta). Tooltip: band name.
  - Both chips absent when `macro` is `None` (same fail-quiet behaviour as today).
  - Tooltips are translated: add `data-i18n-title` support to the i18n `apply()`
    function (mirror of `data-i18n`, targeting the `title` attribute), reusing the
    existing `macro_vix_*` keys plus two new ones for above/below.
- **Guide button deleted** (modal is deleted). **RSS button moves to the footer.**
  **Disclaimer moves to the footer.**
- Responsive: `flex-wrap` — below ~720px the nav wraps to a second line under the title;
  scan meta hides at the narrowest width. Target: max two rows at 375px.

### 2. Card shell (all three pages)

- Each page's tab strip + tab panels are wrapped in a `.card`: white surface,
  10px radius, soft shadow (`0 1px 5px rgba(60,55,40,.10)`), on the cream canvas
  (canvas gets a slightly deeper tint than today's body background so the cards read
  as lifted).
- The tab strip becomes the card's header row (active tab keeps the green underline).
- **"How to read this tab" stays a per-tab `<details>`** (single help surface), restyled:
  the collapsed `<summary>` renders as a compact `ⓘ How to read` link at the top-right
  of the card body instead of a full-width beige bar; expanding reveals the existing
  guide content inside the card. No JS changes — pure CSS restyle of `.tab-guide`.
- Sentiment page (no tab bar): its sections wrap in the same `.card` treatment, with the
  existing Sectors/Themes cohort toggle as the card header.

### 3. Digest line (leaderboard card)

`#scan-digest-banner` collapses to **one line** inside the leaderboard card, under the
tab strip: the "New in Top 5" cluster inline, then gains/drops truncated, with a
`more ▾` toggle that expands to the full three-cluster view. Element IDs
(`digest-chips-entries`, `digest-chips-up`, `digest-chips-down`) are preserved so
`scan-digest.js` keeps working; the collapse is a default `.digest-collapsed` class +
a small toggle handler.

### 4. Rank settings popover (leaderboard card)

The sentiment ranking control (checkbox + weight input) moves out of the content flow
into a `<details class="rank-settings">` popover anchored to a small `⚙` summary that
sits beside the `ⓘ How to read` link in the utility row at the top-right of the
leaderboard card body (directly under the tab strip). Element IDs
`#sentiment-toggle` and `#sentiment-weight` are preserved so `rescore.js` is untouched.

### 5. Footer (new `_footer.html.j2`, included on all three pages)

Single quiet row at the page bottom: disclaimer text (reuses the `disclaimer` i18n key),
`RSS` link to `feed.xml`. Small type, muted color, top hairline border.

### 6. Deletions

- `_guide_modal.html.j2` — file, its includes on all three pages, `#guide-open-btn`,
  all `.guide-*` modal CSS, and all `guide_modal_*` i18n keys (~100 lines SV + ~250
  lines markup + ~150 lines CSS).
- **Guide tab** — the `tab-guide` panel section and its tab button in `index.html.j2`
  and `themes.html.j2`; the `guide_tab` / `guide_tab_themes` SV keys. The per-tab
  accordion keys (`guide_body_*`) stay.
- `_macro_bar.html.j2` and `.macro-bar` styles (superseded by header chips).
- The leaderboard accordion's macro section is reworded: "the thin strip above the
  leaderboard" → "the SPY and VIX chips in the header" (EN + SV).

### 7. CSS rework (`_style.html.j2`)

- New tokens: `--canvas: #f1ecdf` (page background, one step deeper than today's cream
  so cards read as lifted), `--surface: #ffffff` (card white), `--shadow-card: 0 1px 5px
  rgba(60,55,40,.10)`; keep the existing warm palette values for greens/terracotta/text.
- New components: `.command-bar`, `.chip` (+ `.chip-up/.chip-down/.chip-calm/
  .chip-elevated/.chip-stressed`), `.card`, `.rank-settings`, `.site-footer`.
- Restyle: `.tab-guide` (compact ⓘ variant), tab strip inside card, digest line.
- Prune dead styles: guide modal block, `.macro-bar`, old header layout, `.rss-link`
  header variant (moves to footer styling).
- Net expectation: `_style.html.j2` shrinks despite the new components.

## Sequencing / branching

This work builds on the macro-regime-bar branch (PR #93, open) because it consumes the
`macro` template context. Branch `feature/dashboard-redesign` from
`feature/macro-regime-bar` now; once PR #93 merges, rebase onto `main` before opening
the redesign PR. `docs/` stays out of the branch commits as always (CI owns it).

## Verification

1. `python3 dashboard/build.py` — builds clean.
2. Browser (local static server): all three pages — command bar renders with both chips
   (and without them when macro is unavailable), tabs switch, `ⓘ How to read` expands,
   digest `more ▾` expands, `⚙` popover toggles sentiment re-ranking (weight change
   re-sorts the table), footer shows disclaimer + working RSS link.
3. SV toggle: all visible chrome translates, including chip tooltips; no orphaned keys
   in the console.
4. Mobile (375px viewport): command bar wraps to ≤2 rows; cards full-width; table
   scrolls horizontally inside the card.
5. `pytest` — existing suite green (no Python behaviour changes expected; template
   renders exercised by the build).
