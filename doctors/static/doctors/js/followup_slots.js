/* followup_slots.js — CSP-safe slot picker for the doctor "schedule follow-up" modal.
 *
 * Externalized from doctors/partials/schedule_followup_slots.html, which is an
 * HTMX-swapped fragment (its inline <script> + nonce would be stripped on
 * re-injection and blocked under CSP enforcement). Loaded once on the always
 * present parent (doctors/patient_workspace.html). Click is delegated on
 * document so it keeps working across every slot-grid re-swap — no re-init hook
 * needed. Behaviour is identical to the old inline selectFollowupSlot().
 */
(function () {
  'use strict';

  var SELECTED_CLASSES = [
    'ring-2', 'ring-indigo-500', 'bg-indigo-50', 'dark:bg-indigo-900/30',
    'text-indigo-700', 'dark:text-indigo-300', 'border-indigo-400'
  ];

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.followup-slot-btn');
    if (!btn) { return; }

    // Deselect every slot, then select the clicked one.
    document.querySelectorAll('.followup-slot-btn').forEach(function (b) {
      SELECTED_CLASSES.forEach(function (c) { b.classList.remove(c); });
    });
    SELECTED_CLASSES.forEach(function (c) { btn.classList.add(c); });

    var t = btn.dataset.time;
    var hidden = document.getElementById('followup-time-hidden');
    if (hidden) { hidden.value = t; }

    var hint = document.getElementById('followup-slot-hint');
    var hintText = document.getElementById('followup-slot-hint-text');
    if (hint && hintText) {
      // Localized " selected" suffix is supplied by the fragment via data-*.
      hintText.textContent = t + (hint.getAttribute('data-selected-suffix') || '');
      hint.classList.remove('hidden');
    }
  });
})();
