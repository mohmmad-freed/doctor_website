/* followup_slots.js — CSP-safe slot picker for the doctor "Schedule Follow-up" modal.
 *
 * Externalized from doctors/partials/schedule_followup_slots.html, which is an
 * HTMX-swapped fragment (its inline <script> + nonce would be stripped on
 * re-injection and blocked under CSP enforcement). Loaded once on the always-present
 * parent (doctors/patient_workspace.html).
 *
 * The slot grid lives inside an Alpine modal panel that uses @click.stop (so clicks
 * inside the panel don't dismiss the backdrop). That stopPropagation() means a
 * delegated document listener — bubble OR capture — is unreliable here. So we bind a
 * click listener directly on each .followup-slot-btn as it is swapped in: a listener
 * on the button itself fires in the target phase, before any ancestor's bubble-phase
 * @click.stop. This mirrors the original inline onclick="selectFollowupSlot(this)"
 * exactly. Binding is idempotent (data-fs-bound) and re-runs on htmx:afterSwap/load.
 */
(function () {
  'use strict';

  var SELECTED_CLASSES = [
    'ring-2', 'ring-indigo-500', 'bg-indigo-50', 'dark:bg-indigo-900/30',
    'text-indigo-700', 'dark:text-indigo-300', 'border-indigo-400'
  ];

  function selectSlot(btn) {
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
  }

  function bindSlots() {
    document.querySelectorAll('.followup-slot-btn:not([data-fs-bound])').forEach(function (btn) {
      btn.setAttribute('data-fs-bound', '');
      btn.addEventListener('click', function () { selectSlot(btn); });
    });
  }

  if (document.readyState !== 'loading') { bindSlots(); }
  else { document.addEventListener('DOMContentLoaded', bindSlots); }
  // Slots are swapped into #followup-slots via HTMX whenever the date/clinic/type
  // changes — re-bind any freshly-injected buttons.
  document.addEventListener('htmx:afterSwap', bindSlots);
  document.addEventListener('htmx:load', bindSlots);
})();
