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

  function fmtScore(v) {
    return v.toFixed(3);
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

    var regionGroups = { US: [], EU: [] };
    for (var i = 0; i < entries.length; i++) {
      var region = entries[i].key.split("|")[0];
      if (regionGroups[region]) { regionGroups[region].push(entries[i]); }
      else { regionGroups.US.push(entries[i]); }
    }

    var html = "";
    ["US", "EU"].forEach(function (region) {
      var group = regionGroups[region];
      if (!group.length) return;
      group.sort(function (a, b) { return a.scores.rank - b.scores.rank; });
      html += '<tr class="region-header-row"><td colspan="10">' + region + " Sectors</td></tr>";
      for (var j = 0; j < group.length; j++) {
        var e = group[j];
        var sc = e.scores;
        var sector = e.key.split("|")[1];
        var rankClass = sc.rank <= 3 ? " top3" : "";
        var arrow = "";
        var arrowClass = "";
        if (e.delta > 0) { arrow = "▲"; arrowClass = "up"; }
        else if (e.delta < 0) { arrow = "▼"; arrowClass = "down"; }
        var arrowHtml = arrow ? '<span class="arrow ' + arrowClass + '">' + arrow + "</span> " : "";

        html += '<tr class="leaderboard-row">'
          + '<td class="rank-cell"><span class="rank-badge' + rankClass + '">' + sc.rank + "</span></td>"
          + "<td>" + sector + "</td>"
          + '<td><span class="tag-region">' + region + "</span></td>"
          + '<td class="composite-cell">' + fmtScore(sc.composite) + "</td>"
          + "<td>" + fmtScore(sc.level) + "</td>"
          + "<td>" + fmtScore(sc.change) + "</td>"
          + "<td>" + fmtScore(sc.data) + "</td>"
          + '<td class="sentiment-cell">' + fmtScore(sc.sentiment) + "</td>"
          + '<td class="delta-cell">' + arrowHtml + fmtDelta(e.delta) + "</td>"
          + "<td>—</td>"
          + "</tr>";
      }
    });
    tbody.innerHTML = html;
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

  window.showScan = function (scanId) {
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
    if (typeof switchTab === "function") switchTab("leaderboard", document.querySelector('.tab-btn'));
    if (typeof window.renderScanDigest === "function") window.renderScanDigest(scanId);
  };

  window.restoreLatest = function () {
    tbody.innerHTML = originalTbody;
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
