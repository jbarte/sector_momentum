/* Phase-1 position tracking: signed-in users flag sectors/themes they hold.
 *
 * Presence of a row in public.positions == "held". Writes go straight to
 * Postgres under RLS. Fail-open: any missing piece leaves the page untouched.
 * Loaded after auth.js; runs on both the sector page and the themes page. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  if (!cfg || !cfg.url || !cfg.key || !window.SMSupabase) return;

  var sb = window.SMSupabase;  // shared client — see assets/supabase-client.js
  var held = null;       // Set of rowKey() strings once loaded, else null
  var signedIn = false;
  var loadPromise = null; // in-flight guard: concurrent callers await one request
  var loadFailed = false; // last holdings read failed -> `held` is empty by default, not by fact

  function rowKey(itemType, region, name) {
    return itemType + "|" + region + "|" + name;
  }

  /* Screen-reader announcement for a refused toggle. The visual cue is the
   * disabled styling; this is its non-visual counterpart. */
  function announceLive(msg) {
    var el = document.getElementById("sm-live-region");
    if (el) { el.textContent = msg; }
  }

  /* The refusal's VISIBLE counterpart for a touch device. announceLive()
   * above writes to #sm-live-region, which is .sr-only -- invisible by
   * design, for screen readers only. The `title` attribute the caller also
   * sets needs a hover-dwell no touch device ever produces. Without this, a
   * blocked tap on a phone produces no visible change at all -- exactly the
   * "reads as a broken page" failure this whole mechanism exists to avoid.
   *
   * Lives in the review panel (#rp-block-note, _review_panel.html.j2), not
   * on the star itself: the star that was actually tapped can be the
   * (mobile-hidden) TABLE row's button a card tap was forwarded to (see
   * _renderMobileCardsNow()'s card-to-table click forwarding in
   * index.html.j2) -- a flash on that element would be invisible on the
   * exact devices this fixes for. The panel is on screen either way. */
  function showBlockNote(msg) {
    var el = document.getElementById("rp-block-note");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    // Restart the CSS entrance animation on every tap, including repeated
    // taps while still locked: re-adding the same class name does not
    // restart an animation already applied, so the class is removed and a
    // reflow forced before it's added back.
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    window.clearTimeout(showBlockNote._hideTimer);
    showBlockNote._hideTimer = window.setTimeout(function () {
      el.hidden = true;
    }, 4000);
  }

  // Row identity: classify by cohort (data-region), not by which name
  // attribute happens to be present. Rows in the THEME cohort are themes
  // regardless of whether data-sector or the legacy data-theme is set;
  // everything else is a sector keyed by its region. Both the static build
  // (index.html.j2) and the live-upgrade rebuild (auth.js) set data-region
  // + data-sector on every row, including THEME ones — data-theme only
  // exists as a fallback for any pre-unification markup that still sets it.
  // Anything else (region headers, breakdown rows) yields null and is
  // skipped.
  function itemForRow(tr) {
    if (tr.dataset.region === "THEME") {
      return { item_type: "theme", region: "", name: tr.dataset.sector || tr.dataset.theme };
    }
    if (tr.dataset.sector) {
      return { item_type: "sector", region: tr.dataset.region || "", name: tr.dataset.sector };
    }
    if (tr.dataset.theme) {
      return { item_type: "theme", region: "", name: tr.dataset.theme };
    }
    return null;
  }

  function loadHoldings() {
    if (loadPromise) return loadPromise;         // coalesce concurrent callers
    loadFailed = false;
    loadPromise = sb.from("positions").select("item_type, region, name")
      .then(function (res) {
        held = new Set();
        // Still fail-open for the ★ toggles, but RECORD the failure. An empty
        // set is indistinguishable from "owns nothing", and the badge rule now
        // depends on holdings: silently treating a failed read as "owns
        // nothing" would delete every ▼ Exit badge for someone who does hold
        // things. holdingsState() reports it so the badge pass can fall back
        // to the plain band instead.
        if (res.error || !res.data) { loadFailed = true; return held; }
        res.data.forEach(function (r) {
          held.add(rowKey(r.item_type, r.region, r.name));
        });
        return held;
      })
      .catch(function () {                       // fail-open on hard reject
        held = new Set();
        loadFailed = true;
        return held;
      });
    return loadPromise;
  }

  function persist(add, item) {
    if (add) {
      return sb.from("positions").upsert(
        { item_type: item.item_type, region: item.region, name: item.name },
        { onConflict: "user_id,item_type,region,name", ignoreDuplicates: true });
    }
    return sb.from("positions").delete()
      .match({ item_type: item.item_type, region: item.region, name: item.name });
  }

  /* Holdings are what makes the Enter/Hold/Exit badge action-aware, so the badge
   * pass (index.html.j2:applyHorizonBadges) has to be able to ask this file
   * what is held, and has to re-run whenever the answer changes. */
  function announce() {
    document.dispatchEvent(new CustomEvent("sm:positions-changed"));
  }

  /* position-warn (the ⚠ on a holding that has gone to Exit) is NOT set here.
   * It is the same fact as the Exit badge — only a held theme can be badged
   * "exit" now — so the badge pass owns both and they cannot disagree. This
   * file used to derive it independently by looking for a rendered
   * .setup-badge.exit, which meant it silently read whatever the previous
   * badge pass had left behind. */
  function applyRowState(tr, isHeld) {
    tr.classList.toggle("position-held", isHeld);
    var btn = tr.querySelector(".position-toggle");
    if (btn) {
      btn.textContent = isHeld ? "★" : "☆";   // ★ / ☆
      btn.setAttribute("aria-pressed", isHeld ? "true" : "false");
      var label = isHeld ? "Held — click to remove" : "Mark as held";
      var key = isHeld ? "position_held_tip" : "position_mark_held_tip";
      btn.title = label;
      btn.setAttribute("aria-label", label);   // glyph alone isn't a usable SR name
      btn.setAttribute("data-i18n-title", key);
      btn.setAttribute("data-i18n-aria", key);
      // Reset the cached English fallback so applyLangToEl() below doesn't
      // translate stale pre-toggle text into the new label — same rule
      // apply() itself follows, just owned by this call site because this
      // button (unlike auth.js's insert-once UNBUYABLE_BADGE) is re-labeled
      // to a DIFFERENT value on every state change.
      btn.removeAttribute("data-en-title");
      btn.removeAttribute("data-en-aria");
      // Scoped translate, not a full-page applyLang() rescan: this button is
      // the only thing that just changed, and a page-wide rescan would also
      // needlessly re-run applyFilters() (applyLang()'s own side effect) on
      // every star click. Also covers sentiment.html.j2, which loads
      // positions.js but has no sm:positions-changed listener to catch this
      // any other way.
      if (window.applyLangToEl) window.applyLangToEl(btn);
    }
  }

  function decorateRow(tr) {
    var item = itemForRow(tr);
    if (!item) return;
    if (tr.querySelector(".position-toggle")) return;   // idempotent
    var nameCell = tr.cells[1];                          // sector/theme name cell
    if (!nameCell) return;
    var key = rowKey(item.item_type, item.region, item.name);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "position-toggle";
    nameCell.insertBefore(btn, nameCell.firstChild);
    applyRowState(tr, !!(held && held.has(key)));

    btn.addEventListener("click", function (e) {
      e.stopPropagation();                               // don't trigger row drill-down
      if (!held) return;
      // The lock's friction. Refused with a REASON: a star that silently does
      // nothing reads as a broken page, not as a deliberate block. The
      // override lives in the review panel -- deliberately a second, separate
      // action rather than a confirm dialog here, so clearing the lock is a
      // decision rather than a reflex.
      if (window.SMBookLock && window.SMBookLock.isLocked()) {
        var t = window.translate || function (k, en) { return en; };
        var until = window.SMBookLock.lockedUntil();
        var msg = t("lock_blocked", "Book locked until") + " " + (until || "");
        if (typeof announceLive === "function") { announceLive(msg); }
        btn.setAttribute("title", msg);
        showBlockNote(msg);
        return;
      }
      var next = !held.has(key);
      if (next) held.add(key); else held.delete(key);    // optimistic
      applyRowState(tr, next);
      announce();                                        // Enter <-> Hold flips now
      persist(next, item).then(function (res) {
        if (res && res.error) { revert(); }
      }).catch(revert);
      function revert() {
        if (next) held.delete(key); else held.add(key);
        applyRowState(tr, !next);
        announce();
      }
    });
  }

  function decorateAll() {
    if (!signedIn || !held) return;
    var rows = document.querySelectorAll(".leaderboard-row");
    Array.prototype.forEach.call(rows, decorateRow);
    var locked = !!(window.SMBookLock && window.SMBookLock.isLocked());
    Array.prototype.forEach.call(
      document.querySelectorAll(".position-toggle"), function (b) {
        b.classList.toggle("locked", locked);
        b.setAttribute("aria-disabled", locked ? "true" : "false");
      });
    announce();
  }

  function isHeldRow(tr) {
    if (!signedIn || !held) return false;
    var item = itemForRow(tr);
    if (!item) return false;
    return held.has(rowKey(item.item_type, item.region, item.name));
  }

  /* What the badge pass may assume about holdings:
   *   "ready"   holdings are known — label Enter vs Hold from them
   *   "loading" signed in, request in flight — badge nothing rather than
   *             flash "Enter" on a theme the reader actually holds
   *   "unknown" no session or the read failed — fall back to the plain band,
   *             which is what the page did before holdings existed. Showing a
   *             possibly-irrelevant Exit beats hiding a real one. */
  function holdingsState() {
    if (!signedIn || loadFailed) return "unknown";
    return held ? "ready" : "loading";
  }

  window.SMPositions = { isHeld: isHeldRow, holdingsState: holdingsState,
                         refreshLockUI: decorateAll };

  function clearAll() {
    var btns = document.querySelectorAll(".position-toggle");
    Array.prototype.forEach.call(btns, function (b) { b.parentNode.removeChild(b); });
    var rows = document.querySelectorAll(".position-held, .position-warn");
    Array.prototype.forEach.call(rows, function (tr) {
      tr.classList.remove("position-held", "position-warn");
    });
    held = null;
    loadPromise = null;
    announce();
  }

  sb.auth.onAuthStateChange(function (_event, session) {
    var now = !!(session && session.user);
    if (now && !signedIn) {
      signedIn = true;
      loadHoldings().then(decorateAll);   // themes page: static rows decorate now
    } else if (!now && signedIn) {
      signedIn = false;
      clearAll();
    }
  });

  // Sector leaderboard is rebuilt asynchronously by auth.js; (re)decorate then.
  document.addEventListener("sm:leaderboard-upgraded", function () {
    if (!signedIn) return;
    if (held) decorateAll();
    else loadHoldings().then(decorateAll);
  });
})();
