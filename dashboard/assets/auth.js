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

  function showModal(show) {
    if (modal) modal.hidden = !show;
  }

  function render(session) {
    var signedIn = !!(session && session.user);
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

  var _upgraded = false;

  function fmtScore(v) {
    return (v === null || v === undefined || isNaN(v)) ? "—" : Number(v).toFixed(3);
  }

  function upgradeLeaderboard() {
    if (_upgraded) return;
    var tbody = document.querySelector("#tab-leaderboard tbody");
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
  }

  function renderLatestRows(tbody, rows, meta) {
    meta = meta || {};
    var byRegion = {};
    rows.forEach(function (r) { (byRegion[r.region] || (byRegion[r.region] = [])).push(r); });
    tbody.innerHTML = "";
    [["US", "US Sectors"], ["EU", "EU Sectors"]].forEach(function (pair) {
      var region = pair[0], label = pair[1];
      var list = byRegion[region] || [];
      if (!list.length) return;
      list.sort(function (a, b) { return a.rank - b.rank; });
      var hdr = document.createElement("tr");
      hdr.className = "region-header-row";
      hdr.innerHTML = '<td colspan="10">' + label + "</td>";
      tbody.appendChild(hdr);
      list.forEach(function (r) {
        var tr = document.createElement("tr");
        tr.className = "leaderboard-row";
        var rank = (r.rank === null || isNaN(r.rank)) ? "—" : Math.round(r.rank);
        var top3 = (typeof rank === "number" && rank <= 3) ? " top3" : "";
        var m = meta[r.region + "|" + r.gics_sector] || {};
        var badge = "";
        if (m.setup === "entry") {
          badge = '<span class="setup-badge entry" data-i18n="badge_entry">▲ Entry</span>';
        } else if (m.setup === "exit") {
          badge = '<span class="setup-badge exit" data-i18n="badge_exit">▼ Exit</span>';
        }
        var deltaInner = m.arrow
          ? '<span class="arrow ' + m.arrow_class + '">' + m.arrow + "</span> " + (m.delta_rank || "—")
          : (m.delta_rank || "—");
        var trendInner = m.trajectory_state
          ? '<span class="traj-badge traj-' + m.trajectory_state + '">' + m.trajectory_label + "</span>"
          : "—";
        tr.innerHTML =
          '<td class="rank-cell"><span class="rank-badge' + top3 + '">' + rank + "</span></td>" +
          "<td>" + r.gics_sector + badge + "</td>" +
          '<td><span class="tag-region">' + r.region + "</span></td>" +
          '<td class="composite-cell">' + fmtScore(r.composite) + "</td>" +
          "<td>" + fmtScore(r.level_score) + "</td>" +
          "<td>" + fmtScore(r.change_score) + "</td>" +
          "<td>" + fmtScore(r.data_score) + "</td>" +
          '<td class="sentiment-cell">' + fmtScore(r.sentiment_score) + "</td>" +
          '<td class="delta-cell">' + deltaInner + "</td>" +
          "<td>" + trendInner + "</td>";
        tbody.appendChild(tr);
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

  if (continueBtn) {
    continueBtn.addEventListener("click", function () {
      try { localStorage.setItem("guest_dismissed", "1"); } catch (e) {}
      showModal(false);
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

  /* Fires INITIAL_SESSION on load (restores a persisted session, or picks the
   * magic-link token out of the redirect URL via detectSessionInUrl) and
   * SIGNED_IN / SIGNED_OUT afterwards — the single source of UI state. */
  sb.auth.onAuthStateChange(function (_event, session) {
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
