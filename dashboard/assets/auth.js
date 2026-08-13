/* Invite-only magic-link sign-in via Supabase Auth.
 *
 * Loaded only when the build baked window.SUPABASE_CONFIG (publishable key
 * present). Fail-open: any missing piece leaves the dashboard untouched.
 * Sign-ups are disabled server-side; shouldCreateUser:false means links go
 * only to accounts already invited via the Supabase dashboard. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  var root = document.getElementById("auth-root");
  if (!cfg || !cfg.url || !cfg.key || !root || !window.supabase) return;

  var sb = window.supabase.createClient(cfg.url, cfg.key);

  var signinBtn = document.getElementById("auth-signin");
  var headerForm = document.getElementById("auth-form");
  var userBox = document.getElementById("auth-user");
  var emailLabel = document.getElementById("auth-email-label");
  var signoutBtn = document.getElementById("auth-signout");
  var modal = document.getElementById("gate-modal");
  var continueBtn = document.getElementById("gate-continue");

  /* Dynamic strings can't use the data-i18n pass (it runs once per toggle
   * over static nodes), so they carry their own EN/SV pairs. */
  var MSG = {
    sent: {
      en: "Link sent — check your inbox.",
      sv: "Länk skickad — kolla din inkorg.",
    },
    notInvited: {
      en: "No account for this email — access is invite-only.",
      sv: "Inget konto för den här e-postadressen — endast inbjudna.",
    },
    rateLimited: {
      en: "Please wait a minute and try again.",
      sv: "Vänta en minut och försök igen.",
    },
    expired: {
      en: "Link expired — request a new one.",
      sv: "Länken har gått ut — begär en ny.",
    },
    error: {
      en: "Sign-in failed. Try again.",
      sv: "Inloggningen misslyckades. Försök igen.",
    },
  };

  function lang() {
    try { return localStorage.getItem("lang") === "sv" ? "sv" : "en"; }
    catch (e) { return "en"; }
  }

  function setStatus(statusEl, key) {
    if (statusEl) statusEl.textContent = key ? MSG[key][lang()] : "";
  }

  function guestDismissed() {
    try { return localStorage.getItem("guest_dismissed") === "1"; }
    catch (e) { return false; }
  }

  /* The gate modal declared aria-modal="true" but implemented none of it: no
   * focus move, no trap, no Escape, no backdrop close — the first Tab landed
   * behind the overlay. window.SMModal (templates/_modal.js.j2, already used by
   * the methodology and tab-guide modals) provides all of it, and is defined
   * earlier in the page than this file loads.
   *
   * Fail-open: if the helper is somehow absent, fall back to the plain hidden
   * toggle. A missing a11y helper must not take sign-in down with it. */
  var gate = (window.SMModal && modal)
    ? window.SMModal.bind(modal, {closeBtn: continueBtn})
    : null;

  function showModal(show) {
    if (!modal) { return; }
    if (!gate) { modal.hidden = !show; return; }
    if (show) { gate.open(); } else { gate.close(); }
  }

  function render(session) {
    var signedIn = !!(session && session.user);
    /* The Enter/Hold/Exit badge is the signed-in tier (BADGES_GATED in the page).
     * Everything that renders a badge reads this flag, so it must be set
     * before any of them run — including the guest case, where it stays
     * false and the badge pass strips whatever the build baked. */
    window.SM_SIGNED_IN = signedIn;
    document.dispatchEvent(new CustomEvent("sm:auth-changed"));
    signinBtn.hidden = signedIn;
    userBox.hidden = !signedIn;
    emailLabel.textContent = signedIn ? (session.user.email || "") : "";
    var lagBanner = document.getElementById("lag-banner");
    if (lagBanner) lagBanner.hidden = signedIn;
    if (signedIn) {
      headerForm.hidden = true;
      showModal(false);
    } else {
      // First-visit landing modal (suppressed on return visits by the flag).
      showModal(!guestDismissed());
    }
  }

  /* Mirrors the static build's markup in index.html.j2 (same classes, same
   * i18n keys) so the two render paths look identical. applyLang() runs right
   * after the rebuild, which is what translates it. */
  var UNBUYABLE_BADGE =
    '<span class="unbuyable-badge" data-i18n="badge_unbuyable"'
    + ' data-i18n-title="unbuyable_tip"'
    + ' title="No UCITS equivalent exists, so this cannot be bought from an EU'
    + ' account. It is still scored because it shapes how every other theme'
    + ' ranks.">⊘ Not buyable</span>';

  var _upgraded = false;

  function fmtScore(v) {
    return (v === null || v === undefined || isNaN(v)) ? "—" : Number(v).toFixed(3);
  }

  function upgradeLeaderboard() {
    if (_upgraded) return;
    var tbody = document.querySelector("#leaderboard-table tbody");
    if (!tbody) return;
    _upgraded = true;
    sb.from("v_recent_scores")
      .select("scan_id, run_at, region, gics_sector, level_score, change_score, "
            + "data_score, sentiment_score, composite, rank")
      .order("scan_id", { ascending: true })
      .order("region", { ascending: true })
      .order("rank", { ascending: true })
      .then(function (res) {
        if (res.error || !res.data || !res.data.length) { _upgraded = false; return; }
        var meta = (window.Rescore && window.Rescore.latestRowMeta)
          ? window.Rescore.latestRowMeta(res.data) : {};
        var maxScan = res.data.reduce(
          function (m, r) { return r.scan_id > m ? r.scan_id : m; }, -Infinity);
        var latest = res.data.filter(function (r) { return r.scan_id === maxScan; });
        renderLatestRows(tbody, latest, meta);
        markLive();
        makeLeaderboardReadOnly();
        if (window.applyLang) {
          var lang = "en";
          try { lang = localStorage.getItem("lang") || "en"; } catch (e) {}
          window.applyLang(lang);
        }
        document.dispatchEvent(new CustomEvent("sm:leaderboard-upgraded"));
      });
  }

  function makeLeaderboardReadOnly() {
    window._leaderboardUpgraded = true;
    // Hide the sentiment/rank settings gear (its toggle can't work on upgraded rows).
    var settings = document.querySelector("#tab-leaderboard .rank-settings");
    if (settings) settings.style.display = "none";
    // Neutralize the sortable column headers (sortTable() also guards on the flag).
    var ths = document.querySelectorAll("#tab-leaderboard thead th[onclick]");
    ths.forEach(function (th) {
      th.style.cursor = "default";
      th.removeAttribute("tabindex");
    });
    // Filtering stays available: renderLatestRows emits the same filter data
    // attributes as the static build. Re-apply any active filters to the
    // freshly rebuilt rows. (Sorting stays disabled above — sortTable groups by
    // data-sector-key, which these rows don't carry.)
    if (typeof window.applyFilters === "function") window.applyFilters();
  }

  function renderLatestRows(tbody, rows, meta) {
    meta = meta || {};
    var byRegion = {};
    rows.forEach(function (r) { (byRegion[r.region] || (byRegion[r.region] = [])).push(r); });
    // Preserve the static drill-down panels (keyed by sector_id) so signed-in
    // rows stay expandable; re-appended under each rebuilt row below.
    var bdRows = {};
    Array.prototype.forEach.call(tbody.querySelectorAll(".breakdown-row"),
      function (r) { bdRows[r.id] = r; });
    tbody.innerHTML = "";
    // window.COHORTS (injected by build.py from src/cohorts.py) decides which
    // regions may render. There is deliberately NO fallback list: v_recent_scores
    // has no region filter and the retired US/EU sector rows are still in the
    // table, so a default would put dead sectors back on the leaderboard for
    // signed-in users. No COHORTS means render nothing.
    var cohorts = (window.COHORTS && window.COHORTS.length) ? window.COHORTS : [];
    cohorts.forEach(function (cohort) {
      var region = cohort.region;
      var list = byRegion[region] || [];
      if (!list.length) return;
      list.sort(function (a, b) { return a.rank - b.rank; });
      list.forEach(function (r) {
        var tr = document.createElement("tr");
        tr.className = "leaderboard-row";
        tr.dataset.region = r.region;
        tr.dataset.sector = r.gics_sector;
        tr.dataset.sectorId = region + "-" + r.gics_sector.replace(/ /g, "_");
        var rank = (r.rank === null || isNaN(r.rank)) ? "—" : Math.round(r.rank);
        var m = meta[r.region + "|" + r.gics_sector] || {};
        // The same filter attributes the static build emits, so the filter bar
        // works on signed-in rows too — minus data-setup. That one is written
        // by applyHorizonBadges() instead: the badge is action-aware, so it
        // needs the holdings set, which positions.js is still fetching at this
        // point. The page's applyHorizonBadges() pass owns data-setup and the
        // badge element for every row on both render paths; it runs on
        // sm:leaderboard-upgraded and again on sm:positions-changed.
        tr.dataset.trend = m.trajectory_state || "";
        // v_recent_scores has no buyability column; window.UNBUYABLE carries it
        // from the build so the marker and the suppressed Enter prompt survive
        // sign-in. applyHorizonBadges() reads data-unbuyable.
        // Region-scoped, matching src/universe.is_unbuyable: the flag belongs to
        // a THEME, and a same-named row in another cohort is a different
        // instrument that is perfectly tradeable.
        var unbuyable = r.region === "THEME"
          && (window.UNBUYABLE || []).indexOf(r.gics_sector) !== -1;
        if (unbuyable) { tr.dataset.unbuyable = "1"; }
        tr.dataset.composite = (r.composite === null || r.composite === undefined) ? "" : r.composite;
        tr.dataset.change = (r.change_score === null || r.change_score === undefined) ? "" : r.change_score;
        tr.dataset.rank = (r.rank === null || isNaN(r.rank)) ? "" : Math.round(r.rank);
        var deltaInner = m.arrow
          ? '<span class="arrow ' + m.arrow_class + '">' + m.arrow + "</span> " + (m.delta_rank || "—")
          : (m.delta_rank || "—");
        var trendInner = m.trajectory_state
          ? '<span class="traj-badge traj-' + m.trajectory_state + '">' + m.trajectory_label + "</span>"
          : "—";
        tr.innerHTML =
          // No band class here: this rebuild ends by dispatching
          // sm:leaderboard-upgraded, and applyHorizonBadges() writes the
          // highlight from the active horizon.
          '<td class="rank-cell"><span class="rank-badge">' + rank + "</span></td>" +
          "<td>" + r.gics_sector + (unbuyable ? UNBUYABLE_BADGE : "") + "</td>" +
          '<td class="composite-cell">' + Rescore.compositeBar(r.composite) + "</td>" +
          "<td>" + fmtScore(r.level_score) + "</td>" +
          "<td>" + fmtScore(r.change_score) + "</td>" +
          '<td class="sentiment-cell">' + fmtScore(r.sentiment_score) + "</td>" +
          '<td class="delta-cell">' + deltaInner + "</td>" +
          "<td>" + trendInner + "</td>";
        tbody.appendChild(tr);
        var bd = bdRows["bd-" + tr.dataset.sectorId];
        if (bd) tbody.appendChild(bd);
      });
    });
  }

  /* Task 6 resolved ambiguity: the leaderboard template renders no
   * #scan-date element (scan_date isn't used anywhere in
   * dashboard/templates/), so this only adds the Live chip — no
   * scan-date text update. */
  function markLive() {
    var host = document.querySelector(".command-bar .meta-cluster");
    if (host && !document.getElementById("live-chip")) {
      var chip = document.createElement("span");
      chip.id = "live-chip";
      chip.className = "chip chip-up";
      chip.textContent = "Live";
      host.insertBefore(chip, host.firstChild);
    }
  }

  signinBtn.addEventListener("click", function () {
    headerForm.hidden = !headerForm.hidden;
    if (!headerForm.hidden) {
      var inp = headerForm.querySelector(".auth-email");
      if (inp) inp.focus();
    }
  });

  /* Only the explicit "Continue as guest" press suppresses the modal on future
   * visits. Escape and backdrop-click (added by SMModal) just close it for now:
   * an accidental Escape should not permanently hide the sign-in prompt. The
   * helper closes the overlay itself, so this only records the choice. */
  if (continueBtn) {
    continueBtn.addEventListener("click", function () {
      try { localStorage.setItem("guest_dismissed", "1"); } catch (e) {}
      if (!gate) { showModal(false); }
    });
  }

  // Bind every magic-link form (header dropdown + landing modal) to the same
  // signInWithOtp flow. Each form owns its own email input and status span.
  var forms = Array.prototype.slice.call(document.querySelectorAll(".auth-form"));
  forms.forEach(function (form) {
    var emailInput = form.querySelector(".auth-email");
    var sendBtn = form.querySelector(".auth-send");
    var statusEl = form.querySelector(".auth-status");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (sendBtn) sendBtn.disabled = true;
      setStatus(statusEl, null);
      sb.auth
        .signInWithOtp({
          email: emailInput.value.trim(),
          options: {
            shouldCreateUser: false,
            emailRedirectTo: window.location.origin + window.location.pathname,
          },
        })
        .then(function (res) {
          if (!res.error) { setStatus(statusEl, "sent"); return; }
          if (res.error.status === 429) setStatus(statusEl, "rateLimited");
          else if (/signup|not allowed|not found/i.test(res.error.message || "")) setStatus(statusEl, "notInvited");
          else setStatus(statusEl, "error");
        })
        .catch(function () { setStatus(statusEl, "error"); })
        .then(function () { if (sendBtn) sendBtn.disabled = false; });
    });
  });

  signoutBtn.addEventListener("click", function () {
    sb.auth.signOut().catch(function () {});
  });

  /* upgradeLeaderboard() has no inverse: it replaces the baked (lagged) rows
   * with live ones, hides the rank-settings gear, disables sorting and sets
   * window._leaderboardUpgraded, which the template's sort/rescore paths also
   * read. Unwinding that by hand means restoring six pieces of state plus
   * whatever listened to sm:leaderboard-upgraded — so instead, sign-out reloads
   * the baked page, which IS the gated state. positions.js and alert-prefs.js
   * clear themselves on SIGNED_OUT; the leaderboard was the only leak.
   *
   * Reload rules, both load-bearing:
   *   - only on SIGNED_OUT with a session seen earlier in this page's life.
   *     A guest load fires INITIAL_SESSION(null), and older supabase-js builds
   *     could emit SIGNED_OUT there too — reloading on a null session alone
   *     would be an infinite reload loop for every guest.
   *   - navigate to pathname+search, dropping any hash. A magic-link redirect
   *     arrives as #access_token=…; reloading with that hash intact would let
   *     detectSessionInUrl re-consume it and sign the user straight back in.
   *     replace() also keeps the signed-in view out of the back button. */
  var _hadSession = false;

  sb.auth.onAuthStateChange(function (event, session) {
    if (session && session.user) _hadSession = true;
    if (event === "SIGNED_OUT" && _hadSession) {
      window.location.replace(window.location.pathname + window.location.search);
      return;
    }
    render(session);
    if (session && session.user) upgradeLeaderboard();
  });

  /* A failed magic-link redirect (expired/invalid link) comes back with
   * #error=…&error_code=… in the URL instead of a session. */
  var hash = window.location.hash || "";
  if (hash.indexOf("error=") !== -1) {
    if (headerForm) headerForm.hidden = false;
    var hs = headerForm ? headerForm.querySelector(".auth-status") : null;
    setStatus(hs, hash.indexOf("otp_expired") !== -1 ? "expired" : "error");
  }
})();
