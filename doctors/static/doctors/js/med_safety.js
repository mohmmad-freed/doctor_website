/*
 * med_safety.js — CSP-safe medication-safety allergy scan.
 *
 * doctors/partials/_medication_safety_banner.html is included inside the
 * HTMX-swapped ws_orders / ws_prescriptions tab partials, so an inline <script>
 * there loses its nonce on re-inject and is blocked. This file is loaded once on
 * the host page (patient_workspace.html) and (re-)initialises every
 * [data-med-safety] banner on load and after each HTMX swap. The per-root
 * `_medSafetyInit` guard makes it idempotent.
 */
(function () {
  "use strict";

  function initBanner(root) {
    if (root._medSafetyInit) { return; }
    root._medSafetyInit = true;

    var textEl = root.querySelector("[data-allergy-text]");
    if (!textEl) { return; }  // no recorded allergies → nothing to match

    var tokens = textEl.textContent.toLowerCase()
      .split(/[,،;\/\n]/)
      .map(function (t) { return t.trim(); })
      .filter(function (t) { return t.length >= 3; });
    if (!tokens.length) { return; }

    var form = root.closest("form");
    if (!form) { return; }

    var hitsBox = root.querySelector("[data-allergy-hits]");
    var hitsList = root.querySelector("[data-allergy-hits-list]");

    function isMedField(el) {
      return el && el.name && (el.name.indexOf("med_name_") === 0 || el.name === "title");
    }
    function scan() {
      var hits = [];
      form.querySelectorAll('input[name^="med_name_"], input[name="title"]').forEach(function (inp) {
        var v = (inp.value || "").toLowerCase();
        var matched = v && tokens.some(function (t) { return v.indexOf(t) !== -1; });
        inp.classList.toggle("med-allergy-flag", !!matched);
        if (matched) { hits.push(inp.value.trim()); }
      });
      if (hits.length) {
        if (hitsList) { hitsList.textContent = hits.join(", "); }
        if (hitsBox) { hitsBox.classList.remove("hidden"); }
      } else if (hitsBox) {
        hitsBox.classList.add("hidden");
      }
    }

    form.addEventListener("input", function (e) { if (isMedField(e.target)) { scan(); } });
    scan();
  }

  function init() {
    document.querySelectorAll("[data-med-safety]").forEach(initBanner);
  }

  if (document.readyState !== "loading") { init(); }
  else { document.addEventListener("DOMContentLoaded", init); }
  document.addEventListener("htmx:afterSwap", init);
  document.addEventListener("htmx:load", init);
})();
