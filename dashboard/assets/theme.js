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

  // Keys that are known to carry colour. Deliberately NOT "every string in
  // the object" — a hex string appearing as hover text, a trace name, or any
  // other data field must survive untouched. See recolor's own tests for the
  // exact failure this restriction prevents.
  var COLOUR_KEYS = {
    color: true, bgcolor: true, bordercolor: true, gridcolor: true,
    zerolinecolor: true, paper_bgcolor: true, plot_bgcolor: true,
  };
  // Keys whose VALUE is itself an object worth recursing into, even though
  // the key name itself carries no colour (e.g. `line: {...}` isn't a
  // colour, but `line.color` is).
  var CONTAINER_KEYS = {
    line: true, marker: true, font: true, legend: true, title: true,
    xaxis: true, yaxis: true, yaxis2: true,
  };

  function _recolorValue(value, darkMap) {
    if (Array.isArray(value)) {
      return value.map(function (v) {
        return (typeof v === "string" && darkMap[v]) ? darkMap[v] : v;
      });
    }
    if (typeof value === "string") {
      return darkMap[value] || value;
    }
    return value;
  }

  function _walk(node, darkMap) {
    if (Array.isArray(node)) {
      return node.map(function (item) { return _walk(item, darkMap); });
    }
    if (!node || typeof node !== "object") { return node; }
    var out = {};
    Object.keys(node).forEach(function (key) {
      var value = node[key];
      if (COLOUR_KEYS[key]) {
        out[key] = _recolorValue(value, darkMap);
      } else if (CONTAINER_KEYS[key] && value && typeof value === "object") {
        out[key] = _walk(value, darkMap);
      } else if (key === "data" || key === "layout" || key === "shapes" || key === "annotations") {
        out[key] = _walk(value, darkMap);
      } else {
        out[key] = value;
      }
    });
    return out;
  }

  // Pure: fig unchanged if !isDark, else a NEW object (never mutates fig)
  // with every colour-bearing value substituted via darkMap. Values not
  // present in darkMap pass through unchanged — including Plotly built-in
  // colorscale names like "RdBu_r", which are strings but never colour hex.
  function recolor(fig, darkMap, isDark) {
    if (!isDark) { return fig; }
    return {
      data: _walk(fig.data, darkMap),
      layout: _walk(fig.layout, darkMap),
    };
  }

  function smPlot(el, fig) {
    var themed = recolor(fig, root.CHART_DARK || {}, get() === "dark");
    return Plotly.newPlot(el, themed.data, themed.layout, {responsive: true, displayModeBar: true});
  }

  var api = { resolveTheme: resolveTheme, get: get, set: set,
              initControl: initControl, pressedStateFor: pressedStateFor,
              smPlot: smPlot, recolor: recolor };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.SMTheme = api;
})(typeof window !== "undefined" ? window : this);
