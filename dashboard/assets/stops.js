/* Pure stop-distance math: no DOM, no Supabase, no window.SUPABASE_CONFIG --
 * deliberately kept OUTSIDE the config-gated IIFE below, as its own
 * top-level `var`, so it is reachable under node for testing (see
 * tests/test_dashboard_js.py) even when no Supabase config exists at all.
 * Declared with `var` (not just assigned onto `window`) for the same
 * reason beginner-deck.js's module is: a bare `SMStopDistance` identifier
 * must resolve both in a classic browser <script> tag and under this
 * file's own node test harness, which stubs `window` as a disconnected
 * plain object rather than aliasing it to the real global. */
var SMStopDistance = (function () {
  // drawdown is <= 0 (e.g. -0.07); stopFrac is > 0 (e.g. 0.12). Clamped to
  // [0, 1]: a position that fell hard between two scans can already be
  // well past its stop by the time this reading was taken (a scan's own
  // breaching reading is written to this same table before the latch
  // takes over -- see src/alerts.py's collect_stop_events), so an
  // unclamped value could exceed 100% and overflow the bar. Guarded
  // against stopFrac <= 0 defensively, even though
  // src.horizons.trailing_stop_frac() guarantees 0 < val < 1 in production.
  function computeProximity(drawdown, stopFrac) {
    if (!stopFrac || stopFrac <= 0) { return 0; }
    var p = drawdown / -stopFrac;
    return Math.max(0, Math.min(1, p));
  }

  // Two segments, so the midpoint (p = 0.5) is a dark, theme-aware neutral
  // (var(--fg1)) rather than the muddy brown a direct var(--up)->var(--down)
  // mix produces in this palette -- see the design spec's colour table
  // (sector_momentum-notes/specs/2026-09-11-stop-distance-indicator-design.md).
  // Colour is redundant encoding, never the sole signal: the number beside
  // the bar (stops.js's decorate()) carries the same information in
  // greyscale or for a colour-blind reader.
  function proximityColor(p) {
    if (p <= 0.5) {
      return "color-mix(in srgb, var(--fg1) " + Math.round(p * 200) + "%, var(--up))";
    }
    return "color-mix(in srgb, var(--down) " + Math.round((p - 0.5) * 200) + "%, var(--fg1))";
  }

  return {computeProximity: computeProximity, proximityColor: proximityColor};
})();

if (typeof window !== "undefined") { window.SMStopDistance = SMStopDistance; }

/* Trailing stop-loss markers on held rows.
 *
 * Reads public.position_stops (SELECT-only under RLS; the scan is the sole
 * writer) and marks each latched row. Fail-open in every direction: any
 * missing piece leaves the page exactly as rendered — a stop marker is worth
 * nothing if its absence can break the leaderboard.
 *
 * The drawdown shown is measured from the peak SINCE THE ROW WAS STARRED, not
 * from a purchase price. positions carries no cost basis, so the number would
 * read as wrong to anyone who bought at a different time unless the page says
 * which peak it means — hence stop_chip_tip. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  if (!cfg || !cfg.url || !cfg.key || !window.SMSupabase) return;

  var sb = window.SMSupabase;

  function rowKey(itemType, region, name) {
    return itemType + "|" + region + "|" + name;
  }

  function keyForRow(tr) {
    if (tr.dataset.region === "THEME") {
      return rowKey("theme", "", tr.dataset.sector || tr.dataset.theme);
    }
    return rowKey("sector", tr.dataset.region || "", tr.dataset.sector);
  }

  function decorate(stopsByKey) {
    var rows = document.querySelectorAll("tr.leaderboard-row");
    Array.prototype.forEach.call(rows, function (tr) {
      var existing = tr.querySelector(".stop-chip");
      if (existing) existing.remove();

      var stop = stopsByKey[keyForRow(tr)];
      if (!stop) { tr.classList.remove("position-stopped"); return; }

      // .theme-name holds only a single text node (index.html.j2's documented
      // invariant, ~line 797) -- renderReviewPanel()'s nameOf(), the mobile
      // card projection, and the band-cut summary strip all read its
      // textContent/innerHTML directly and would pick up a nested chip. Every
      // other badge (.unbuyable-badge, .setup-badge, .traj-badge,
      // .theme-ticker, and positions.js's own star toggle) is inserted as a
      // SIBLING within the containing cell, never inside .theme-name itself
      // -- this appends to that same cell, matching the established pattern.
      var nameSpan = tr.querySelector(".theme-name");
      var cell = nameSpan ? nameSpan.parentNode : tr.cells[1];
      if (!cell) return;

      var pct = Math.abs(Math.round(100 * Number(stop.drawdown)));
      var chip = document.createElement("span");
      chip.className = "stop-chip";
      chip.textContent = "■ " + pct + "%";
      chip.setAttribute("data-i18n-title", "stop_chip_tip");
      chip.title = "Closed " + pct + "% below its peak since you starred it, on "
                 + stop.stopped_on + ". It does not mean the position was sold.";
      cell.appendChild(chip);
      // Page-wide applyLang() already ran (auth.js runs it before dispatching
      // sm:leaderboard-upgraded/sm:auth-changed, which is what triggers this
      // decorate() call) and will not run again for an element created after
      // it. Without a scoped translate call here, a Swedish-language reader
      // would see this English title forever. Same pattern positions.js uses
      // for its own dynamically-created content (applyRowState()).
      if (window.applyLangToEl) window.applyLangToEl(chip);
      tr.classList.add("position-stopped");
    });
  }

  function load() {
    return sb.from("position_stops").select("item_type, region, name, stopped_on, drawdown")
      .then(function (res) {
        if (res.error || !res.data) return;      // fail-open: leave the page alone
        var byKey = {};
        res.data.forEach(function (r) {
          byKey[rowKey(r.item_type, r.region || "", r.name)] = r;
        });
        decorate(byKey);
      })
      .catch(function () { /* fail-open */ });
  }

  /* auth.js sets window.SM_SIGNED_IN then dispatches sm:auth-changed
   * (auth.js:100-101). There is no "sm:signed-in" event -- subscribing to one
   * would mean the chips never appear at all. */
  document.addEventListener("sm:auth-changed", load);

  /* Holdings changing can clear a latch (unstarring cascades the row away),
   * so re-read rather than leaving a marker for a position that no longer
   * exists. */
  document.addEventListener("sm:positions-changed", load);

  /* auth.js:154 REPLACES the leaderboard DOM for a signed-in reader
   * (sm:leaderboard-upgraded) -- every chip appended before that point is
   * destroyed with the rows it was attached to. Re-decorating here is what
   * makes the marker survive the upgrade; without it the chips appear and
   * then silently vanish a moment after sign-in. */
  document.addEventListener("sm:leaderboard-upgraded", load);

  if (window.SM_SIGNED_IN) load();
})();
