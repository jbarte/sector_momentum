// Discreet motion for the dashboard (spec 2026-11-08 § 3). Every effect asks
// allowed() first, so a reader with Reduce Motion gets the end state at once.
// Only transform and opacity ever animate, so nothing re-lays out.
(function () {
  "use strict";

  function allowed() {
    return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // FLIP: record where each row is drawn, let mutate() reorder the DOM, then
  // animate each row from where it was to where it now is.
  function flip(rows, mutate) {
    if (!allowed()) { mutate(); return; }

    // Where each row is DRAWN now. A row still part-way through an earlier glide
    // is drawn away from its layout position, which is exactly where the next
    // glide has to start from.
    var before = new Map();
    rows.forEach(function (r) { before.set(r, r.getBoundingClientRect().top); });

    // Then drop any in-flight transform, so the reads after mutate() see layout
    // positions. Reading the drawn position as the "after" too would animate by
    // the wrong distance after a second sort in quick succession.
    rows.forEach(function (r) { r.style.transition = "none"; r.style.transform = ""; });
    mutate();

    var moved = [];
    rows.forEach(function (r) {
      var dy = before.get(r) - r.getBoundingClientRect().top;
      if (dy) {
        r.style.transition = "none";
        r.style.transform = "translateY(" + dy + "px)";
        moved.push(r);
      } else {
        r.style.transition = "";   // undo the "none" above on rows that stayed put
      }
    });
    if (!moved.length) { return; }

    // Force the inverted position to be computed. Setting it and clearing it in
    // the same frame, with no style recalculation between, shows the browser no
    // change and so no animation.
    void moved[0].offsetWidth;

    requestAnimationFrame(function () {
      moved.forEach(function (r) {
        r.style.transition = "transform var(--duration-base) var(--ease-glide)";
        r.style.transform = "";
        r.addEventListener("transitionend", function done(e) {
          // Bubbled from a descendant, or another property (a hover fade)?
          // Not ours: keep waiting for the glide itself.
          if (e && e.propertyName && e.propertyName !== "transform") { return; }
          if (e && e.target && e.target !== r) { return; }
          r.style.transition = "";
          r.removeEventListener("transitionend", done);
        });
      });
    });
  }

  // Re-trigger a one-shot CSS animation: removing and re-adding the same class
  // does not restart it on its own, hence the layout read in between (the same
  // trick as .rp-block-note.flash).
  function flash(el, cls) {
    cls = cls || "flash";
    if (!el || !allowed()) { return; }
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }

  // Slide an indicator to {x, w}. The first placement (and any after the
  // placed flag is cleared, as a resize does) and reduced motion jump.
  // opts.sizeByWidth sets the width in px and moves with translateX only: a
  // thumb with a radius and a shadow would be distorted by scaleX. A width
  // change jumps; only the position glides.
  function glideTo(ind, box, opts) {
    var first = !ind.dataset.placed;
    ind.style.transition = (first || !allowed()) ? "none"
      : "transform var(--duration-base) var(--ease-glide)";
    if (opts && opts.sizeByWidth) {
      ind.style.width = box.w + "px";
      ind.style.transform = "translateX(" + box.x + "px)";
    } else {
      ind.style.transform = "translateX(" + box.x + "px) scaleX(" + box.w + ")";
    }
    ind.dataset.placed = "1";
  }

  window.SMMotion = { allowed: allowed, flip: flip, flash: flash, glideTo: glideTo };
})();
