/* appointments_panel.js — CSP-safe month/year picker for the clinic-owner
 * appointments panel.
 *
 * Externalized from clinics/partials/appointments_panel.html, which is rendered
 * standalone by clinics.views.appointments_panel_view and re-swapped (outerHTML)
 * on every month navigation — so its inline <script>+nonce would be stripped and
 * blocked under CSP enforcement. Loaded once on the always-present parent
 * (clinics/my_clinic.html). All behaviour is delegated on document and every
 * element/value is looked up fresh on each interaction, so it transparently
 * survives the panel's own re-swaps (no re-init hook needed).
 *
 * Runtime values come from the freshly-swapped fragment:
 *   #appointments-panel[data-panel-url|data-month|data-year]
 *   <script type="application/json" id="appt-picker-months">[…localized names…]</script>
 */
(function () {
  'use strict';

  var pickerYear = null;  // transient: the year currently shown in the open picker

  function panelData() {
    var p = document.getElementById('appointments-panel');
    if (!p) { return null; }
    return {
      url: p.dataset.panelUrl,
      month: parseInt(p.dataset.month, 10),
      year: parseInt(p.dataset.year, 10)
    };
  }

  function monthNames() {
    var el = document.getElementById('appt-picker-months');
    if (!el) { return []; }
    try { return JSON.parse(el.textContent); } catch (e) { return []; }
  }

  function renderPickerMonths() {
    var grid = document.getElementById('picker-months-grid');
    var d = panelData();
    if (!grid || !d) { return; }
    grid.innerHTML = '';
    monthNames().forEach(function (name, idx) {
      var m = idx + 1;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'picker-month-btn' +
        (m === d.month && pickerYear === d.year ? ' picker-month-active' : '');
      btn.textContent = name;
      btn.addEventListener('click', function () { navigate(m, pickerYear); });
      grid.appendChild(btn);
    });
    var label = document.getElementById('picker-year-label');
    if (label) { label.textContent = pickerYear; }
  }

  function closePicker() {
    var dropdown = document.getElementById('month-picker-dropdown');
    var btn = document.getElementById('month-picker-btn');
    if (dropdown) { dropdown.classList.remove('picker-visible'); }
    if (btn) { btn.classList.remove('picker-open'); }
  }

  function toggle() {
    var dropdown = document.getElementById('month-picker-dropdown');
    var btn = document.getElementById('month-picker-btn');
    var d = panelData();
    if (!dropdown || !btn || !d) { return; }
    if (dropdown.classList.contains('picker-visible')) {
      closePicker();
    } else {
      pickerYear = d.year;
      renderPickerMonths();
      dropdown.classList.add('picker-visible');
      btn.classList.add('picker-open');
    }
  }

  function changeYear(delta) {
    if (pickerYear === null) {
      var d = panelData();
      pickerYear = d ? d.year : new Date().getFullYear();
    }
    pickerYear += delta;
    renderPickerMonths();
  }

  function navigate(month, year) {
    var d = panelData();
    if (!d || typeof htmx === 'undefined') { return; }
    closePicker();
    htmx.ajax('GET', d.url + '?month=' + month + '&year=' + year, {
      target: '#appointments-panel',
      swap: 'outerHTML'
    });
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-appt-picker-toggle]')) { toggle(); return; }

    var yearBtn = e.target.closest('[data-appt-picker-year]');
    if (yearBtn) {
      changeYear(parseInt(yearBtn.getAttribute('data-appt-picker-year'), 10) || 0);
      return;
    }

    // Click anywhere outside the picker closes it.
    var wrap = document.getElementById('month-picker-wrap');
    if (wrap && !wrap.contains(e.target)) { closePicker(); }
  });
})();
