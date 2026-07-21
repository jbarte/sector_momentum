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
