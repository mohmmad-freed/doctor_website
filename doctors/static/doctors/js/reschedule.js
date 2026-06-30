/*
 * reschedule.js — CSP-safe slot selection for the doctor reschedule modal.
 *
 * The slot grid (doctors/partials/_reschedule_slots.html) is HTMX-swapped into
 * the modal, so an inline <script> there would lose its nonce on re-inject and
 * be blocked. Instead this file is loaded once on the host page
 * (appointment_detail.html) and delegates clicks on [data-reschedule-slot],
 * which keeps working across swaps. Replaces the old onclick="selectRescheduleSlot(this)".
 */
(function () {
  "use strict";

  // Active-slot ring/colour classes (mirror the prior inline handler exactly).
  var SELECTED = [
    "ring-2", "ring-indigo-500", "bg-indigo-50", "dark:bg-indigo-900/30",
    "text-indigo-700", "dark:text-indigo-300", "border-indigo-400"
  ];

  function isRtl() { return document.documentElement.dir === "rtl"; }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-reschedule-slot]");
    if (!btn) { return; }

    var grid = btn.closest("#reschedule-slot-grid") || document;
    grid.querySelectorAll("[data-reschedule-slot]").forEach(function (b) {
      b.classList.remove.apply(b.classList, SELECTED);
    });
    btn.classList.add.apply(btn.classList, SELECTED);

    var time = btn.getAttribute("data-time") || "";
    var hidden = document.getElementById("reschedule-time-hidden");
    if (hidden) { hidden.value = time; }

    var hint = document.getElementById("reschedule-slot-hint");
    var hintText = document.getElementById("reschedule-slot-hint-text");
    if (hint && hintText) {
      hintText.textContent = time + (isRtl() ? " تم الاختيار" : " selected");
      hint.classList.remove("hidden");
    }
  });
})();
