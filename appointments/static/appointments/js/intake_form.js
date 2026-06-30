/* intake_form.js — CSP-safe behaviour for the dynamic intake form.
 *
 * Externalized from two HTMX-swapped partials whose inline <script> + nonce would
 * be stripped on re-injection and blocked under CSP enforcement:
 *   - appointments/partials/intake_form.html        (mode "book")
 *   - patients/partials/edit_intake_form.html        (mode "edit")
 *
 * Loaded ONCE on the always-present parent page (book_appointment.html,
 * secretary appointments/create.html + walk_in.html, patients/edit_appointment.html).
 * Each partial emits a CSP-safe data island that carries the values that used to be
 * baked into the inline script ({% trans %} strings, the conditional-rules JSON and
 * the mode flag) — type="application/json" is never executed, so no nonce is needed:
 *
 *   <script type="application/json" id="intake-config">{ "mode", "rules", "dfw", "file" }</script>
 *
 * The two partials diverge in real ways (edit supports DATED_FILES pre-fill, uses a
 * different simple-FILE widget, and its conditional-rules engine differs in the IN
 * operator + reset behaviour). Both code paths are kept verbatim and selected by the
 * mode flag so the UI is byte-identical to the old inline versions. All inits are
 * idempotent and re-run on htmx:afterSwap / htmx:load, exactly like before.
 */
