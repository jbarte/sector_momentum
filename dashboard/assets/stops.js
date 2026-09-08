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

      var cell = tr.querySelector(".theme-name") || tr.cells[1];
      if (!cell) return;

      var pct = Math.abs(Math.round(100 * Number(stop.drawdown)));
      var chip = document.createElement("span");
      chip.className = "stop-chip";
      chip.textContent = "■ " + pct + "%";
      chip.setAttribute("data-i18n-title", "stop_chip_tip");
      chip.title = "Closed " + pct + "% below its peak since you starred it, on "
                 + stop.stopped_on + ". It does not mean the position was sold.";
      cell.appendChild(chip);
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
