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
 * writer) and marks each latched row with a "breached" chip. Also reads
 * public.position_stop_distance (same SELECT-only/scan-writes shape) and
 * renders a fill bar for a starred, opted-in position that has NOT yet
 * breached, showing how close it is to its stop. A row shows the chip OR the
 * bar, never both -- the chip always wins the scan a position breaches (see
 * decorate()). Fail-open in every direction: any missing piece (config,
 * table, or a rejecting query) leaves the page exactly as rendered — a stop
 * marker is worth nothing if its absence can break the leaderboard.
 *
 * The drawdown shown is measured from the peak SINCE THE ROW WAS STARRED, not
 * from a purchase price. positions carries no cost basis, so the number would
 * read as wrong to anyone who bought at a different time unless the page says
 * which peak it means — hence stop_chip_tip. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  if (!cfg || !cfg.url || !cfg.key || !window.SMSupabase) return;

  var sb = window.SMSupabase;
  var stopFrac = window.SM_TRAILING_STOP_FRAC;
  // computeProximity() already returns 0 for a falsy/zero/invalid stopFrac
  // (defensive on ITS side), but a 0-proximity bar still RENDERS -- at 0%
  // fill in pure var(--up) (green), asserting "nowhere near its stop" for a
  // position whose real proximity is simply unknown. Checked once here
  // (stopFrac is a module-wide constant, not per-row) and used by decorate()
  // to skip the bar entirely rather than render a fill that asserts
  // something false.
  var hasValidStopFrac = typeof stopFrac === "number" && isFinite(stopFrac) && stopFrac > 0;

  function rowKey(itemType, region, name) {
    return itemType + "|" + region + "|" + name;
  }

  function keyForRow(tr) {
    if (tr.dataset.region === "THEME") {
      return rowKey("theme", "", tr.dataset.sector || tr.dataset.theme);
    }
    return rowKey("sector", tr.dataset.region || "", tr.dataset.sector);
  }

  // Builds the bar shown INSTEAD OF the chip, for a starred, opted-in
  // position that has not yet breached. The FILL width is proportional
  // (SMStopDistance.computeProximity); the NUMBER beside it is the real
  // drawdown, in the same unit the breach chip already uses -- deliberately
  // NOT the proximity percentage, so the displayed number never drops at
  // the exact instant a position breaches (see the design spec's "The
  // number is the actual drawdown" section).
  function buildDistanceEl(row) {
    var pct = Math.abs(Math.round(100 * Number(row.drawdown)));
    var p = SMStopDistance.computeProximity(Number(row.drawdown), stopFrac);

    var wrap = document.createElement("span");
    wrap.className = "stop-distance";

    var track = document.createElement("span");
    track.className = "stop-distance-track";
    var fill = document.createElement("span");
    fill.className = "stop-distance-fill";
    fill.style.width = Math.round(p * 100) + "%";
    // An inline background-color the browser can't parse (color-mix()
    // unsupported) is dropped ENTIRELY, leaving the class rule's own
    // fallback colour (var(--fg4)) in place -- see _tables.css.j2's
    // .stop-distance-fill comment.
    fill.style.backgroundColor = SMStopDistance.proximityColor(p);
    track.appendChild(fill);

    var label = document.createElement("span");
    label.className = "stop-distance-pct";
    label.textContent = "-" + pct + "%";

    wrap.appendChild(track);
    wrap.appendChild(label);

    // window.SM_TRAILING_STOP_FRAC can be absent on a broken deploy (Task 3's
    // contract, not this module's own guarantee) -- computeProximity() above
    // already guards that defensively (returns 0), but stopPct is built
    // straight from stopFrac with no such guard, so an absent config would
    // render "...closes NaN% below peak." Degrade by omitting that sentence
    // instead of ever showing "NaN".
    var stopPct = Math.round(100 * stopFrac);
    var hasStopPct = isFinite(stopPct);
    wrap.setAttribute("data-i18n-title", "stop_distance_tip");
    wrap.title = "Currently " + pct + "% below its peak since you starred it, as of "
               + row.as_of + "."
               + (hasStopPct ? " An alert fires if it closes " + stopPct + "% below peak." : "");
    return wrap;
  }

  function decorate(stopsByKey, distanceByKey) {
    var rows = document.querySelectorAll("tr.leaderboard-row");
    Array.prototype.forEach.call(rows, function (tr) {
      var existingChip = tr.querySelector(".stop-chip");
      if (existingChip) existingChip.remove();
      var existingBar = tr.querySelector(".stop-distance");
      if (existingBar) existingBar.remove();

      var key = keyForRow(tr);
      var stop = stopsByKey[key];
      var distance = distanceByKey[key];

      if (!stop && !distance) { tr.classList.remove("position-stopped"); return; }

      // Both the bar and the chip live in the dedicated .stop-cell (the
      // trailing "To stop" column), not in the name cell they used to share
      // with the badges. Aligning them in one column is the whole point of
      // that column: inline, each bar started after a variable-length theme
      // name, so no two were comparable at a glance.
      //
      // The chip moves WITH the bar rather than staying behind: the two are
      // one mutually-exclusive status slot (a row shows exactly one), and
      // leaving the chip in the name cell would blank this column on
      // breached rows -- the rows it matters most for.
      //
      // Rows built before this column existed, or by a path that omits the
      // cell, simply get nothing: no fallback into cells[1], because a bar
      // appearing in the name cell on some rows and the column on others is
      // worse than it being absent. Every current builder emits the cell
      // (index.html.j2, auth.js, scan-history.js).
      var cell = tr.querySelector(".stop-cell");
      if (!cell) return;

      // The chip always wins when both exist -- a row transitions from bar
      // to chip the scan it breaches, never showing both.
      if (stop) {
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
        return;
      }

      tr.classList.remove("position-stopped");
      // window.SM_TRAILING_STOP_FRAC missing/invalid on a broken deploy
      // (Task 3's contract, not this module's own guarantee) must leave the
      // cell exactly as it already is -- matching this module's own
      // fail-open contract -- rather than render a bar that reads as
      // "nowhere near its stop" when that isn't actually known.
      if (!hasValidStopFrac) return;
      var bar = buildDistanceEl(distance);
      cell.appendChild(bar);
      if (window.applyLangToEl) window.applyLangToEl(bar);
    });
  }

  // Each query is caught and normalized to a safe {data: null} shape BEFORE
  // Promise.all sees it -- Promise.all itself rejects (skipping BOTH
  // results) the instant either promise rejects, which would otherwise mean
  // a missing position_stop_distance table (mid-deploy, before the
  // migration is applied) could silently kill the EXISTING breach chip too.
  //
  // Deliberately `.then(onFulfilled, onRejected)`, NOT `.catch(...)`: the
  // real supabase-js query builder returned by `sb.from(...).select(...)`
  // (dashboard/assets/supabase.min.js) is a bare thenable -- it implements
  // `.then()` but has no `.catch()`/`.finally()` and is not `instanceof
  // Promise`. Calling `.catch` directly on it throws a SYNCHRONOUS
  // TypeError before Promise.all is ever entered, which silently disabled
  // this module entirely (both the bar AND the pre-existing breach chip).
  // `.then(ok, err)` works identically whether the input is a real Promise
  // or a catch-less thenable -- see
  // tests/test_dashboard_js.py::test_safe_query_lets_the_other_query_succeed_when_one_rejects.
  function safeQuery(promise) {
    return promise.then(
      function (res) { return res; },
      function (err) { return {data: null, error: err}; }
    );
  }

  function load() {
    return Promise.all([
      safeQuery(sb.from("position_stops")
        .select("item_type, region, name, stopped_on, drawdown")),
      safeQuery(sb.from("position_stop_distance")
        .select("item_type, region, name, as_of, drawdown")),
    ]).then(function (results) {
      var stopsRes = results[0], distRes = results[1];
      var stopsByKey = {};
      if (!stopsRes.error && stopsRes.data) {
        stopsRes.data.forEach(function (r) {
          stopsByKey[rowKey(r.item_type, r.region || "", r.name)] = r;
        });
      }
      var distanceByKey = {};
      if (!distRes.error && distRes.data) {
        distRes.data.forEach(function (r) {
          distanceByKey[rowKey(r.item_type, r.region || "", r.name)] = r;
        });
      }
      decorate(stopsByKey, distanceByKey);
    }).catch(function () { /* fail-open: both queries already normalized
                             above, so this only guards decorate() itself
                             throwing on something unexpected. */ });
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