(function () {
  'use strict';

  function getConfig() {
    var el = document.getElementById('intake-config');
    if (!el) { return null; }
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // ── DATED_FILES widget (shared; edit additionally pre-fills existing groups) ──
  function initDFW(widget, t) {
    var qId = widget.dataset.questionId;
    var maxGroups = parseInt(widget.dataset.maxGroups) || 7;
    var maxPerGroup = parseInt(widget.dataset.maxPerGroup) || 5;
    var maxMb = widget.dataset.maxSizeMb ? parseFloat(widget.dataset.maxSizeMb) : null;
    var container = widget.querySelector('.dfw-groups');
    var addBtn = widget.querySelector('.dfw-add-btn');
    var countInput = widget.querySelector('.dfw-count');
    var groupCount = 0;
    var todayStr = new Date().toISOString().split('T')[0];

    function updateCount() { countInput.value = groupCount; }

    function addGroup(prefillDate, prefillFiles) {
      if (groupCount >= maxGroups) return;
      var gi = groupCount;
      groupCount++;
      updateCount();

      var div = document.createElement('div');
      div.className = 'dfw-group rounded-lg border border-gray-200 dark:border-slate-600 p-3 space-y-2 bg-white dark:bg-slate-700/30';
      div.dataset.groupIndex = gi;

      var acceptAttr = ' accept="image/*,.pdf"';
      var dateVal = prefillDate || '';

      // Existing-files info (edit only — booking never passes prefillFiles).
      var existingHtml = '';
      if (prefillFiles && prefillFiles.length) {
        existingHtml = '<div class="space-y-0.5 mb-1">';
        prefillFiles.forEach(function (f) {
          var sizeMB = (f.size / 1024 / 1024).toFixed(1);
          existingHtml += '<p class="text-xs text-green-600"><i class="fa-solid fa-file-check ml-1"></i>' + f.name + ' (' + sizeMB + ' MB)</p>';
        });
        existingHtml += '<p class="text-xs text-amber-500"><i class="fa-solid fa-info-circle ml-1"></i>' + t.replaceNote + '</p></div>';
      }

      div.innerHTML =
        '<div class="flex items-center justify-between gap-2">' +
          '<div class="flex items-center gap-2 flex-1">' +
            '<i class="fa-solid fa-calendar-day text-primary-400 text-sm"></i>' +
            '<input type="date" name="intake_dfile_date_' + qId + '_g' + gi + '" value="' + dateVal + '" required max="' + todayStr + '" ' +
              'class="flex-1 px-3 py-2 rounded-lg border border-gray-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-primary-500 text-sm">' +
          '</div>' +
          '<button type="button" class="dfw-remove-btn text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg p-2 transition-colors" title="' + t.deleteGroup + '">' +
            '<i class="fa-solid fa-trash-can text-sm"></i>' +
          '</button>' +
        '</div>' +
        existingHtml +
        '<label class="dfw-dropzone flex flex-col items-center justify-center w-full py-4 border-2 border-dashed border-gray-300 dark:border-slate-500 rounded-lg cursor-pointer hover:border-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/10 transition-colors bg-gray-50 dark:bg-slate-800/50">' +
          '<i class="fa-solid fa-cloud-arrow-up text-gray-400 dark:text-slate-400 text-2xl mb-2"></i>' +
          '<span class="text-sm font-medium text-gray-600 dark:text-gray-300">' + t.dragFiles + '</span>' +
          '<span class="text-xs text-gray-400 mt-1">' + t.maxFilesOf + ' ' + maxPerGroup + ' ' + t.filesUnit + '</span>' +
          '<input type="file" name="intake_dfile_' + qId + '_g' + gi + '" class="hidden" multiple' + acceptAttr + '>' +
        '</label>' +
        '<div class="dfw-file-feedback mt-2 space-y-2"></div>';

      container.appendChild(div);

      div.querySelector('.dfw-remove-btn').addEventListener('click', function () {
        div.remove();
        rebuildIndices();
      });

      var fileInput = div.querySelector('input[type="file"]');
      var feedback = div.querySelector('.dfw-file-feedback');
      var dropzone = div.querySelector('.dfw-dropzone');

      dropzone.addEventListener('dragover', function (e) { e.preventDefault(); dropzone.classList.add('border-primary-500', 'bg-primary-50', 'dark:bg-primary-900/20'); });
      dropzone.addEventListener('dragleave', function (e) { e.preventDefault(); dropzone.classList.remove('border-primary-500', 'bg-primary-50', 'dark:bg-primary-900/20'); });
      dropzone.addEventListener('drop', function (e) {
        e.preventDefault();
        dropzone.classList.remove('border-primary-500', 'bg-primary-50', 'dark:bg-primary-900/20');
        if (e.dataTransfer.files.length) {
          fileInput.files = e.dataTransfer.files;
          fileInput.dispatchEvent(new Event('change'));
        }
      });

      fileInput.addEventListener('change', function () {
        feedback.innerHTML = '';
        var valid = true;
        var fileArr = Array.from(fileInput.files);
        if (fileArr.length > maxPerGroup) {
          feedback.innerHTML = '<div class="p-2 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg text-sm flex items-center gap-2"><i class="fa-solid fa-circle-exclamation"></i>' + t.maxFilesOf + ' ' + maxPerGroup + ' ' + t.maxFilesSimple + '</div>';
          fileInput.value = '';
          dropzone.classList.add('border-red-400');
          return;
        }

        var filesHtml = '<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">';
        fileArr.forEach(function (f) {
          var isPdf = f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf';
          var isImage = f.type.startsWith('image/');
          var sizeMB = (f.size / 1024 / 1024).toFixed(1);
          var icon = isPdf ? 'fa-file-pdf text-red-500' : (isImage ? 'fa-file-image text-blue-500' : 'fa-file text-gray-500');

          var fileError = '';
          if (!isPdf && !isImage) {
            fileError = t.unsupported;
            valid = false;
          } else if (maxMb && f.size > maxMb * 1024 * 1024) {
            fileError = t.fileTooLarge;
            valid = false;
          }

          if (fileError) {
            filesHtml += '<div class="flex items-center gap-2 p-2 rounded-lg border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-900/20"><i class="fa-solid fa-circle-exclamation text-red-500 shadow-sm shrink-0"></i><div class="flex-1 min-w-0"><p class="text-xs text-red-600 dark:text-red-400 truncate" dir="ltr" style="text-align: right;">' + f.name + '</p><p class="text-[10px] text-red-500">' + fileError + '</p></div></div>';
          } else {
            filesHtml += '<div class="flex items-center gap-2 p-2 rounded-lg border border-gray-200 dark:border-slate-600 bg-white dark:bg-slate-700 shadow-sm"><i class="fa-solid ' + icon + ' text-lg shrink-0"></i><div class="flex-1 min-w-0"><p class="text-xs text-gray-700 dark:text-gray-300 truncate font-medium" dir="ltr" style="text-align: right;">' + f.name + '</p><p class="text-[10px] text-gray-500">' + sizeMB + ' MB</p></div></div>';
          }
        });
        filesHtml += '</div>';

        feedback.innerHTML = filesHtml;

        if (!valid) {
          fileInput.value = '';
          dropzone.classList.add('border-red-400');
          dropzone.classList.remove('border-green-400', 'border-gray-300', 'dark:border-slate-500');
        } else if (fileArr.length > 0) {
          dropzone.classList.add('border-green-400');
          dropzone.classList.remove('border-red-400', 'border-gray-300', 'dark:border-slate-500');
        } else {
          dropzone.classList.remove('border-green-400', 'border-red-400');
          dropzone.classList.add('border-gray-300', 'dark:border-slate-500');
        }
      });

      if (groupCount >= maxGroups) addBtn.style.display = 'none';
    }

    function rebuildIndices() {
      var groups = container.querySelectorAll('.dfw-group');
      groupCount = groups.length;
      updateCount();
      groups.forEach(function (g, i) {
        g.dataset.groupIndex = i;
        var dateInput = g.querySelector('input[type="date"]');
        if (dateInput) dateInput.name = 'intake_dfile_date_' + qId + '_g' + i;
        var fileInput = g.querySelector('input[type="file"]');
        if (fileInput) fileInput.name = 'intake_dfile_' + qId + '_g' + i;
      });
      addBtn.style.display = groupCount >= maxGroups ? 'none' : '';
    }

    addBtn.addEventListener('click', function () { addGroup('', null); });

    // Pre-fill existing groups (edit only — booking has no data-prefill).
    var prefillAttr = widget.dataset.prefill;
    if (prefillAttr) {
      try {
        JSON.parse(prefillAttr).forEach(function (g) { addGroup(g.date || '', g.files || null); });
      } catch (e) { /* ignore parse errors */ }
    }
  }

  function initAllDFW(t) {
    document.querySelectorAll('.dated-files-widget').forEach(function (w) {
      if (!w.dataset.dfwInit) {
        w.dataset.dfwInit = '1';
        initDFW(w, t);
      }
    });
  }

  // ── Simple FILE field (mode "book": .file-upload-container, delegated once) ──
  function initSimpleFileBooking() {
    if (window.__intakeFileHandlerInit) { return; }
    window.__intakeFileHandlerInit = true;
    document.addEventListener('change', function (e) {
      var input = e.target;
      if (!input || input.type !== 'file') return;
      var container = input.closest('.file-upload-container');
      if (!container) return;

      var cfg = getConfig();
      var tf = (cfg && cfg.file) || {};

      var qId = container.dataset.questionId;
      var maxMb = container.dataset.maxSizeMb ? parseFloat(container.dataset.maxSizeMb) : null;
      var allowedRaw = container.dataset.allowedExt || '';
      var allowed = allowedRaw ? allowedRaw.split(',').map(function (x) { return x.trim().toLowerCase().replace(/^\./, ''); }) : [];

      var infoDiv = document.getElementById('file-info-' + qId);
      if (!infoDiv) return;
      var successDiv = infoDiv.querySelector('.file-success');
      var errorDiv = infoDiv.querySelector('.file-error');
      var nameSpan = infoDiv.querySelector('.file-name');
      var sizeSpan = infoDiv.querySelector('.file-size');
      var errorMsg = infoDiv.querySelector('.error-msg');
      var label = container.querySelector('label');

      infoDiv.classList.add('hidden');
      if (successDiv) successDiv.classList.add('hidden');
      if (errorDiv) errorDiv.classList.add('hidden');
      if (label) label.classList.remove('border-red-400', 'dark:border-red-500', 'border-green-400', 'dark:border-green-500');

      if (!input.files || !input.files.length) return;

      var files = Array.prototype.slice.call(input.files);
      var errors = [];
      var totalSize = 0;
      files.forEach(function (file) {
        totalSize += file.size;
        if (allowed.length > 0) {
          var ext = file.name.indexOf('.') >= 0 ? file.name.split('.').pop().toLowerCase() : '';
          if (allowed.indexOf(ext) === -1) {
            errors.push(tf.invalidFormat + ' (' + file.name + '). ' + tf.allowed + ' ' + allowed.join(', '));
          }
        }
        if (maxMb && file.size > maxMb * 1024 * 1024) {
          errors.push(tf.fileTooLarge + ' (' + file.name + ')');
        }
      });

      infoDiv.classList.remove('hidden');
      if (errors.length > 0) {
        if (errorMsg) errorMsg.textContent = errors.join(' · ');
        if (errorDiv) errorDiv.classList.remove('hidden');
        if (label) label.classList.add('border-red-400', 'dark:border-red-500');
        input.value = '';
      } else {
        var sizeTxt = totalSize < 1024 * 1024
          ? (totalSize / 1024).toFixed(0) + ' KB'
          : (totalSize / (1024 * 1024)).toFixed(1) + ' MB';
        if (nameSpan) nameSpan.textContent = files[0].name + (files.length > 1 ? ' (+' + (files.length - 1) + ')' : '');
        if (sizeSpan) sizeSpan.textContent = '(' + sizeTxt + ')';
        if (successDiv) successDiv.classList.remove('hidden');
        if (label) label.classList.add('border-green-400', 'dark:border-green-500');
      }
    });
  }

  // ── Simple FILE field (mode "edit": .multi-file-upload, per-widget init) ──
  function initMFU(w) {
    var input = w.querySelector('input[type="file"]');
    var feedback = w.querySelector('.file-feedback');
    var dropzone = w.querySelector('.dropzone');
    var maxSize = parseFloat(w.dataset.maxSize || 10);
    var extensions = w.dataset.extensions ? w.dataset.extensions.split(',').map(function (e) { return e.trim().toLowerCase(); }) : [];

    if (!input) return;

    input.addEventListener('change', function () {
      feedback.innerHTML = '';
      var valid = true;
      Array.from(input.files).forEach(function (f) {
        var ext = f.name.split('.').pop().toLowerCase();
        var sizeMB = (f.size / 1024 / 1024).toFixed(1);
        if (extensions.length && extensions.indexOf(ext) < 0) {
          feedback.innerHTML += '<p class="text-xs text-red-500"><i class="fa-solid fa-xmark ml-1"></i>صيغة غير مسموحة: .' + ext + '</p>';
          valid = false;
        } else if (f.size > maxSize * 1024 * 1024) {
          feedback.innerHTML += '<p class="text-xs text-red-500"><i class="fa-solid fa-xmark ml-1"></i>' + f.name + ' (' + sizeMB + ' MB) يتجاوز الحد</p>';
          valid = false;
        } else {
          feedback.innerHTML += '<p class="text-xs text-green-600"><i class="fa-solid fa-check ml-1"></i>' + f.name + ' (' + sizeMB + ' MB)</p>';
        }
      });
      if (!valid) {
        input.value = '';
        dropzone.classList.add('border-red-400');
        dropzone.classList.remove('border-green-400');
      } else if (input.files.length) {
        dropzone.classList.add('border-green-400');
        dropzone.classList.remove('border-red-400');
      }
    });
  }

  function initAllMFU() {
    document.querySelectorAll('.multi-file-upload').forEach(function (w) {
      if (!w.dataset.mfuInit) {
        w.dataset.mfuInit = '1';
        initMFU(w);
      }
    });
  }

  // ── Conditional rules (mode-specific evaluate semantics, kept verbatim) ──
  function initRules(mode, rules) {
    var c = document.getElementById('intakeFormFields');
    if (!c || !rules || !rules.length) { return; }
    if (c.dataset.rulesInit) { return; }   // a freshly-swapped #intakeFormFields has no flag
    c.dataset.rulesInit = '1';

    var showTargets = new Set();
    rules.forEach(function (r) { if (r.action === 'SHOW') showTargets.add(r.target_question_id); });
    showTargets.forEach(function (qId) {
      var el = document.getElementById('question-' + qId);
      if (el) el.style.display = 'none';
    });

    function getVal(qId) {
      var r = document.querySelector('input[name="intake_' + qId + '"]:checked');
      if (r) return r.value;
      var s = document.querySelector('select[name="intake_' + qId + '"]');
      if (s) return s.value;
      var t = document.querySelector('[name="intake_' + qId + '"]');
      if (t && t.type !== 'checkbox' && t.type !== 'radio') return t.value || '';
      var chks = document.querySelectorAll('input[name="intake_' + qId + '"]:checked');
      return chks.length ? Array.from(chks).map(function (c2) { return c2.value; }) : '';
    }

    function evaluate() {
      if (mode === 'book') {
        showTargets.forEach(function (qId) {
          var el = document.getElementById('question-' + qId);
          if (el) el.style.display = 'none';
        });
        rules.forEach(function (r) {
          var v = getVal(r.source_question_id), match = false;
          if (r.operator === 'EQUALS') match = v === r.expected_value;
          else if (r.operator === 'NOT_EQUALS') match = v !== r.expected_value;
          else if (r.operator === 'CONTAINS') match = Array.isArray(v) ? v.indexOf(r.expected_value) >= 0 : (v || '').indexOf(r.expected_value) >= 0;
          else if (r.operator === 'IN') match = Array.isArray(v) ? v.indexOf(r.expected_value) >= 0 : (v || '').indexOf(r.expected_value) >= 0;
          var tgt = document.getElementById('question-' + r.target_question_id);
          if (!tgt) return;
          if (match) { if (r.action === 'SHOW') tgt.style.display = ''; else tgt.style.display = 'none'; }
        });
      } else {
        rules.forEach(function (r) {
          var v = getVal(r.source_question_id), match = false;
          if (r.operator === 'EQUALS') match = v === r.expected_value;
          else if (r.operator === 'NOT_EQUALS') match = v !== r.expected_value;
          else if (r.operator === 'CONTAINS') match = Array.isArray(v) ? v.indexOf(r.expected_value) >= 0 : (v || '').indexOf(r.expected_value) >= 0;
          else if (r.operator === 'IN') match = Array.isArray(v) ? r.expected_value.split(',').some(function (x) { return v.indexOf(x.trim()) >= 0; }) : r.expected_value.split(',').indexOf(v) >= 0;
          if (match) {
            var el = document.getElementById('question-' + r.target_question_id);
            if (el) el.style.display = (r.action === 'SHOW') ? '' : 'none';
          }
        });
      }
    }

    c.addEventListener('change', evaluate);
    if (mode === 'book') { c.addEventListener('input', evaluate); }
    evaluate();
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────
  function boot() {
    var cfg = getConfig();
    if (cfg && cfg.dfw) { initAllDFW(cfg.dfw); }
    initSimpleFileBooking();   // delegated + window-guarded; no-ops without .file-upload-container
    initAllMFU();              // no-ops without .multi-file-upload
    if (cfg) { initRules(cfg.mode, cfg.rules || []); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
  document.addEventListener('htmx:afterSwap', boot);
  document.addEventListener('htmx:load', boot);
})();
