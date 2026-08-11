// dashboard/assets/theme.js
//
// Owns: the data-theme attribute (beyond the blocking init script's first
// paint job), the manual control's click handling, and — from Task 7 on —
// the colour substitution that lets baked Plotly figures follow the theme.
//
// resolveTheme/recolor are written as PURE functions (all inputs as
// parameters, no reads of localStorage/matchMedia/DOM inside them) so they
// are unit-testable under plain Node, mirroring rescore.js's
// badgeForRank(rank, horizon, isHeld) — this file follows the same
// convention deliberately.
(function (root) {
  "use strict";

  // Pure: given the current data-theme attribute value (or null) and whether
  // the OS reports dark, decide the EFFECTIVE theme. An explicit attribute
  // always wins over a disagreeing OS — that is the entire point of a manual
  // override.
  function resolveTheme(attr, osIsDark) {
    if (attr === "light" || attr === "dark") { return attr; }
    return osIsDark ? "dark" : "light";
  }

  // Pure: given a stored choice ("auto"/"light"/"dark"/anything else treated
  // as "auto"), which of the three control buttons should read aria-pressed.
  // Split out from updateControlUI so "does the control reflect the stored
  // choice on load" is testable under plain Node instead of only by manually
  // reloading a browser three times — see the Task 1 test for exactly that.
  function pressedStateFor(choice) {
    var c = (choice === "light" || choice === "dark") ? choice : "auto";
    return { auto: c === "auto", light: c === "light", dark: c === "dark" };
  }

  function osIsDark() {
    return !!(root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function get() {
    var attr = document.documentElement.getAttribute("data-theme");
    return resolveTheme(attr, osIsDark());
  }

  function updateControlUI(choice) {
    var pressed = pressedStateFor(choice);
    var buttons = document.querySelectorAll(".theme-toggle .theme-btn");
    Array.prototype.forEach.call(buttons, function (b) {
      var key = b.getAttribute("data-theme-choice");
      b.setAttribute("aria-pressed", pressed[key] ? "true" : "false");
    });
  }

  function set(choice) {
    if (choice === "light" || choice === "dark") {
      document.documentElement.setAttribute("data-theme", choice);
      try { localStorage.setItem("theme", choice); } catch (e) {}
    } else {
      document.documentElement.removeAttribute("data-theme");
      try { localStorage.removeItem("theme"); } catch (e) {}
      choice = "auto";
    }
    updateControlUI(choice);
    document.dispatchEvent(new CustomEvent("sm:theme-changed"));
  }

  function initControl() {
    var buttons = document.querySelectorAll(".theme-toggle .theme-btn");
    if (!buttons.length) { return; }
    var stored = "auto";
    try { stored = localStorage.getItem("theme") || "auto"; } catch (e) {}
    updateControlUI(stored);
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener("click", function () {
        set(b.getAttribute("data-theme-choice"));
      });
    });
    // A reader on Auto whose OS flips needs the control's own highlighted
    // state to stay correct — it never changes (still "Auto"), but this is
    // also where a later task hooks the chart re-render for that case.
    if (root.matchMedia) {
      root.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        document.dispatchEvent(new CustomEvent("sm:theme-changed"));
      });
    }
  }

  // Colour substitution lands in Task 7 (recolor()); for now this is a
  // pass-through so every call site can switch to it with zero behaviour
  // change, verified before any colour logic is added.
  function smPlot(el, fig) {
    return Plotly.newPlot(el, fig.data, fig.layout, {responsive: true, displayModeBar: true});
  }

  var api = { resolveTheme: resolveTheme, get: get, set: set,
              initControl: initControl, pressedStateFor: pressedStateFor,
              smPlot: smPlot };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.SMTheme = api;
})(typeof window !== "undefined" ? window : this);
