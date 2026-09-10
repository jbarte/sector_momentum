/* Beginner walkthrough deck: card-stepper state + first-visit auto-show.
 *
 * Deliberately its own module, not folded into auth.js -- auth.js is
 * exclusively sign-in state, and this has nothing to do with it beyond
 * needing to wait for auth.js's first decision to be made (see the
 * auto-show logic added in a later step of this file).
 *
 * State-tracking (currentStep/next/back/goToStep) is a plain closure over
 * an integer, deliberately decoupled from DOM rendering, so it's testable
 * under node with a minimal document/window stub -- see
 * tests/test_dashboard_js.py's beginner-deck tests.
 *
 * Declared with `var` at top level (not just assigned onto `window`) so
 * the bare name `BeginnerDeck` resolves both in a classic browser
 * <script> tag (where top-level `var` already becomes a `window`
 * property) and under tests/test_dashboard_js.py's node harness, which
 * stubs `window` as its own disconnected plain object rather than
 * aliasing it to the real global object -- an assignment of
 * `window.BeginnerDeck` alone would not be visible as a bare identifier
 * there. */
var BeginnerDeck = (function () {
  var TOTAL_STEPS = 4;
  var step = 1;

  function hasDom() {
    // tests/test_dashboard_js.py's node harness stubs document with bare
    // getElementById/querySelectorAll functions (present so `typeof
    // document.querySelectorAll` alone can't tell the stub apart from a
    // real browser) that throw the moment they're actually invoked with
    // ANY argument -- they exist only to be referenced, not called.
    // document.body is a plain property, never populated by that stub, so
    // checking it distinguishes "a real page" from the minimal stub
    // without ever calling into (and crashing on) the query functions
    // themselves. This is what keeps state-tracking (goToStep/next/back/
    // currentStep, tested under that stub) decoupled from DOM rendering.
    return typeof document !== "undefined" && !!document.body;
  }

  function render() {
    if (!hasDom()) { return; }
    var cards = document.querySelectorAll(".beginner-deck-card");
    Array.prototype.forEach.call(cards, function (card) {
      card.hidden = String(step) !== card.getAttribute("data-step");
    });

    var backBtn = document.getElementById("beginner-deck-back");
    var nextBtn = document.getElementById("beginner-deck-next");
    if (backBtn) { backBtn.hidden = step === 1; }
    if (nextBtn) { nextBtn.textContent = step === TOTAL_STEPS ? "Got it" : "Next"; }

    var dots = document.getElementById("beginner-deck-dots");
    if (dots) {
      dots.innerHTML = "";
      for (var i = 1; i <= TOTAL_STEPS; i++) {
        var dot = document.createElement("span");
        dot.className = "beginner-deck-dot" + (i === step ? " active" : "");
        dots.appendChild(dot);
      }
    }
  }

  function goToStep(n) {
    step = Math.max(1, Math.min(TOTAL_STEPS, n));
    render();
  }

  function next() { goToStep(step + 1); }
  function back() { goToStep(step - 1); }
  function currentStep() { return step; }

  return {goToStep: goToStep, next: next, back: back, currentStep: currentStep,
          TOTAL_STEPS: TOTAL_STEPS};
})();

if (typeof window !== "undefined") { window.BeginnerDeck = BeginnerDeck; }

(function () {
  if (typeof document === "undefined" || !document.body || !document.addEventListener) { return; }
  var nextBtn = document.getElementById("beginner-deck-next");
  var backBtn = document.getElementById("beginner-deck-back");
  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      if (BeginnerDeck.currentStep() === BeginnerDeck.TOTAL_STEPS) {
        if (window.SMBeginnerDeckModal) { window.SMBeginnerDeckModal.close(); }
        return;
      }
      BeginnerDeck.next();
    });
  }
  if (backBtn) { backBtn.addEventListener("click", BeginnerDeck.back); }
})();

/* First-visit auto-show, sequenced after the sign-in gate modal.
 *
 * maybeAutoShow() is deliberately a plain, synchronous decision function
 * with no MutationObserver inside it -- that keeps it testable under node
 * with a minimal document stub (see tests/test_dashboard_js.py). The
 * MutationObserver wiring below calls it at the right moments; the
 * decision logic itself doesn't need to know about observers at all. */
(function () {
  function deckDismissed() {
    try { return localStorage.getItem("deck_dismissed") === "1"; }
    catch (e) { return false; }
  }

  // Exposed on BeginnerDeck (not just called internally) so it's directly
  // testable -- see tests/test_dashboard_js.py's autoshow tests.
  window.BeginnerDeck.maybeAutoShow = function () {
    if (deckDismissed()) { return; }
    var gate = document.getElementById("gate-modal");
    if (!gate || gate.hidden) {
      window.BeginnerDeck.goToStep(1);
      if (window.SMBeginnerDeckModal) { window.SMBeginnerDeckModal.open(); }
    }
    // If the gate modal exists and is currently visible, do nothing here --
    // the MutationObserver below (real-DOM only, not exercised by the node
    // tests) is what shows the deck once the gate actually closes.
  };

  window.BeginnerDeck._markDismissed = function () {
    try { localStorage.setItem("deck_dismissed", "1"); } catch (e) {}
  };
})();

