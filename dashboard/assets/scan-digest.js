// "What changed today" digest.
// Summarizes new top-5 entries and biggest rank movers between the viewed
// scan and its predecessor, using data already shipped in SCAN_HISTORY.
(function () {
  "use strict";
  if (typeof SCAN_HISTORY === "undefined" || !SCAN_HISTORY.scans.length) return;

  var banner = document.getElementById("scan-digest-banner");
  if (!banner) return;

  function findPrevScanId(scanId) {
    for (var i = 0; i < SCAN_HISTORY.scans.length; i++) {
      if (SCAN_HISTORY.scans[i].id === scanId && i + 1 < SCAN_HISTORY.scans.length) {
        return SCAN_HISTORY.scans[i + 1].id;
      }
    }
    return null;
  }

  function computeDigest(scanId) {
    var scores = SCAN_HISTORY.scores[String(scanId)];
    if (!scores) return null;
    var prevId = findPrevScanId(scanId);
    if (prevId === null) return null;
    var prevScores = SCAN_HISTORY.scores[String(prevId)];
    if (!prevScores) return null;

    var entries = [];
    var movers = [];
    for (var key in scores) {
      if (!scores.hasOwnProperty(key)) continue;
      var s = scores[key];
      var parts = key.split("|");
      var region = parts[0];
      var sector = parts[1];
      var prev = prevScores[key];

      if (s.rank <= 5 && (!prev || prev.rank > 5)) {
        entries.push({ key: key, sector: sector, region: region, rank: s.rank });
      }

      if (prev) {
        var delta = prev.rank - s.rank;
        if (delta !== 0) {
          movers.push({ key: key, sector: sector, region: region, rank: s.rank, delta: delta });
        }
      }
    }

    entries.sort(function (a, b) { return a.rank - b.rank; });
    movers.sort(function (a, b) {
      var diff = Math.abs(b.delta) - Math.abs(a.delta);
      return diff !== 0 ? diff : a.rank - b.rank;
    });

    return {
      entries: entries,
      up: movers.filter(function (m) { return m.delta > 0; }).slice(0, 3),
      down: movers.filter(function (m) { return m.delta < 0; }).slice(0, 3),
    };
  }

  function fmtChip(item, isMover) {
    var label = item.sector + " (" + item.region + ")";
    if (!isMover) return label + " #" + item.rank;
    var cls = item.delta > 0 ? "up" : "down";
    var arrow = item.delta > 0 ? "▲" : "▼";
    return label + ' <span class="arrow ' + cls + '">' + arrow + "</span>" + Math.abs(item.delta);
  }

  function renderCluster(clusterKey, items, isMover) {
    var cluster = banner.querySelector('[data-cluster="' + clusterKey + '"]');
    var container = document.getElementById("digest-chips-" + clusterKey);
    if (!cluster || !container) return;
    if (!items.length) {
      cluster.style.display = "none";
      container.innerHTML = "";
      return;
    }
    cluster.style.display = "";
    container.innerHTML = items
      .map(function (item) { return '<span class="digest-chip">' + fmtChip(item, isMover) + "</span>"; })
      .join("");
  }

  window.renderScanDigest = function (scanId) {
    var digest = computeDigest(scanId);
    if (!digest) {
      banner.style.display = "none";
      return;
    }
    renderCluster("entries", digest.entries, false);
    renderCluster("up", digest.up, true);
    renderCluster("down", digest.down, true);
    var hasAny = digest.entries.length || digest.up.length || digest.down.length;
    banner.style.display = hasAny ? "" : "none";
  };

  window.renderScanDigest(SCAN_HISTORY.scans[0].id);
})();
