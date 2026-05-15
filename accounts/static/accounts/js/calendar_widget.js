/* Reusable inline calendar widget.
 *
 * Mounts on a `.cal-wrap` element with markup matching the structure used by
 * the secretary booking page (header w/ prev/next + Today, dow row, days grid,
 * legend, selected-date label). Writes the picked date as YYYY-MM-DD into a
 * hidden <input> and dispatches a 'change' event so HTMX or other listeners
 * can react. Working-days hatching is driven by an optional JSON endpoint.
 *
 * Usage:
 *   var cal = initCalendarWidget({
 *     rootId: 'cal-widget',
 *     inputId: 'appt-date-input',
 *     hintId: 'cal-hint',                       // optional
 *     workingDaysUrl: '/path/to/working-days/', // optional
 *     workingDaysParams: function() {            // optional, returns object or null
 *       return {doctor_id: 5};
 *     },
 *     fullDaysUrl: '/path/to/full-days/',        // optional
 *     fullDaysParams: function() {                // optional, returns object or null
 *       return {doctor_id: 5, clinic_id: 7, appointment_type_id: 3};
 *     },
 *     preserveInitialSelection: false,            // optional; when true a
 *       // prefilled date is NOT auto-cleared by working/full-day refreshes
 *     onInitialSelectionStale: function(reason) {}, // optional; called once
 *       // with 'day_off' | 'full' when the preserved date became invalid
 *     i18n: {
 *       isRtl: false,
 *       months: [...12 names...],
 *       fullDow: [...7 names, Sun first...],
 *       dowSep: ', ',
 *       dowPyNames: [...7 names, Mon first...], // for hint string
 *       pickDate: 'Pick a date',
 *       past: 'Past date',
 *       doctorOff: 'Doctor does not work this day',
 *       full: 'Fully booked',
 *       hintNoDoctor: 'Select a doctor first to see working days.',
 *       hintNoDays:   'This doctor has no working days.',
 *       hintWorks:    'Doctor works on: ',
 *     },
 *   });
 *
 * Returns: { setWorkingDays(days), refreshWorkingDays(), setFullDays(arr),
 *            refreshFullDays(), releaseInitialSelection(), getSelected() }.
 */
