/* Phase-1 position tracking: signed-in users flag sectors/themes they hold.
 *
 * Presence of a row in public.positions == "held". Writes go straight to
 * Postgres under RLS. Fail-open: any missing piece leaves the page untouched.
 * Loaded after auth.js; runs on both the sector page and the themes page. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  if (!cfg || !cfg.url || !cfg.key || !window.supabase) return;

  var sb = window.supabase.createClient(cfg.url, cfg.key);
  var held = null;       // Set of rowKey() strings once loaded, else null
  var signedIn = false;
  var loadPromise = null; // in-flight guard: concurrent callers await one request

  function rowKey(itemType, region, name) {
    return itemType + "|" + region + "|" + name;
  }

  // Row identity: sector rows carry data-region + data-sector (from auth.js);
  // theme rows carry data-theme. Anything else (region headers, breakdown
  // rows) yields null and is skipped.
  function itemForRow(tr) {
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
    loadPromise = sb.from("positions").select("item_type, region, name")
      .then(function (res) {
        held = new Set();
        if (res.error || !res.data) return held;  // fail-open -> empty set
        res.data.forEach(function (r) {
          held.add(rowKey(r.item_type, r.region, r.name));
        });
        return held;
      })
      .catch(function () { held = new Set(); return held; });  // fail-open on hard reject
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

  function applyRowState(tr, isHeld) {
    tr.classList.toggle("position-held", isHeld);
    var hasExit = !!tr.querySelector(".setup-badge.exit");
    tr.classList.toggle("position-warn", isHeld && hasExit);
    var btn = tr.querySelector(".position-toggle");
    if (btn) {
      btn.textContent = isHeld ? "★" : "☆";   // ★ / ☆
      btn.setAttribute("aria-pressed", isHeld ? "true" : "false");
      var label = isHeld ? "Held — click to remove" : "Mark as held";
      btn.title = label;
      btn.setAttribute("aria-label", label);   // glyph alone isn't a usable SR name
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
      var next = !held.has(key);
      if (next) held.add(key); else held.delete(key);    // optimistic
      applyRowState(tr, next);
      persist(next, item).then(function (res) {
        if (res && res.error) { revert(); }
      }).catch(revert);
      function revert() {
        if (next) held.delete(key); else held.add(key);
        applyRowState(tr, !next);
      }
    });
  }

  function decorateAll() {
    if (!signedIn || !held) return;
    var rows = document.querySelectorAll(".leaderboard-row");
    Array.prototype.forEach.call(rows, decorateRow);
  }

  function clearAll() {
    var btns = document.querySelectorAll(".position-toggle");
    Array.prototype.forEach.call(btns, function (b) { b.parentNode.removeChild(b); });
    var rows = document.querySelectorAll(".position-held, .position-warn");
    Array.prototype.forEach.call(rows, function (tr) {
      tr.classList.remove("position-held", "position-warn");
    });
    held = null;
    loadPromise = null;
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
