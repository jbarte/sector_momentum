/* Position lock. "Freeze my book until the next review date."
 *
 * This is FRICTION AND A RECORD, NOT ENFORCEMENT, and must never be described
 * to the reader as a guarantee: the override is one click away by design. A
 * lock that could not be cleared would let this dashboard stop the reader
 * recording a trade they had actually made at their broker -- leaving the
 * board wrong, which is a worse failure than the impulsive trade the lock
 * exists to slow down.
 *
 * State lives in Supabase rather than localStorage on purpose: a per-browser
 * lock would leave the phone unlocked, and the phone is exactly where an
 * impulsive trade happens.
 */
(function () {
  "use strict";
  var sb = window.SMSupabase;   // shared client — see assets/supabase-client.js
  if (!sb) { return; }

  var state = null;             // {locked_until, horizon_key} | null once loaded
  var loaded = false;

  function load() {
    return sb.from("book_locks").select("horizon_key, locked_until, unlocked_at")
      .maybeSingle()
      .then(function (res) {
        loaded = true;
        // A failed read must not fabricate a lock: an unavailable lock that
        // silently blocked every toggle would be indistinguishable from a bug.
        state = (res && !res.error && res.data) ? res.data : null;
        return state;
      })
      .catch(function () { loaded = true; state = null; return null; });
  }

  function lockedUntil() {
    return (state && !state.unlocked_at) ? state.locked_until : null;
  }

  /* Locked only while today is still before the release date. The lock
   * expires on its own at the review; nothing has to clear it. */
  function isLocked() {
    var until = lockedUntil();
    if (!until) { return false; }
    var today = (window.Rescore && window.Rescore.localISODate)
      ? window.Rescore.localISODate() : null;
    return today ? today < until : false;
  }

  /* Both writers below mirror load()'s fail-safe discipline: local `state`
   * only advances once the write is CONFIRMED (a resolved result with no
   * `.error`), inside the .then(). An eager, pre-write set here -- the
   * original bug -- left a rejected, unpersisted call reporting the new
   * state for the rest of the page session: fail LOCKED, the one outcome
   * the header comment forbids. On failure `state` is simply never
   * touched, so it stays whatever it was before the call. The rejection
   * itself is not swallowed -- no .catch() here -- so it keeps propagating
   * to the caller's own .then()/.catch(), same as before this fix. */
  function lock(untilISO, horizonKey) {
    return sb.from("book_locks").upsert(
      { horizon_key: horizonKey, locked_until: untilISO, unlocked_at: null },
      { onConflict: "user_id" })
      .then(function (res) {
        if (res && !res.error) {
          state = { horizon_key: horizonKey, locked_until: untilISO, unlocked_at: null };
        }
        return res;
      });
  }

  function unlock() {
    var ts = new Date().toISOString();
    return sb.from("book_locks")
      .update({ unlocked_at: ts })
      .not("locked_until", "is", null)
      .then(function (res) {
        if (res && !res.error && state) { state.unlocked_at = ts; }
        return res;
      });
  }

  window.SMBookLock = {
    load: load, isLocked: isLocked, lockedUntil: lockedUntil,
    lock: lock, unlock: unlock,
    isLoaded: function () { return loaded; }
  };

  /* The wiring load() itself doesn't provide: without a caller, `state` stays
   * null for the whole page lifetime and isLocked() reports false on every
   * fresh load -- the lock only ever holds within the SAME page session it
   * was set in, not across a reload or a second device (the phone -- the
   * exact case the header comment above says Supabase-over-localStorage
   * exists to cover). Mirrors positions.js's sb.auth.onAuthStateChange
   * pattern: load once per sign-in, reset on sign-out so a stale lock from a
   * previous user's session can't leak into the next one.
   *
   * `sb.auth` may not exist on a test-mocked SMSupabase (see
   * tests/test_book_lock.py's fail-safe tests, which stub only `.from()`),
   * so this stays guarded rather than assumed -- consistent with the
   * `if (!sb) return;` fail-open at the top of this file. */
  var _signedIn = false;
  function _afterLoad() {
    // Tell whoever is already on screen to re-render from the now-current
    // state, matching lock()/unlock()'s own callers (index.html.j2) rather
    // than inventing a second notification shape. Guarded on `document`
    // existing, not assumed -- this file is also exercised under plain Node
    // in tests/test_book_lock.py, which stubs `window` but not `document`.
    if (typeof document !== "undefined" && document.dispatchEvent) {
      document.dispatchEvent(new CustomEvent("sm:lock-changed"));
    }
    if (window.SMPositions && typeof window.SMPositions.refreshLockUI === "function") {
      window.SMPositions.refreshLockUI();
    }
    if (typeof window.renderReviewPanel === "function") {
      window.renderReviewPanel();
    }
  }
  if (sb.auth && typeof sb.auth.onAuthStateChange === "function") {
    sb.auth.onAuthStateChange(function (_event, session) {
      var now = !!(session && session.user);
      if (now && !_signedIn) {
        _signedIn = true;
        load().then(_afterLoad);
      } else if (!now && _signedIn) {
        _signedIn = false;
        state = null;
        loaded = false;
        _afterLoad();
      }
    });
  }
})();