function initCalendarWidget(opts) {
  var root = document.getElementById(opts.rootId);
  if (!root) return null;

  var dateInput = document.getElementById(opts.inputId);
  var titleEl = root.querySelector('.cal-title');
  var daysEl = root.querySelector('[data-role="cal-days"]') || root.querySelector('.cal-grid:last-of-type');
  var prevBtn = root.querySelector('[data-role="cal-prev"]');
  var nextBtn = root.querySelector('[data-role="cal-next"]');
  var todayBtn = root.querySelector('[data-role="cal-today"]');
  var hintEl = opts.hintId ? document.getElementById(opts.hintId) : null;
  var selLabel = root.querySelector('[data-role="cal-selected-text"]');

  var i18n = opts.i18n || {};
  var MONTHS = i18n.months || ['January','February','March','April','May','June','July','August','September','October','November','December'];
  var FULL_DOW = i18n.fullDow || ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var DOW_SEP = i18n.dowSep || ', ';
  var DOW_PY = i18n.dowPyNames || ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  var T_PICK = i18n.pickDate || 'Pick a date';
  var T_PAST = i18n.past || 'Past date';
  var T_DOFF = i18n.doctorOff || 'Doctor does not work this day';
  var T_FULL = i18n.full || 'Fully booked';
  var T_HINT_NONE = i18n.hintNoDoctor || '';
  var T_HINT_NODAYS = i18n.hintNoDays || '';
  var T_HINT_WORKS = i18n.hintWorks || '';

  // When true, a prefilled date is treated as an authoritative initial
  // selection: working/full-day refreshes will NOT clear it (they notify via
  // onInitialSelectionStale instead). Released on first user pick or via
  // releaseInitialSelection(). Opt-in — default behaviour is unchanged.
  var onInitialStale = typeof opts.onInitialSelectionStale === 'function'
    ? opts.onInitialSelectionStale : null;
  var preserveInitial = false;
  var staleNotified = false;

  var todayStr = root.dataset.today; // YYYY-MM-DD
  var today;
  if (todayStr) {
    var tp = todayStr.split('-').map(Number);
    today = new Date(tp[0], tp[1] - 1, tp[2]);
  } else {
    var now = new Date();
    today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }
  today.setHours(0, 0, 0, 0);

  // Python weekday set: Mon=0..Sun=6. null = unknown → all days enabled.
  var workingDays = null;

  // Set of YYYY-MM-DD strings for which no slots are available for the
  // currently selected appointment type. Empty by default.
  var fullDays = Object.create(null);

  var viewYear = today.getFullYear();
  var viewMonth = today.getMonth();
  var selected = null;

  // Prefill: hidden input value or root[data-prefill]
  var prefill = (dateInput && dateInput.value) || root.dataset.prefill || '';
  if (prefill) {
    var p = prefill.split('-').map(Number);
    if (p.length === 3 && !isNaN(p[0])) {
      selected = new Date(p[0], p[1] - 1, p[2]);
      viewYear = selected.getFullYear();
      viewMonth = selected.getMonth();
      if (dateInput && !dateInput.value) dateInput.value = prefill;
      if (opts.preserveInitialSelection) preserveInitial = true;
    }
  }

  function notifyStale(reason) {
    if (staleNotified) return;
    staleNotified = true;
    if (onInitialStale) onInitialStale(reason);
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmt(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function pyWeekday(d) { return (d.getDay() + 6) % 7; }
  function isPast(d) {
    var x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    return x.getTime() < today.getTime();
  }

  function setSelectedLabel() {
    if (!selLabel) return;
    if (selected) {
      var dowName = FULL_DOW[selected.getDay()];
      selLabel.innerHTML = dowName + DOW_SEP + selected.getDate() + ' ' +
        MONTHS[selected.getMonth()] + ' ' + selected.getFullYear() +
        ' <strong>(' + fmt(selected) + ')</strong>';
    } else {
      selLabel.textContent = T_PICK;
    }
  }

  function render() {
    titleEl.textContent = MONTHS[viewMonth] + ' ' + viewYear;

    var lastOfPrev = new Date(viewYear, viewMonth, 0);
    if (prevBtn) prevBtn.disabled = lastOfPrev.getTime() < today.getTime();

    var firstOfMonth = new Date(viewYear, viewMonth, 1);
    var startOffset = firstOfMonth.getDay(); // Sun=0..Sat=6 — week starts Sunday
    var gridStart = new Date(viewYear, viewMonth, 1 - startOffset);

    daysEl.innerHTML = '';
    for (var i = 0; i < 42; i++) {
      var d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
      var inMonth = d.getMonth() === viewMonth;
      var past = isPast(d);
      var dowPy = pyWeekday(d);
      var nonWorking = workingDays !== null && workingDays.indexOf(dowPy) === -1;

      var cell = document.createElement('div');
      cell.className = 'cal-day';
      cell.textContent = d.getDate();
      cell.setAttribute('role', 'button');

      if (!inMonth) {
        cell.classList.add('is-other-month');
      } else if (past) {
        cell.classList.add('is-disabled');
        cell.title = T_PAST;
      } else if (nonWorking) {
        cell.classList.add('is-non-working');
        cell.title = T_DOFF;
      } else if (fullDays[fmt(d)]) {
        cell.classList.add('is-full');
        cell.title = T_FULL;
      } else {
        cell.dataset.date = fmt(d);
        cell.tabIndex = 0;
      }

      if (sameDay(d, today) && !selected) cell.classList.add('is-today');
      if (selected && sameDay(d, selected)) cell.classList.add('is-selected');

      daysEl.appendChild(cell);
    }
  }

  function pickDate(d) {
    preserveInitial = false;  // a deliberate user pick supersedes the initial selection
    selected = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    viewYear = selected.getFullYear();
    viewMonth = selected.getMonth();
    if (dateInput) dateInput.value = fmt(selected);
    setSelectedLabel();
    render();
    if (dateInput) dateInput.dispatchEvent(new Event('change', {bubbles: true}));
  }

  daysEl.addEventListener('click', function(e) {
    var cell = e.target.closest('.cal-day');
    if (!cell || !cell.dataset.date) return;
    var p = cell.dataset.date.split('-').map(Number);
    pickDate(new Date(p[0], p[1] - 1, p[2]));
  });

  if (prevBtn) prevBtn.addEventListener('click', function() {
    if (prevBtn.disabled) return;
    if (viewMonth === 0) { viewMonth = 11; viewYear--; } else { viewMonth--; }
    render();
    refreshFullDays();
  });
  if (nextBtn) nextBtn.addEventListener('click', function() {
    if (viewMonth === 11) { viewMonth = 0; viewYear++; } else { viewMonth++; }
    render();
    refreshFullDays();
  });
  if (todayBtn) todayBtn.addEventListener('click', function() {
    pickDate(today);
    refreshFullDays();
  });

  function setWorkingDays(days) {
    workingDays = days;
    if (hintEl) {
      if (days === null) {
        hintEl.textContent = T_HINT_NONE;
      } else if (days.length === 0) {
        hintEl.textContent = T_HINT_NODAYS;
      } else {
        var names = days.map(function(d) { return DOW_PY[d]; });
        hintEl.textContent = T_HINT_WORKS + names.join(DOW_SEP);
      }
    }
    if (selected && days !== null && days.indexOf(pyWeekday(selected)) === -1) {
      if (preserveInitial) {
        notifyStale('day_off');
      } else {
        selected = null;
        if (dateInput) dateInput.value = '';
        setSelectedLabel();
        if (dateInput) dateInput.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    render();
    refreshFullDays();
  }

  function refreshWorkingDays() {
    if (!opts.workingDaysUrl) return;
    var params = opts.workingDaysParams ? opts.workingDaysParams() : null;
    if (!params) {
      setWorkingDays(null);
      return;
    }
    var qs = Object.keys(params).map(function(k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&');
    var url = opts.workingDaysUrl + (opts.workingDaysUrl.indexOf('?') === -1 ? '?' : '&') + qs;
    fetch(url, {credentials: 'same-origin'})
      .then(function(r) { return r.ok ? r.json() : {working_days: []}; })
      .then(function(data) { setWorkingDays(data.working_days || []); })
      .catch(function() { setWorkingDays([]); });
  }

  function setFullDays(arr) {
    fullDays = Object.create(null);
    if (arr && arr.length) {
      for (var i = 0; i < arr.length; i++) fullDays[arr[i]] = true;
    }
    // If currently selected date became full, clear the selection.
    if (selected && fullDays[fmt(selected)]) {
      if (preserveInitial) {
        notifyStale('full');
      } else {
        selected = null;
        if (dateInput) dateInput.value = '';
        setSelectedLabel();
        if (dateInput) dateInput.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    render();
  }

  function refreshFullDays() {
    if (!opts.fullDaysUrl) return;
    var params = opts.fullDaysParams ? opts.fullDaysParams() : null;
    if (!params) {
      setFullDays([]);
      return;
    }
    var firstOfMonth = new Date(viewYear, viewMonth, 1);
    var startOffset = firstOfMonth.getDay();
    var gridStart = new Date(viewYear, viewMonth, 1 - startOffset);
    var gridEnd = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + 41);
    params.start = fmt(gridStart);
    params.end = fmt(gridEnd);

    var qs = Object.keys(params).map(function(k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&');
    var url = opts.fullDaysUrl + (opts.fullDaysUrl.indexOf('?') === -1 ? '?' : '&') + qs;
    fetch(url, {credentials: 'same-origin'})
      .then(function(r) { return r.ok ? r.json() : {full_days: []}; })
      .then(function(data) { setFullDays(data.full_days || []); })
      .catch(function() { setFullDays([]); });
  }

  setSelectedLabel();
  render();

  if (opts.workingDaysUrl) {
    refreshWorkingDays();
  } else if (opts.fullDaysUrl) {
    // No working-days dependency — fetch full-days directly.
    refreshFullDays();
  }

  return {
    setWorkingDays: setWorkingDays,
    refreshWorkingDays: refreshWorkingDays,
    setFullDays: setFullDays,
    refreshFullDays: refreshFullDays,
    releaseInitialSelection: function() { preserveInitial = false; },
    getSelected: function() { return selected ? fmt(selected) : ''; },
  };
}
