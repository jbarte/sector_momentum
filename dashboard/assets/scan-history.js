// Client-side scan history viewer.
// Rebuilds the leaderboard table from SCAN_HISTORY data when the user
// clicks a past scan in the History tab's scan index.
(function () {
  "use strict";
  if (typeof SCAN_HISTORY === "undefined" || !SCAN_HISTORY.scans.length) return;

  var table = document.getElementById("leaderboard-table");
  if (!table) return;
  var tbody = table.querySelector("tbody");
  var originalTbody = tbody.innerHTML;
  var banner = document.getElementById("scan-history-banner");
  var bannerText = banner ? banner.querySelector(".scan-history-text") : null;
  var headerDate = document.querySelector(".scan-date");
  var originalDate = headerDate ? headerDate.innerHTML : "";
  var sentimentToggle = document.getElementById("sentiment-toggle");
  var sentimentControl = document.getElementById("sentiment-control");
  var latestScanId = SCAN_HISTORY.scans[0].id;

  // renderScanLeaderboard() below builds row HTML by string concatenation
  // and interpolates `sector` (the theme name) and its ticker symbol
  // unescaped. Not exploitable today — both come from config/themes.yaml via
  // the pipeline, never from a reader — but hardening against the day any
  // row field stops being repo-controlled, same as auth.js's
  // renderLatestRows() and scan-digest.js's fmtChip(). `sector` was missed in
  // the 2026-08-23 sweep that hardened those two despite this file's own
  // comment (below) citing auth.js's identical pattern by name; the ticker
  // call site was then missed in the first fix too. Both caught in code
  // review the same day.
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtDelta(d) {
    if (d === 0) return "—";
    return (d > 0 ? "+" : "") + d.toFixed(1);
  }

  function findPrevScanId(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId && i + 1 < SCAN_HISTORY.scans.length) {
        return SCAN_HISTORY.scans[i + 1].id;
      }
    }
    return null;
  }

  function renderScanLeaderboard(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return;

    var prevId = findPrevScanId(scanId);
    var prevScores = prevId ? SCAN_HISTORY.scores[String(prevId)] : null;

    var entries = [];
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var s = scores[key];
      var delta = 0;
      if (prevScores && prevScores[key]) {
        delta = prevScores[key].rank - s.rank;
      }
      entries.push({ key: key, scores: s, delta: delta });
    }
    entries.sort(function (a, b) { return a.scores.rank - b.scores.rank; });

    // COHORTS (injected by build.py from src/cohorts.py) decides which regions
    // may render. No fallback list: historical scans still contain the retired
    // US/EU sector rows, so a default would replay dead sectors into the
    // scan-history view. No COHORTS means render nothing.
    var cohorts = (typeof COHORTS !== "undefined" && COHORTS.length) ? COHORTS : [];

    var regionGroups = {};
    cohorts.forEach(function (c) { regionGroups[c.region] = []; });
    for (var i = 0; i < entries.length; i++) {
      var region = entries[i].key.split("|")[0];
      // No fallback bucket: a row whose region isn't a known cohort is
      // skipped rather than misfiled under another cohort's group.
      if (regionGroups[region]) { regionGroups[region].push(entries[i]); }
    }

    var html = "";
    cohorts.forEach(function (c) {
      var group = regionGroups[c.region];
      if (!group.length) return;
      group.sort(function (a, b) { return a.scores.rank - b.scores.rank; });
      for (var j = 0; j < group.length; j++) {
        var e = group[j];
        var sc = e.scores;
        var sector = e.key.split("|")[1];
        // The reader's ACTIVE horizon, not HORIZON_DEFAULT: the selector stays
        // live in this view (only the sentiment toggle is disabled), and a
        // reader on Long would otherwise see past scans marked top-4. Falls back
        // to inBuyBand's own HORIZON_DEFAULT default if the page's inline script
        // has not defined currentHorizon yet.
        var _h = (typeof currentHorizon === "function") ? currentHorizon() : null;
        var rankClass = Rescore.inBuyBand(sc.rank, _h) ? " in-buy-band" : "";
        var arrow = "";
        var arrowClass = "";
        if (e.delta > 0) { arrow = "▲"; arrowClass = "up"; }
        else if (e.delta < 0) { arrow = "▼"; arrowClass = "down"; }
        var arrowHtml = arrow ? '<span class="arrow ' + arrowClass + '">' + arrow + "</span> " : "";

        // This view shows no trend badge. It never had a real one — the Trend
        // column held a literal "—" here through both the 6-column restructure
        // and its predecessor. When that column was dropped and the badge moved
        // into the theme cell, a placeholder had nowhere left to live: the other
        // builders put a real .traj-badge beside the setup badge, and an em dash
        // sitting in that slot would read as a trend value rather than as the
        // absence of one. Deriving a real trajectory for a past scan stays out
        // of scope.
        // Static, unlike rankClass above — applyHorizonBadges() never sets it.
        var rank1Class = (sc.rank === 1) ? " rank-1" : "";
        // Ticker: `sector` here is `e.key.split("|")[1]`, the plain theme
        // name — the same key window.THEME_TICKERS (build.py -> template ->
        // JS, keyed by theme name) is looked up on, exactly like auth.js's
        // renderLatestRows() does for r.gics_sector (auth.js:218-219).
        var ticker = (window.THEME_TICKERS || {})[sector] || "";
        var tickerHtml = ticker ? '<span class="theme-ticker">' + escapeHtml(ticker) + '</span>' : "";
        // The rank cell's left rail is written HERE, not left to
        // applyHorizonBadges(): that pass returns early for any row without a
        // data-rank attribute, and these rows deliberately carry none (a past
        // scan shows no action badges). Same condition as rankClass, so the
        // rail and the badge tint always agree.
        var railClass = rankClass ? " in-band-rail" : "";
        html += '<tr class="leaderboard-row">'
          + '<td class="rank-cell' + railClass + '"><span class="rank-badge' + rankClass + rank1Class + '">' + sc.rank + "</span></td>"
          + "<td class=\"theme-cell\"><span class=\"theme-name\">" + escapeHtml(sector) + "</span>" + tickerHtml + "</td>"
          + '<td class="composite-cell">' + Rescore.compositeBar(sc.composite) + "</td>"
          + '<td data-sort-value="' + (sc.level === null || sc.level === undefined ? "" : sc.level) + '">'
            + Rescore.levelChangeBars(sc.level, sc.change) + "</td>"
          + '<td class="delta-cell">' + arrowHtml + fmtDelta(e.delta) + "</td>"
          + "</tr>";

        // Band cut rows — same rule as applyBandBoundaries() (index.html.j2),
        // computed here instead of via that shared DOM pass: these rows
        // deliberately carry no data-rank attribute (see railClass's comment
        // above), which is exactly what that pass requires to find a row at
        // all. group is sorted by rank ascending, so "the next row" is
        // simply group[j + 1]; only a cut if something is actually below it.
        if (typeof window.buildBandCutRowHtml === "function" && _h) {
          var nextRank = (j + 1 < group.length) ? group[j + 1].scores.rank : null;
          var isBuyCut = nextRank !== null
            && sc.rank <= _h.top_n && nextRank > _h.top_n;
          var isHoldCut = nextRank !== null
            && sc.rank <= (_h.top_n + _h.buffer) && nextRank > (_h.top_n + _h.buffer);
          // The exit cut is the more consequential of the two, so if the
          // buffer leaves no gap between them (both land on this row), only
          // the exit cut shows — same tie-break as applyBandBoundaries().
          if (isBuyCut && !isHoldCut) {
            html += window.buildBandCutRowHtml('buy', 'band_buy_ends', 'BUY BAND ENDS',
              [{key: 'band_buy_note', en: 'below this line: not a new buy'}]);
          }
          if (isHoldCut) {
            var exitRank = _h.top_n + _h.buffer;
            html += window.buildBandCutRowHtml('exit', 'band_sell_line', 'SELL LINE', [
              {key: 'band_sell_note_prefix', en: 'a holding that falls past rank'},
              {text: String(exitRank)},
              {key: 'band_sell_note_suffix', en: 'is sold'}
            ]);
          }
        }
      }
    });
    tbody.innerHTML = html;
    // Same reason band-cut rows needed their own call here (Stage 2): this
    // view never calls applyHorizonBadges()/applyBandBoundaries(), so the
    // shared renderMobileCards() call inside applyBandBoundaries() never
    // runs for it either. Cards would silently go stale on a past scan
    // without this.
    if (typeof window.renderMobileCards === "function") { window.renderMobileCards(); }
    if (typeof window.renderBuyBand === "function") { window.renderBuyBand(); }
  }

  function updateShowingBadge(scanId) {
    var scanTable = document.querySelector(".scan-index table");
    if (!scanTable) return;
    var rows = scanTable.querySelectorAll("tbody tr");
    rows.forEach(function (tr) {
      var sid = tr.getAttribute("data-scan-id");
      var badgeCell = tr.querySelector("td:first-child");
      tr.classList.remove("active-scan");
      if (badgeCell) badgeCell.innerHTML = "";
      if (sid && parseInt(sid, 10) === scanId) {
        tr.classList.add("active-scan");
        if (badgeCell) badgeCell.innerHTML = '<span class="showing-badge">● Showing</span>';
      }
    });
  }

  function findScanMeta(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId) return SCAN_HISTORY.scans[i];
    }
    return null;
  }

  // Which past scan is on screen, or null for the latest. switchHorizon() reads
  // this to re-render: these rows bake the band highlight at render time, so
  // without a re-render the highlight would keep describing the horizon that
  // was active when the scan was opened.
  window.SM_ACTIVE_SCAN_ID = null;

  window.showScan = function (scanId) {
    window.SM_ACTIVE_SCAN_ID = scanId;
    renderScanLeaderboard(scanId);
    updateShowingBadge(scanId);
    var meta = findScanMeta(scanId);
    if (headerDate && meta) {
      headerDate.innerHTML = '<span data-i18n="lastScan">Last scan:</span> #' + scanId + " · " + meta.date;
    }
    if (banner) banner.style.display = "";
    if (bannerText) {
      var prefix = bannerText.getAttribute("data-en-prefix") || "Viewing scan #";
      bannerText.textContent = prefix + scanId;
    }
    if (sentimentToggle) sentimentToggle.disabled = true;
    if (sentimentControl) sentimentControl.style.opacity = "0.4";
    // Past-scan rows are rebuilt without filter data attributes.
    if (typeof window.setFilterBarVisible === "function") window.setFilterBarVisible(false);
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
    if (typeof window.renderScanDigest === "function") window.renderScanDigest(scanId);
  };

  window.restoreLatest = function () {
    window.SM_ACTIVE_SCAN_ID = null;
    tbody.innerHTML = originalTbody;
    // originalTbody is the BAKED markup, whose rank-badge highlight is the
    // default horizon's. Re-run the single writer so it matches whatever the
    // reader has selected; these rows carry data-rank, so the pass applies.
    if (typeof applyHorizonBadges === "function") { applyHorizonBadges(); }
    updateShowingBadge(latestScanId);
    if (headerDate) headerDate.innerHTML = originalDate;
    if (banner) banner.style.display = "none";
    if (sentimentToggle) {
      sentimentToggle.disabled = false;
      if (sentimentToggle.checked) {
        sentimentToggle.dispatchEvent(new Event("change"));
      }
    }
    if (sentimentControl) sentimentControl.style.opacity = "";
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
    if (typeof window.renderScanDigest === "function") window.renderScanDigest(latestScanId);
    // The original tbody (with filter data attributes) is back, so show the bar
    // and re-apply whatever filter state was active before.
    if (typeof window.setFilterBarVisible === "function") window.setFilterBarVisible(true);
    if (typeof window.applyFilters === "function") window.applyFilters();
  };

  // Delegated click + keyboard on scan-index table
  var scanTable = document.querySelector(".scan-index table");
  if (scanTable) {
    scanTable.addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-scan-id]");
      if (!tr) return;
      var sid = parseInt(tr.getAttribute("data-scan-id"), 10);
      if (sid === latestScanId) { window.restoreLatest(); return; }
      window.showScan(sid);
    });
    scanTable.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var tr = e.target.closest("tr[data-scan-id]");
      if (!tr) return;
      e.preventDefault();
      var sid = parseInt(tr.getAttribute("data-scan-id"), 10);
      if (sid === latestScanId) { window.restoreLatest(); return; }
      window.showScan(sid);
    });
  }
})();
