// Pure client-side rescoring for the leaderboard sentiment toggle.
// No DOM access. Mirrors src/scoring.py rank semantics and
// dashboard/build.py:_compute_rank_trajectories OLS thresholds.
(function (root) {
  "use strict";

  // Descending rank: highest value -> rank 1. Average tie-break,
  // mirroring scipy.rankdata(-x, method="average").
  function rankAverage(values) {
    var n = values.length;
    var idx = values.map(function (v, i) { return i; });
    // Sort indices by value DESCENDING
    idx.sort(function (a, b) { return values[b] - values[a]; });
    var ranks = new Array(n);
    var i = 0;
    while (i < n) {
      var j = i;
      // Group ties (equal values)
      while (j + 1 < n && values[idx[j + 1]] === values[idx[i]]) { j++; }
      // Positions i..j (0-based) -> 1-based ranks i+1..j+1; average them
      var avg = 0;
      for (var k = i; k <= j; k++) { avg += k + 1; }
      avg = avg / (j - i + 1);
      for (var m = i; m <= j; m++) { ranks[idx[m]] = avg; }
      i = j + 1;
    }
    return ranks;
  }

  // Least-squares slope over x = 0..n-1. Returns 0 for n < 2.
  function olsSlope(values) {
    var n = values.length;
    if (n < 2) { return 0.0; }
    var xMean = (n - 1) / 2.0;
    var yMean = values.reduce(function (a, b) { return a + b; }, 0) / n;
    var num = 0, den = 0;
    for (var i = 0; i < n; i++) {
      num += (i - xMean) * (values[i] - yMean);
      den += (i - xMean) * (i - xMean);
    }
    return den === 0 ? 0.0 : num / den;
  }

  // Trajectory thresholds match _compute_rank_trajectories in build.py.
  // Negative slope = rank improving (climbing toward 1).
  function trajectoryLabel(slope) {
    if (slope <= -1.5) { return { label: "↑↑", state: "strong_up" }; }
    if (slope <= -0.3) { return { label: "↑", state: "up" }; }
    if (slope < 0.3)   { return { label: "→", state: "flat" }; }
    if (slope < 1.5)   { return { label: "↓", state: "down" }; }
    return { label: "↓↓", state: "strong_down" };
  }

  // Merge a split-region dataset into composite (GICS-only) entries keyed
  // "ALL|<sector>". Each per-scan value is the mean of the US and EU series.
  // Only sectors present in BOTH regions are emitted.
  function mergeComposite(data) {
    var bare = {};
    data.sectors.forEach(function (key) {
      var parts = key.split("|");
      var region = parts[0], sector = parts.slice(1).join("|");
      if (!bare[sector]) { bare[sector] = {}; }
      bare[sector][region] = key;
    });
    var nScans = data.scans.length;
    var sectors = [];
    var outData = {}, outSent = {};
    Object.keys(bare).sort().forEach(function (sector) {
      var us = bare[sector].US, eu = bare[sector].EU;
      if (!us || !eu) { return; } // require both regions
      var ck = "ALL|" + sector;
      sectors.push(ck);
      var d = [], s = [];
      for (var i = 0; i < nScans; i++) {
        d.push((data.data[us][i] + data.data[eu][i]) / 2);
        s.push((data.sentiment[us][i] + data.sentiment[eu][i]) / 2);
      }
      outData[ck] = d; outSent[ck] = s;
    });
    return { scans: data.scans, sectors: sectors, data: outData, sentiment: outSent };
  }

  // data = {scans:[{scan_id,run_at}], sectors:[key], data:{key:[..]}, sentiment:{key:[..]}}
  // Returns per-sector result for the LATEST scan.
  function rescore(data, W) {
    var sectors = data.sectors;
    var nScans = data.scans.length;
    var out = {};
    if (nScans === 0) {
      sectors.forEach(function (s) {
        out[s] = { rank: null, composite: 0, delta_rank: 0, delta_composite: 0,
                   emerging: false, trajectory_label: "→", trajectory_state: "flat" };
      });
      return out;
    }

    // composite[scanIdx] = {sector: value}; ranks[scanIdx] = {sector: rank}
    var compositeByScan = [];
    var rankByScan = [];
    for (var s = 0; s < nScans; s++) {
      var vals = sectors.map(function (key) {
        var d = data.data[key][s];
        var sent = data.sentiment[key][s];
        return (1 - W) * d + W * sent;
      });
      var ranks = rankAverage(vals);
      var cMap = {}, rMap = {};
      sectors.forEach(function (key, i) { cMap[key] = vals[i]; rMap[key] = ranks[i]; });
      compositeByScan.push(cMap);
      rankByScan.push(rMap);
    }

    var last = nScans - 1;
    var prev = nScans >= 2 ? last - 1 : null;

    sectors.forEach(function (key) {
      var rankNow = rankByScan[last][key];
      var compNow = compositeByScan[last][key];
      var dRank = 0, dComp = 0;
      if (prev !== null) {
        dRank = rankByScan[prev][key] - rankNow;          // + = climbed
        dComp = compNow - compositeByScan[prev][key];
      }
      // Trajectory: OLS slope over last up-to-5 scans' ranks
      var start = Math.max(0, nScans - 5);
      var rankSeries = [];
      for (var s2 = start; s2 < nScans; s2++) { rankSeries.push(rankByScan[s2][key]); }
      var traj = trajectoryLabel(olsSlope(rankSeries));

      out[key] = {
        rank: rankNow,
        composite: compNow,
        delta_rank: dRank,
        delta_composite: dComp,
        emerging: dRank > 0 && dComp > 0,
        trajectory_label: traj.label,
        trajectory_state: traj.state
      };
    });
    return out;
  }

  var api = { rankAverage: rankAverage, olsSlope: olsSlope,
              trajectoryLabel: trajectoryLabel, rescore: rescore,
              mergeComposite: mergeComposite };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.Rescore = api;
})(typeof window !== "undefined" ? window : this);
