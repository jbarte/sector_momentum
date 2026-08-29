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

  function lock(untilISO, horizonKey) {
    state = { horizon_key: horizonKey, locked_until: untilISO, unlocked_at: null };
    return sb.from("book_locks").upsert(
      { horizon_key: horizonKey, locked_until: untilISO, unlocked_at: null },
      { onConflict: "user_id" });
  }

  function unlock() {
    if (state) { state.unlocked_at = new Date().toISOString(); }
    return sb.from("book_locks")
      .update({ unlocked_at: new Date().toISOString() })
      .not("locked_until", "is", null);
  }

  window.SMBookLock = {
    load: load, isLocked: isLocked, lockedUntil: lockedUntil,
    lock: lock, unlock: unlock,
    isLoaded: function () { return loaded; }
  };
})();
