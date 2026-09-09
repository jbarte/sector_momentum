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