/* Real-DOM wiring: not exercised by the node tests above (they call
 * maybeAutoShow() directly), verified by hand in the browser per Task 5
 * Step 5 instead. Guarded so this file still loads harmlessly under the
 * node test stub, which defines no MutationObserver. */
if (typeof document !== "undefined" && document.addEventListener
    && typeof MutationObserver !== "undefined") {
  // Shared by every branch below: reads #gate-modal's CURRENT (already
  // real, post-render()) hidden state and either shows the deck now or
  // arranges to show it once the gate actually closes.
  function decideAgainstGate() {
    var gate = document.getElementById("gate-modal");
    if (!gate || gate.hidden) {
      window.BeginnerDeck.maybeAutoShow();
      return;
    }
    var observer = new MutationObserver(function () {
      if (gate.hidden) {
        observer.disconnect();
        window.BeginnerDeck.maybeAutoShow();
      }
    });
    observer.observe(gate, {attributes: true, attributeFilter: ["hidden"]});
  }

  // #gate-modal only exists in the markup at all when the build has auth
  // configured (index.html.j2's `{% if auth %}` block). When it's absent,
  // auth.js's own top-level guard (missing SUPABASE_CONFIG/#auth-root/
  // SMSupabase) returns before ever calling render() -- so sm:auth-changed
  // is never dispatched, and waiting for it below would mean the deck never
  // auto-shows at all. #gate-modal, if the build has one, is already in the
  // DOM by the time this script runs (it lives in the header, this script
  // loads at the end of body) so this check is safe to make immediately,
  // with no need to wait for DOMContentLoaded first.
  if (!document.getElementById("gate-modal")) {
    window.BeginnerDeck.maybeAutoShow();
  } else if (window.SM_SIGNED_IN !== undefined) {
    // auth.js's render() sets window.SM_SIGNED_IN immediately before it
    // dispatches sm:auth-changed (auth.js:100-101) -- the exact same
    // "has render() already run" signal stops.js already relies on for
    // this identical race (stops.js:82-84, `if (window.SM_SIGNED_IN)
    // load()`). Measured empirically in the browser (2026-09-10): the
    // Supabase client's initial onAuthStateChange callback can resolve
    // (and so call render() to completion, including its showModal()
    // call) within tens of milliseconds -- comfortably before this
    // script, several script tags later in the page, has even loaded.
    // Waiting exclusively for the event below would then wait forever
    // for a dispatch that already happened. Since render() runs
    // atomically (JS is single-threaded; this script's top-level code
    // cannot execute in the middle of render()'s body), SM_SIGNED_IN
    // being defined here means render() -- and its showModal() call --
    // has ALREADY fully finished, so #gate-modal's hidden attribute
    // already reflects the real decision and can be read immediately.
    decideAgainstGate();
  } else {
    document.addEventListener("sm:auth-changed", function onFirstAuthChange() {
      document.removeEventListener("sm:auth-changed", onFirstAuthChange);
      // auth.js's render() dispatches sm:auth-changed BEFORE it calls
      // showModal() (see dashboard/assets/auth.js: the dispatchEvent call is
      // several lines above the showModal()/showModal(false) branch at the
      // end of render()). dispatchEvent runs listeners synchronously, so at
      // the moment this fires, #gate-modal's `hidden` attribute still holds
      // its PRE-render() value (the template bakes it `hidden` initially) --
      // not this render() call's decision. Reading gate.hidden here directly
      // would see it still `true` for a first-time guest and open the deck a
      // few lines before render() opens the gate modal too, in the same
      // synchronous tick -- the exact simultaneous-open this task exists to
      // prevent. Deferring to a fresh task lets render() (and its
      // showModal() call) finish first, so gate.hidden reflects the real
      // decision by the time we read it.
      window.setTimeout(decideAgainstGate, 0);
    });
  }

  // The last card's "Got it" closes the modal (existing Back/Next handler);
  // this separately marks it dismissed so it never auto-shows again. Bound
  // here rather than in the Back/Next handler above so Task 4's stepper
  // logic stays free of the dismissal concern entirely. Applies regardless
  // of whether #gate-modal exists -- dismissal tracking doesn't depend on it.
  document.addEventListener("DOMContentLoaded", function () {
    var nextBtn = document.getElementById("beginner-deck-next");
    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        if (BeginnerDeck.currentStep() === BeginnerDeck.TOTAL_STEPS) {
          BeginnerDeck._markDismissed();
        }
      });
    }
  });
}
