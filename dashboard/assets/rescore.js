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

  var TRAJECTORY_WORDS = {
    strong_up: "surging", up: "rising", flat: "flat",
    down: "falling", strong_down: "sliding"
  };

  // Trajectory thresholds match _compute_rank_trajectories in build.py.
  // Negative slope = rank improving (climbing toward 1).
  function trajectoryLabel(slope) {
    if (slope <= -1.5) { return { label: "↑↑", state: "strong_up",   word: TRAJECTORY_WORDS.strong_up }; }
    if (slope <= -0.3) { return { label: "↑",  state: "up",          word: TRAJECTORY_WORDS.up }; }
    if (slope < 0.3)   { return { label: "→",  state: "flat",        word: TRAJECTORY_WORDS.flat }; }
    if (slope < 1.5)   { return { label: "↓",  state: "down",        word: TRAJECTORY_WORDS.down }; }
    return { label: "↓↓", state: "strong_down", word: TRAJECTORY_WORDS.strong_down };
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
                   setup: null, trajectory_label: "→", trajectory_state: "flat" };
      });
      return out;
    }

    // Split sectors by region for per-region ranking
    var regionGroups = {};
    sectors.forEach(function (key) {
      var region = key.split("|")[0];
      if (!regionGroups[region]) { regionGroups[region] = []; }
      regionGroups[region].push(key);
    });

    // composite[scanIdx] = {sector: value}; ranks[scanIdx] = {sector: rank}
    var compositeByScan = [];
    var rankByScan = [];
    for (var s = 0; s < nScans; s++) {
      var cMap = {};
      sectors.forEach(function (key) {
        var d = data.data[key][s];
        var sent = data.sentiment[key][s];
        cMap[key] = (1 - W) * d + W * sent;
      });
      var rMap = {};
      Object.keys(regionGroups).forEach(function (region) {
        var group = regionGroups[region];
        var vals = group.map(function (key) { return cMap[key]; });
        var ranks = rankAverage(vals);
        group.forEach(function (key, i) { rMap[key] = ranks[i]; });
      });
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
        setup: null,
        trajectory_label: traj.label,
        trajectory_state: traj.state
      };
    });
    return out;
  }

  // Position-band setup, mirroring dashboard/rows.py:_compute_setup. Entry
  // inside the buy band, Exit once past the hold band, silence in between.
  // `horizon` is {top_n, buffer}; omitted, it falls back to the page's
  // window.HORIZON_DEFAULT so this file stays usable on pages that have no
  // horizon selector.
  function setupForRank(rank, horizon) {
    var h = horizon || (typeof window !== "undefined" && window.HORIZON_DEFAULT) || null;
    if (!h || rank == null || isNaN(rank)) { return null; }
    if (rank <= h.top_n) { return "entry"; }
    if (rank > h.top_n + h.buffer) { return "exit"; }
    return null;
  }

  // Is this rank inside the buy band? Drives the highlighted rank badge.
  //
  // This was a literal `rank <= 3` in FOUR places — the server bake, the
  // sentiment rescore, the signed-in rebuild and the scan-history rebuild —
  // none of which knew about the horizon. `medium` holds 4, so three highlighted
  // badges sat above a buy-band cut line drawn after the fourth row, and `long`
  // (top 5) widened the disagreement to two rows. Same band rule as
  // setupForRank's entry arm, without the holdings and buyability logic that
  // badgeFor layers on: this is "where does it sit", not "what should you do".
  function inBuyBand(rank, horizon) {
    var h = horizon || (typeof window !== "undefined" && window.HORIZON_DEFAULT) || null;
    if (!h || rank == null || isNaN(rank)) { return false; }
    return rank <= h.top_n;
  }

  // Action-aware badge. setupForRank answers "where does this sit?"; the badge
  // answers "what should I do?", which needs to know whether the reader owns it:
  //
  //   not held, inside the buy band  -> "entry"  Enter
  //   held,     anywhere above exit  -> "hold"   Hold — you own it, do nothing
  //   held,     past the exit rank   -> "exit"   Exit
  //   not held, past the exit rank   -> null     you cannot exit what you don't own
  //
  // "hold" deliberately spans the buy band AND the silent middle band. Scoping
  // it to the buy band alone would make a held theme drifting rank 4 -> 6 lose
  // its badge, reading as a change when nothing happened — exactly the churn
  // the band rule exists to remove.
  //
  // The band rule itself is unchanged, and setupForRank still mirrors
  // dashboard/rows.py:_compute_setup for the server-side consumers (alerts,
  // badge scorecard) that have no reader and no holdings.
  function badgeForRank(rank, horizon, isHeld) {
    var h = horizon || (typeof window !== "undefined" && window.HORIZON_DEFAULT) || null;
    if (!h || rank == null || isNaN(rank)) { return null; }
    var band = setupForRank(rank, h);
    if (!isHeld) { return band === "entry" ? "entry" : null; }
    return band === "exit" ? "exit" : "hold";
  }

  // Which rule the leaderboard may apply, given what is known about holdings.
  // `state` comes from positions.js:holdingsState():
  //
  //   "ready"    holdings known -> the action-aware rule above
  //   "loading"  signed in, request in flight -> nothing, rather than flashing
  //              "Enter" on a theme the reader actually holds
  //   "unknown"  no session, or the holdings read failed -> the plain band,
  //              which is what the page did before holdings existed and what
  //              the server bakes on an ungated build. Showing a
  //              possibly-irrelevant Exit beats hiding a real one.
  //
  // Without the "unknown" arm the action-aware rule deletes every Exit badge
  // whenever holdings are unavailable — nothing is held, so nothing can be
  // exited — including on ungated builds, where it would strip badges the
  // server had just rendered.
  // `unbuyable` suppresses ONLY the Enter prompt. A theme with no route to
  // purchase can still be marked as held (the reader may own the exposure some
  // other way), and Hold/Exit on a position they say they have is still true
  // and still actionable. What would be false is telling them to buy something
  // they cannot buy — the same "badge names an action you cannot take" problem
  // that Enter-on-a-holding was.
  function badgeFor(rank, horizon, state, isHeld, unbuyable) {
    if (state === "loading") { return null; }
    var kind = (state === "ready")
      ? badgeForRank(rank, horizon, isHeld)
      : setupForRank(rank, horizon);
    if (unbuyable && kind === "entry") { return null; }
    return kind;
  }

  // The BOOK rule, mirroring src/backtest/strategy.py:_select.
  //
  // Everything above this point answers "where does this row sit?" -- a
  // property of one row. This answers "what does the strategy actually do?",
  // which is a property of the whole book and needs to know how many slots are
  // free. The dashboard never had this, which is why a board with four healthy
  // holdings still rendered a green Enter badge: the badge was right about the
  // band and silent about the fact that nothing could be bought.
  //
  // Kept in lockstep with the Python original by tests/test_select_book_parity.py
  // (Node), the same arrangement badgeFor already has.
  function _toSet(keys) {
    var s = {};
    if (keys) { for (var i = 0; i < keys.length; i++) { s[keys[i]] = true; } }
    return s;
  }

  function selectBook(rankedKeys, heldKeys, horizon, unbuyableKeys) {
    var h = horizon || (typeof window !== "undefined" && window.HORIZON_DEFAULT) || null;
    if (!h || !rankedKeys) { return null; }
    var held = _toSet(heldKeys), unbuyable = _toSet(unbuyableKeys);
    var band = h.top_n + h.buffer;

    var rankOf = {};
    for (var i = 0; i < rankedKeys.length; i++) { rankOf[rankedKeys[i]] = i; }
    // _select uses 10**9 for a held name with no rank this scan; the effect is
    // the same either way (it fails the `< band` test and is dropped), but the
    // sentinel is mirrored rather than special-cased so the two read alike.
    var MISSING = 1e9;

    // A held name keeps its slot while its rank is inside the band. One that
    // fell out -- or vanished from the scan entirely -- is sold.
    var keep = {}, keepCount = 0, sells = [];
    for (var j = 0; j < rankedKeys.length; j++) {
      var rk = rankedKeys[j];
      if (held[rk] && rankOf[rk] < band) { keep[rk] = true; keepCount++; }
    }
    for (var hk in held) {
      if (held.hasOwnProperty(hk) && !keep[hk]) { sells.push(hk); }
    }
    sells.sort(function (a, b) {
      return (rankOf[a] === undefined ? MISSING : rankOf[a])
           - (rankOf[b] === undefined ? MISSING : rankOf[b]);
    });

    // free < 0 is the over-held case: nothing is added, and nothing is
    // trimmed either. That is _select's real behaviour, not an oversight
    // here -- see test_over_held_book_is_not_trimmed.
    var free = h.top_n - keepCount;
    var buys = [];
    if (free > 0) {
      for (var b = 0; b < rankedKeys.length && buys.length < free; b++) {
        if (!keep[rankedKeys[b]]) { buys.push(rankedKeys[b]); }
      }
      for (var c = 0; c < buys.length; c++) { keep[buys[c]] = true; }
    }

    // Unbuyable names are removed AFTER selection so the slot goes unused,
    // exactly as simulate() does. `blocked` carries them out so the panel can
    // say "slot stays empty" instead of naming a buy the reader cannot make.
    var picks = [], blocked = [];
    for (var p = 0; p < rankedKeys.length; p++) {
      var pk = rankedKeys[p];
      if (!keep[pk]) { continue; }
      if (unbuyable[pk]) { blocked.push(pk); } else { picks.push(pk); }
    }
    var buysOut = [];
    for (var q = 0; q < buys.length; q++) {
      if (!unbuyable[buys[q]]) { buysOut.push(buys[q]); }
    }

    // Worst-ranked first: the surplus is what would be dropped to get back to
    // top_n. NOTE this ordering is OUR rule, not the strategy's -- simulate()
    // never over-holds, so the backtest has no opinion here. See the spec.
    var kept = [];
    for (var kk = 0; kk < rankedKeys.length; kk++) {
      if (keep[rankedKeys[kk]] && held[rankedKeys[kk]]) { kept.push(rankedKeys[kk]); }
    }
    var overHeld = Math.max(0, keepCount - h.top_n);
    var surplus = overHeld > 0 ? kept.slice(kept.length - overHeld).reverse() : [];

    return {
      picks: picks, buys: buysOut, sells: sells, blocked: blocked,
      surplus: surplus, freeSlots: Math.max(0, free), overHeld: overHeld
    };
  }

  // recentRows: array of {scan_id, region, gics_sector, change_score, composite, rank}.
  // Returns per-"REGION|Sector" meta for the LATEST scan: formatted delta, arrow,
  // trajectory, and entry/exit setup — mirroring dashboard/rows.py.
  function latestRowMeta(recentRows, horizon) {
    var groups = {};
    recentRows.forEach(function (r) {
      var key = r.region + "|" + r.gics_sector;
      (groups[key] || (groups[key] = [])).push(r);
    });
    var out = {};
    Object.keys(groups).forEach(function (key) {
      var rows = groups[key].slice().sort(function (a, b) { return a.scan_id - b.scan_id; });
      var n = rows.length;
      var latest = rows[n - 1];
      var dRank = (n >= 2) ? (rows[n - 2].rank - latest.rank) : 0;
      var deltaStr = (dRank !== 0) ? ((dRank > 0 ? "+" : "") + dRank.toFixed(1)) : "—";
      var arrow = dRank > 0 ? "▲" : (dRank < 0 ? "▼" : "");
      var arrowClass = dRank > 0 ? "up" : (dRank < 0 ? "down" : "");
      var series = [];
      for (var i = Math.max(0, n - 5); i < n; i++) { series.push(rows[i].rank); }
      var traj = trajectoryLabel(olsSlope(series));
      var setup = setupForRank(latest.rank, horizon);
      out[key] = {
        delta_rank: deltaStr, arrow: arrow, arrow_class: arrowClass,
        trajectory_label: traj.label, trajectory_state: traj.state,
        trajectory_word: traj.word, setup: setup
      };
    });
    return out;
  }

  /* Composite cell: a centre-origin diverging bar plus the number.
   *
   * The composite is an average of z-scores, so it is signed and centred on
   * zero — a left-filled bar would render -0.7 and +0.7 identically. Scale is
   * FIXED (+/-COMPOSITE_FULL_SCALE), not per-scan, so bar lengths stay
   * comparable between scans; values beyond it clamp.
   */
  var COMPOSITE_FULL_SCALE = 1.6;

  function signedFmt(v) {
    // 2 decimals, explicit sign, U+2212 instead of ASCII hyphen for negatives.
    return (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2);
  }

  function compositeBar(v) {
    var n = (v === null || v === undefined) ? null : Number(v);
    if (n === null || isNaN(n)) {
      return '<span class="cbar-wrap"></span><span class="cbar-val">—</span>';
    }
    var frac = Math.min(Math.abs(n) / COMPOSITE_FULL_SCALE, 1);
    var pct = (frac * 50).toFixed(1);
    var side = n >= 0 ? "left:50%" : "right:50%";
    var cls = n >= 0 ? "cbar pos" : "cbar neg";
    var valCls = n > 0 ? "cbar-val pos" : (n < 0 ? "cbar-val neg" : "cbar-val");
    return '<span class="cbar-wrap">'
         + '<span class="' + cls + '" style="' + side + ';width:' + pct + '%"></span>'
         + '</span>'
         + '<span class="' + valCls + '">' + signedFmt(n) + '</span>';
  }

  function levelChangeBars(level, change) {
    function row(label, value) {
      var v = (value === null || value === undefined) ? null : Number(value);
      if (v === null || isNaN(v)) {
        return '<div class="lc-row"><span class="lc-label">' + label + '</span>'
             + '<span class="lc-track"></span>'
             + '<span class="lc-val">—</span></div>';
      }
      var frac = Math.min(Math.abs(v) / COMPOSITE_FULL_SCALE, 1);
      var pct = (frac * 50).toFixed(1);
      var side = v >= 0 ? "left:50%" : "right:50%";
      var cls = v >= 0 ? "lc-bar pos" : "lc-bar neg";
      return '<div class="lc-row"><span class="lc-label">' + label + '</span>'
           + '<span class="lc-track"><span class="' + cls + '" style="' + side
           + ';width:' + pct + '%"></span></span>'
           + '<span class="lc-val">' + signedFmt(v) + '</span></div>';
    }
    return '<div class="lc-cell">' + row("LEVEL", level) + row("CHANGE", change) + '</div>';
  }

  // Whether a preset's Enter/Exit badges should render as actionable now or
  // muted (informational only, nothing new since the last review) — the fix
  // for "the daily-signal mismatch": badges used to recompute every scan
  // regardless of the preset's intended review cadence, so a reader checking
  // more often than that cadence saw far more churn than the preset was
  // tuned for. See BACKLOG.md "Badges don't say whether today is an
  // actionable day".
  //
  // reviewDates: the server-baked forward calendar for one preset (ISO date
  // strings, from src.horizons.review_dates via build.py's horizons_json).
  // todayISO / ackISO: the reader's local "today" and the last date they
  // acknowledged THIS preset's review (null if never) — both decided
  // CLIENT-side, never baked, so the muting state is always current even on
  // a page built days ago.
  //
  // due:false ("muted") is the ordinary, expected state on most days of the
  // month — not a failure mode. due:true ("actionable") only on/after a
  // review date that has not yet been acknowledged. Missing/malformed input
  // is the one case that fails toward due:true: it falls back to the
  // pre-existing always-on behaviour rather than a new, silently-muted one —
  // a board stuck permanently quiet is a worse failure than one that never
  // mutes.
  function reviewStatus(reviewDates, todayISO, ackISO) {
    if (!reviewDates || !reviewDates.length || !todayISO) {
      return { due: true, dueDate: null, nextDate: null };
    }
    var dates = reviewDates.slice().sort();
    var dueDate = null, nextDate = null;
    for (var i = 0; i < dates.length; i++) {
      if (dates[i] <= todayISO) { dueDate = dates[i]; }
      else if (nextDate === null) { nextDate = dates[i]; }
    }
    if (dueDate === null) {
      // Every baked date is still ahead of today: between reviews, nothing
      // has come due within this calendar yet. The common case, not an error.
      return { due: false, dueDate: null, nextDate: nextDate };
    }
    var due = !ackISO || ackISO < dueDate;
    return { due: due, dueDate: dueDate, nextDate: nextDate };
  }

  // Local calendar day as YYYY-MM-DD, from the reader's own clock — never
  // UTC, since "today" must match the day the reader actually sees on their
  // screen. ISO-format strings compare correctly with plain `<=`/`<`, which
  // is what reviewStatus relies on, so this never constructs a second Date
  // object to compare against — string comparison only, no timezone re-entry.
  function localISODate(d) {
    d = d || new Date();
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  // "31 Aug" in the reader's language. Intl carries the month names, so this
  // needs no new i18n keys -- and a badge that names a date is readable cold,
  // which the .muted styling it supplements is not.
  function shortDate(iso, lang) {
    if (!iso) { return ""; }
    var parts = String(iso).split("-");
    if (parts.length < 3) { return String(iso); }
    var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    try {
      return new Intl.DateTimeFormat(lang || "en",
        { day: "numeric", month: "short" }).format(d);
    } catch (e) {
      return String(iso);
    }
  }

  var api = { rankAverage: rankAverage, olsSlope: olsSlope, setupForRank: setupForRank,
              inBuyBand: inBuyBand,
              badgeForRank: badgeForRank, badgeFor: badgeFor, selectBook: selectBook,
              trajectoryLabel: trajectoryLabel, rescore: rescore,
              latestRowMeta: latestRowMeta, compositeBar: compositeBar, levelChangeBars: levelChangeBars, signedFmt: signedFmt,
              reviewStatus: reviewStatus, localISODate: localISODate, shortDate: shortDate,
              COMPOSITE_FULL_SCALE: COMPOSITE_FULL_SCALE };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.Rescore = api;
})(typeof window !== "undefined" ? window : this);
