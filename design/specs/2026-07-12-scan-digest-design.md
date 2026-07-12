# "What Changed Today" Digest

**Date:** 2026-07-12
**Status:** Approved
**Backlog item:** "What changed today" digest

## Summary

A compact summary strip above the sector leaderboard that shows, at a glance,
what's different since the previous scan: sectors that newly entered the top
5, and the biggest rank movers (up and down). Entirely client-side — no
pipeline, schema, or scoring changes — built on top of the `SCAN_HISTORY` JSON
blob already shipped by the renderable-scan-history feature (2026-07-12).

Sectors page only, same scope boundary as renderable-scan-history (the
`SCAN_HISTORY` blob itself is sector-only data).

## Scope (v1)

Two of the three signals named in the original backlog idea:

- **New top-5 entries** — a sector's rank crossed into the top 5 this scan
  (wasn't ≤5 in the previous scan, or didn't exist in it).
- **Biggest movers** — up to 3 biggest rank gains and up to 3 biggest rank
  drops by absolute delta (ties broken by current rank), no minimum move size.

**Deferred to a fast-follow:** trajectory flips. Trajectory state
(↑↑/↑/→/↓/↓↓) is currently a 5-scan trailing-slope computation that only runs
server-side, in `dashboard/rows.py::_compute_rank_trajectories`, for the
*latest* scan — it is not part of the `SCAN_HISTORY` blob. Including it in the
digest would mean porting that slope algorithm into client-side JS (to
recompute trajectory state for both the current and previous scan from raw
rank history), which is a non-trivial amount of duplicated logic deserving its
own design and test pass. v1 ships without it.

## Data & Computation

New file `dashboard/assets/scan-digest.js` (IIFE, same pattern as
`scan-history.js`/`rescore.js`). Guarded no-op if `SCAN_HISTORY` is undefined,
has no scans, or the banner element is missing.

**`computeDigest(scanId)`** — finds `scanId`'s predecessor in
`SCAN_HISTORY.scans`. `scan-history.js` has an equivalent lookup
(`findPrevScanId`), but it's a private helper inside that file's own IIFE, not
exposed on `window` — so `scan-digest.js` defines its own small, local copy of
the same ~6-line linear scan rather than introduce cross-file coupling for a
trivial lookup. Returns `null` if there is no predecessor (first scan
ever — nothing to diff). Otherwise, for every `region|sector` key present in
both scans' score maps in `SCAN_HISTORY.scores`:

- `delta = rank_prev - rank` (positive = moved up in rank, i.e. improved).
- **Entries:** `rank <= 5 AND (key not in prevScores OR prevScores[key].rank > 5)`.
- **Movers:** sort all keys by `abs(delta)` descending (ties → current rank
  ascending); take top 3 with `delta > 0` as gains, top 3 with `delta < 0` as
  drops, independently (so a day with only upward churn doesn't force-fill the
  drops list with unrelated 1-rank noise).

Return shape:

```js
{
  entries: [{ key, sector, region, rank }, ...],
  up:      [{ key, sector, region, rank, delta }, ...],
  down:    [{ key, sector, region, rank, delta }, ...],
}
```

Any or all arrays may be empty — an empty result across all three is the
"nothing notable" case, not an error state.

## Rendering

New `#scan-digest-banner` div, placed directly above `#scan-history-banner`
(which itself sits above the leaderboard's `.table-wrap`). When both are
visible — viewing a historical scan that also has a digest — they stack:
digest banner on top, "Viewing scan #N" banner below it.

Markup (static skeleton in the template, JS fills in the chip lists and
toggles cluster visibility):

```html
<div id="scan-digest-banner" style="display:none">
  <span class="digest-cluster" data-cluster="entries">
    <span data-i18n="digest_new_top5">New in Top 5:</span>
    <span class="digest-chips" id="digest-chips-entries"></span>
  </span>
  <span class="digest-cluster" data-cluster="up">
    <span data-i18n="digest_gains">Biggest gains:</span>
    <span class="digest-chips" id="digest-chips-up"></span>
  </span>
  <span class="digest-cluster" data-cluster="down">
    <span data-i18n="digest_drops">Biggest drops:</span>
    <span class="digest-chips" id="digest-chips-down"></span>
  </span>
</div>
```

Chip text: entries render as `{sector} ({region}) #{rank}`; movers render as
`{sector} ({region}) {arrow}{|delta|}`, reusing the leaderboard's existing
`.arrow.up`/`.arrow.down` classes (`var(--up)`/`var(--down)`) so colors match
the rest of the page. Each `.digest-cluster` is hidden (`display:none`) when
its array is empty; the whole banner is hidden when all three are empty. No
click interaction on chips in v1 — clicking a sector's leaderboard row already
opens its breakdown panel.

## Integration with scan-history.js

`scan-digest.js` computes and renders the digest for the latest scan
(`SCAN_HISTORY.scans[0].id`) on page load.

To stay in sync with historical browsing (renderable-scan-history), two
one-line additions to the *existing* `dashboard/assets/scan-history.js`:
`showScan(scanId)` and `restoreLatest()` each call
`if (typeof window.renderScanDigest === "function") window.renderScanDigest(scanId)`
(for `restoreLatest`, pass the latest scan id). The `typeof` guard means
`scan-history.js` has no hard dependency on `scan-digest.js` — either file can
be present or absent independently. This is the only change to
`scan-history.js`; all digest logic lives in the new file.

`window.renderScanDigest(scanId)` is the single exposed entry point: it calls
`computeDigest(scanId)` and updates the banner in place. It serves both
"page just loaded" and "user clicked a historical scan row" — same function,
same rendering path.

## i18n

Three new SV keys in `_i18n.html.j2`: `digest_new_top5`, `digest_gains`,
`digest_drops`. The cluster labels are static template-rendered
`data-i18n`-tagged spans (English text as the DOM default), so the existing
`applyLang()` mechanism translates them automatically — no language-detection
logic needed inside `scan-digest.js` itself. This sidesteps a latent gap in
the existing scan-history banner (its "Viewing scan #" prefix is written by
JS via `data-en-prefix` and never re-translates after a language switch);
the digest's labels don't have that problem because only static markup, not
JS-generated text, carries the translatable strings.

## CSS

New `#scan-digest-banner` — same visual family as `#scan-history-banner`
(bg-raised, left accent border, compact padding, flex layout) but its own id
since both banners can be visible simultaneously. `.digest-chips` renders as
small inline pill-style spans; movers reuse `.arrow.up`/`.arrow.down`.

## Testing

| Test | File | What |
|------|------|------|
| `test_scan_digest_json_in_rendered_output` | `test_dashboard_js.py` | Rendered `index.html` contains the `scan-digest.js` script tag, `#scan-digest-banner` markup, and the three new i18n keys |

No JS unit tests — vanilla client-side logic, same precedent as
`scan-history.js` (verified via browser check, not a JS test harness). No
Python/schema changes, so no backend tests.

## Out of Scope

- Trajectory flips (fast-follow — needs the slope-algorithm port discussed above)
- Themes page (sectors only; `SCAN_HISTORY` is sector-only data)
- Chip click-interactivity
- Dismiss control / localStorage persistence — banner always shows when there's something to report, matching the scan-history banner's always-on pattern
- Minimum move-size threshold for movers (top-3-by-magnitude already suppresses noise without an arbitrary cutoff)
