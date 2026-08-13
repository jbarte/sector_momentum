/* Phase-2 personalized alerts: manage this user's ntfy topic.
 *
 * One row per user in public.alert_prefs, written by its owner under RLS.
 * The topic is generated in the browser so the secret is never issued by the
 * server. Fail-open: any missing piece leaves the page untouched. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  var root = document.getElementById("alert-prefs");
  if (!cfg || !cfg.url || !cfg.key || !root || !window.supabase) return;

  var sb = window.supabase.createClient(cfg.url, cfg.key);
  var userId = null;

  /* `root.hidden` means "alerts are available to this reader", NOT "the dialog
     is closed" — the modal overlay is a separate element for exactly that
     reason. The footer link mirrors availability, so a reader whose alert_prefs
     table is missing never gets a link that opens an empty dialog.
     Every place that used to assign root.hidden goes through here instead, so
     the two cannot drift apart. */
  function setAvailable(available) {
    root.hidden = !available;
    var link = document.getElementById("alerts-link");
    if (link) { link.hidden = !available; }
    // Deep link, deferred until the content exists. #alerts on a cold load
    // arrives long before the auth round-trip that decides availability.
    if (available && location.hash === "#alerts" && window.SMAlertsModal) {
      window.SMAlertsModal.open();
    }
  }

  var offBox = document.getElementById("alert-prefs-off");
  var onBox = document.getElementById("alert-prefs-on");
  var topicEl = document.getElementById("alert-prefs-topic");
  var linkEl = document.getElementById("alert-prefs-link");
  var copyBtn = document.getElementById("alert-prefs-copy");
  var enableBtn = document.getElementById("alert-prefs-enable");
  var regenBtn = document.getElementById("alert-prefs-regen");
  var enabledBox = document.getElementById("alert-prefs-enabled");
  var statusEl = document.getElementById("alert-prefs-status");

  /* Dynamic strings can't use the data-i18n pass, so they carry EN/SV pairs. */
  var MSG = {
    saved:   { en: "Saved.", sv: "Sparat." },
    copied:  { en: "Copied.", sv: "Kopierat." },
    error:   { en: "Something went wrong. Try again.", sv: "Något gick fel. Försök igen." }
  };

  function lang() {
    try { return localStorage.getItem("lang") === "sv" ? "sv" : "en"; }
    catch (e) { return "en"; }
  }

  function setStatus(key) {
    statusEl.textContent = key ? MSG[key][lang()] : "";
  }

  /* 128 bits from the CSPRNG, hex-encoded at fixed width (2 chars per byte, so
     nothing is silently truncated). The topic is the only thing protecting the
     feed, so it must not be guessable. */
  function newTopic() {
    var buf = new Uint8Array(16);
    window.crypto.getRandomValues(buf);
    var s = "";
    for (var i = 0; i < buf.length; i++) {
      s += buf[i].toString(16).padStart(2, "0");
    }
    return "sm-" + s;
  }

  function renderPref(pref) {
    setAvailable(true);
    if (!pref) {
      offBox.hidden = false;
      onBox.hidden = true;
      return;
    }
    offBox.hidden = true;
    onBox.hidden = false;
    topicEl.textContent = pref.ntfy_topic;
    linkEl.href = "https://ntfy.sh/" + encodeURIComponent(pref.ntfy_topic);
    enabledBox.checked = !!pref.enabled;
  }

  function load() {
    return sb.from("alert_prefs").select("ntfy_topic, enabled").limit(1)
      .then(function (res) {
        if (res.error) { setAvailable(false); return; }  // table missing -> stay hidden
        renderPref(res.data && res.data.length ? res.data[0] : null);
      })
      .catch(function () { setAvailable(false); });
  }

  enableBtn.addEventListener("click", function () {
    enableBtn.disabled = true;
    setStatus(null);
    sb.from("alert_prefs").insert({ ntfy_topic: newTopic(), enabled: true })
      .then(function (res) {
        if (res.error) { setStatus("error"); return; }
        return load().then(function () { setStatus("saved"); });
      })
      .catch(function () { setStatus("error"); })
      .then(function () { enableBtn.disabled = false; });
  });

  regenBtn.addEventListener("click", function () {
    regenBtn.disabled = true;
    setStatus(null);
    /* UPDATE, not delete+insert: enabled and created_at must survive. */
    sb.from("alert_prefs").update({ ntfy_topic: newTopic() }).eq("user_id", userId)
      .then(function (res) {
        if (res.error) { setStatus("error"); return; }
        return load().then(function () { setStatus("saved"); });
      })
      .catch(function () { setStatus("error"); })
      .then(function () { regenBtn.disabled = false; });
  });

  enabledBox.addEventListener("change", function () {
    var next = enabledBox.checked;
    enabledBox.disabled = true;
    sb.from("alert_prefs").update({ enabled: next }).eq("user_id", userId)
      .then(function (res) {
        if (res.error) { enabledBox.checked = !next; setStatus("error"); return; }
        setStatus("saved");
      })
      .catch(function () { enabledBox.checked = !next; setStatus("error"); })
      .then(function () { enabledBox.disabled = false; });
  });

  copyBtn.addEventListener("click", function () {
    var text = topicEl.textContent;
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text)
      .then(function () { setStatus("copied"); })
      .catch(function () { setStatus("error"); });
  });

  /* RLS scopes every statement to the current user; the .eq("user_id", ...)
     filters above are defense in depth in case that policy is ever dropped. */
  sb.auth.onAuthStateChange(function (_event, session) {
    if (session && session.user) { userId = session.user.id; load(); }
    else { userId = null; setAvailable(false); }
  });
})();
