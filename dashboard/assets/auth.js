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
  var form = document.getElementById("auth-form");
  var emailInput = document.getElementById("auth-email");
  var sendBtn = document.getElementById("auth-send");
  var status = document.getElementById("auth-status");
  var userBox = document.getElementById("auth-user");
  var emailLabel = document.getElementById("auth-email-label");
  var signoutBtn = document.getElementById("auth-signout");

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

  function setStatus(key) {
    status.textContent = key ? MSG[key][lang()] : "";
  }

  function render(session) {
    var signedIn = !!(session && session.user);
    signinBtn.hidden = signedIn;
    userBox.hidden = !signedIn;
    var lagBanner = document.getElementById("lag-banner");
    if (lagBanner) lagBanner.hidden = signedIn;
    emailLabel.textContent = signedIn ? (session.user.email || "") : "";
    if (signedIn) {
      form.hidden = true;
      setStatus(null);
    }
  }

  signinBtn.addEventListener("click", function () {
    form.hidden = !form.hidden;
    if (!form.hidden) emailInput.focus();
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    sendBtn.disabled = true;
    setStatus(null);
    sb.auth
      .signInWithOtp({
        email: emailInput.value.trim(),
        options: {
          shouldCreateUser: false,
          emailRedirectTo: window.location.origin + window.location.pathname,
        },
      })
      .then(function (res) {
        if (!res.error) { setStatus("sent"); return; }
        if (res.error.status === 429) setStatus("rateLimited");
        else if (/signup|not allowed|not found/i.test(res.error.message || "")) setStatus("notInvited");
        else setStatus("error");
      })
      .catch(function () { setStatus("error"); })
      .then(function () { sendBtn.disabled = false; });
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
    form.hidden = false;
    setStatus(hash.indexOf("otp_expired") !== -1 ? "expired" : "error");
  }
})();
