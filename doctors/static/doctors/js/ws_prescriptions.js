/* ws_prescriptions.js — CSP-safe drag-to-(de)activate for the prescriptions tab.
 *
 * Externalized from the inline <script> in doctors/partials/ws_prescriptions.html
 * (HTMX-swapped into #tab-content — its nonce would be stripped on re-injection and
 * blocked under CSP enforcement). Loaded once on patient_workspace.html. Re-inits on
 * htmx:afterSwap into #tab-content, exactly like the old inline version. The two
 * localized empty-state messages now come from data-empty-msg on each list element.
 */
(function () {
  'use strict';

  function getCsrf() {
    var f = document.querySelector('#rx-csrf-form [name=csrfmiddlewaretoken]');
    return f ? f.value : '';
  }

  function removeEmptyState(list) {
    list.querySelectorAll('.rx-empty-state').forEach(function (el) { el.remove(); });
  }

  function addEmptyStateIfNeeded(list, message) {
    if (list.querySelectorAll('[data-rx-id]').length === 0) {
      var el = document.createElement('div');
      el.className = 'rx-empty-state flex items-center justify-center h-14 text-sm text-slate-400 dark:text-slate-500 italic select-none';
      el.textContent = message;
      list.appendChild(el);
    }
  }

  function initSortable() {
    var activeList = document.getElementById('active-rx-list');
    var inactiveList = document.getElementById('inactive-rx-list');
    if (!activeList || !inactiveList || typeof Sortable === 'undefined') { return; }

    var csrf = getCsrf();
    var sharedOpts = {
      group: 'rx-prescriptions',
      animation: 150,
      handle: '.rx-drag-handle',
      ghostClass: 'opacity-40',
      onAdd: function (evt) {
        var rxId = evt.item.dataset.rxId;
        var toggleUrl = evt.item.dataset.toggleUrl;
        if (!rxId || !toggleUrl) { return; }

        removeEmptyState(evt.to);
        addEmptyStateIfNeeded(evt.from, evt.from.dataset.emptyMsg || '');

        // Update bullet dot colors immediately to reflect new status.
        var isNowActive = evt.to.dataset.list === 'active';
        evt.item.querySelectorAll('.rx-med-dot').forEach(function (dot) {
          if (isNowActive) {
            dot.classList.remove('bg-slate-400', 'dark:bg-slate-500');
            dot.classList.add('bg-emerald-500');
          } else {
            dot.classList.remove('bg-emerald-500');
            dot.classList.add('bg-slate-400', 'dark:bg-slate-500');
          }
        });

        fetch(toggleUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf } });
      }
    };

    // Guard so each freshly-swapped list element only gets one Sortable instance.
    [activeList, inactiveList].forEach(function (list) {
      if (list.dataset.sortableInit) { return; }
      list.dataset.sortableInit = '1';
      Sortable.create(list, sharedOpts);
    });
  }

  if (document.readyState !== 'loading') { initSortable(); }
  else { document.addEventListener('DOMContentLoaded', initSortable); }

  // Re-init after any HTMX swap into the workspace tab container.
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'tab-content') {
      initSortable();
    }
  });
})();
