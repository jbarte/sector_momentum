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

  /* ~114 bits from the CSPRNG — the topic is the only thing protecting the
     feed, so it must not be guessable. */
  function newTopic() {
    var buf = new Uint8Array(16);
    window.crypto.getRandomValues(buf);
    var s = "";
    for (var i = 0; i < buf.length; i++) { s += buf[i].toString(36); }
    return "sm-" + s.slice(0, 22);
  }

  function renderPref(pref) {
    root.hidden = false;
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
        if (res.error) { root.hidden = true; return; }   // table missing -> stay hidden
        renderPref(res.data && res.data.length ? res.data[0] : null);
      })
      .catch(function () { root.hidden = true; });
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
    sb.from("alert_prefs").update({ ntfy_topic: newTopic() }).neq("ntfy_topic", "")
      .then(function (res) {
        if (res.error) { setStatus("error"); return; }
        return load().then(function () { setStatus("saved"); });
      })
      .catch(function () { setStatus("error"); })
      .then(function () { regenBtn.disabled = false; });
  });

  enabledBox.addEventListener("change", function () {
    var next = enabledBox.checked;
    sb.from("alert_prefs").update({ enabled: next }).neq("ntfy_topic", "")
      .then(function (res) {
        if (res.error) { enabledBox.checked = !next; setStatus("error"); return; }
        setStatus("saved");
      })
      .catch(function () { enabledBox.checked = !next; setStatus("error"); });
  });

  copyBtn.addEventListener("click", function () {
    var text = topicEl.textContent;
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text)
      .then(function () { setStatus("copied"); })
      .catch(function () { setStatus("error"); });
  });

  /* RLS scopes every statement to the current user, so the update filters above
     only need to be non-empty predicates. */
  sb.auth.onAuthStateChange(function (_event, session) {
    if (session && session.user) { load(); }
    else { root.hidden = true; }
  });
})();
