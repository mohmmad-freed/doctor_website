/* alpine_components.js — CSP-safe Alpine component registrations (Phase 2a).
 *
 * The self-hosted @alpinejs/csp build evaluates directive expressions with a
 * restricted parser and a scope that EXCLUDES window globals, so:
 *   - factory components must be registered via Alpine.data() (not window.foo());
 *   - any complex logic (multi-statement, optional chaining, arrow fns, JSON/
 *     document/setTimeout, template literals, DOM assignment) must live in a
 *     component METHOD/getter here — attributes stay simple calls/refs.
 *
 * Registered on 'alpine:init' (fires before Alpine starts). Loaded eagerly,
 * before Alpine, on every page that loads Alpine: patient_workspace.html,
 * order_catalog.html, patients_list.html.
 *
 * Localized labels are picked at runtime from <html dir> (RTL=ar) — removes the
 * {% if IS_RTL %} Django dependency, matching the prior ortho_workspace.js.
 */
(function () {
  'use strict';

  var RTL = document.documentElement.dir === 'rtl';
  function L(ar, en) { return RTL ? ar : en; }

  // ──────────────────────────────────────────────────────────────────────
  // Orthopedic exam — shared by ws_overview (overview tab) + ws_notes
  // ──────────────────────────────────────────────────────────────────────
  function buildRegions() {
    return [
      { id: 'cervical',      label: L('العمود الفقري العنقي', 'Cervical Spine'),            labelEn: 'Cervical Spine',        group: 'spine' },
      { id: 'r_shoulder',    label: L('الكتف الأيمن', 'Right Shoulder'),                    labelEn: 'Right Shoulder',        group: 'upper' },
      { id: 'l_shoulder',    label: L('الكتف الأيسر', 'Left Shoulder'),                     labelEn: 'Left Shoulder',         group: 'upper' },
      { id: 'r_elbow',       label: L('الكوع الأيمن', 'Right Elbow'),                       labelEn: 'Right Elbow',           group: 'upper' },
      { id: 'l_elbow',       label: L('الكوع الأيسر', 'Left Elbow'),                        labelEn: 'Left Elbow',            group: 'upper' },
      { id: 'r_wrist',       label: L('الرسغ/اليد اليمنى', 'Right Wrist/Hand'),             labelEn: 'Right Wrist/Hand',      group: 'upper' },
      { id: 'l_wrist',       label: L('الرسغ/اليد اليسرى', 'Left Wrist/Hand'),              labelEn: 'Left Wrist/Hand',       group: 'upper' },
      { id: 'thoracolumbar', label: L('العمود الفقري الصدري/القطني', 'Thoracic/Lumbar Spine'), labelEn: 'Thoracic/Lumbar Spine', group: 'spine' },
      { id: 'r_hip',         label: L('الورك الأيمن', 'Right Hip'),                         labelEn: 'Right Hip',             group: 'lower' },
      { id: 'l_hip',         label: L('الورك الأيسر', 'Left Hip'),                          labelEn: 'Left Hip',              group: 'lower' },
      { id: 'r_knee',        label: L('الركبة اليمنى', 'Right Knee'),                       labelEn: 'Right Knee',            group: 'lower' },
      { id: 'l_knee',        label: L('الركبة اليسرى', 'Left Knee'),                        labelEn: 'Left Knee',             group: 'lower' },
      { id: 'r_ankle',       label: L('الكاحل/القدم الأيمن', 'Right Ankle/Foot'),           labelEn: 'Right Ankle/Foot',      group: 'lower' },
      { id: 'l_ankle',       label: L('الكاحل/القدم الأيسر', 'Left Ankle/Foot'),            labelEn: 'Left Ankle/Foot',       group: 'lower' },
    ];
  }

  function mkFinding(id) {
    return {
      id: id, pain: '', tenderness: false, swelling: false, rom: '', notes: '',
      instability: false, locking: false, clicking: false, weight_bearing_diff: false,
      active_rom: '', passive_rom: '', weakness: false, impingement: false,
      stiffness: false, radiation: false, numbness: false, posture_notes: ''
    };
  }

  var PRESETS = {
    normal:      { pain: '0', tenderness: false, swelling: false, rom: 'Full range, painless', notes: 'No abnormality detected.' },
    tenderness:  { pain: '3', tenderness: true,  swelling: false, rom: 'Normal',               notes: 'Tenderness on palpation.' },
    swollen:     { pain: '4', tenderness: true,  swelling: true,  rom: 'Restricted',           notes: 'Swelling and tenderness present.' },
    painful_rom: { pain: '5', tenderness: false, swelling: false, rom: 'Painful and restricted', notes: 'Pain on ROM testing.' },
    post_trauma: { pain: '7', tenderness: true,  swelling: true,  rom: 'Severely restricted',  notes: 'Post-traumatic findings.' },
  };

  function orthoWorkspace(initObjective) {
    return {
      regions: buildRegions(),
      selected: [],
      activeId: null,
      findings: {},
      bodyView: 'front',
      open: false,
      objectiveText: initObjective || '',

      init() {
        // Read whichever init island the current tab provides (notes uses
        // #ortho-edit-init; overview uses #ortho-edit-init-overview).
        var el = document.getElementById('ortho-edit-init') ||
                 document.getElementById('ortho-edit-init-overview');
        var data = el ? JSON.parse(el.textContent) : [];
        if (Array.isArray(data) && data.length) {
          var self = this;
          data.forEach(function (f) {
            self.selected.push(f.id);
            self.findings[f.id] = Object.assign(mkFinding(f.id), f);
          });
          this.activeId = this.selected[0] || null;
          this.open = true;
        }
        var form = this.$el.closest('form');
        if (form) {
          var _this = this;
          form.addEventListener('formdata', function (e) {
            e.formData.set('ortho_findings', _this.serialized);
          });
        }
      },

      toggleRegion(id) {
        if (this.selected.includes(id)) {
          this.selected = this.selected.filter(function (r) { return r !== id; });
          delete this.findings[id];
          this.activeId = this.selected.length ? this.selected[this.selected.length - 1] : null;
        } else {
          this.selected.push(id);
          this.findings[id] = mkFinding(id);
          this.activeId = id;
        }
      },

      quickPick(id) {
        if (!this.selected.includes(id)) {
          this.selected.push(id);
          this.findings[id] = mkFinding(id);
        }
        this.activeId = id;
        this.open = true;
      },

      setActive(id) { if (this.selected.includes(id)) this.activeId = id; },

      // CSP-safe replacement for inline `getRegion(id)?.label || id`.
      regionLabel(id) {
        var r = this.getRegion(id);
        return (r && r.label) || id;
      },

      get activeFinding() {
        if (!this.activeId) return null;
        if (!this.findings[this.activeId]) this.findings[this.activeId] = mkFinding(this.activeId);
        return this.findings[this.activeId];
      },

      getRegion(id) { return this.regions.find(function (r) { return r.id === id; }); },

      applyPreset(key) {
        if (!this.activeId || !PRESETS[key]) return;
        Object.assign(this.findings[this.activeId], PRESETS[key]);
      },

      isKnee(id)     { return id === 'r_knee' || id === 'l_knee'; },
      isShoulder(id) { return id === 'r_shoulder' || id === 'l_shoulder'; },
      isSpine(id)    { return id === 'cervical' || id === 'thoracolumbar'; },

      get livePreview() {
        if (!this.selected.length) return '';
        var lines = [];
        var self = this;
        this.selected.forEach(function (id) {
          var f = self.findings[id];
          if (!f) return;
          var reg = self.getRegion(id);
          if (!reg) return;
          var parts = [reg.labelEn + ':'];
          if (f.pain !== '' && f.pain !== null) parts.push('Pain ' + f.pain + '/10');
          var signs = [];
          if (f.tenderness) signs.push('tenderness');
          if (f.swelling) signs.push('swelling');
          if (f.instability) signs.push('instability');
          if (f.locking) signs.push('locking');
          if (f.clicking) signs.push('clicking');
          if (f.weight_bearing_diff) signs.push('weight-bearing difficulty');
          if (f.weakness) signs.push('weakness');
          if (f.impingement) signs.push('impingement sign');
          if (f.stiffness) signs.push('stiffness');
          if (f.radiation) signs.push('radicular symptoms');
          if (f.numbness) signs.push('numbness/tingling');
          if (signs.length) parts.push(signs.join(', '));
          if (f.rom) parts.push('ROM: ' + f.rom);
          if (f.notes) parts.push(f.notes);
          lines.push(parts.join(' '));
        });
        return lines.join('\n');
      },

      applyToObjective() {
        var preview = this.livePreview;
        if (preview) this.objectiveText = preview;
      },

      // Overview tab writes the generated summary straight into the form's
      // <textarea name="objective"> (it has no objectiveText x-model).
      generateSummary() {
        if (!this.selected.length) return;
        var lines = [];
        var self = this;
        this.selected.forEach(function (id) {
          var f = self.findings[id]; if (!f) return;
          var reg = self.getRegion(id); if (!reg) return;
          var parts = [reg.labelEn + ':'];
          if (f.pain !== '' && f.pain !== null) parts.push('Pain ' + f.pain + '/10');
          var signs = [];
          if (f.tenderness) signs.push('tenderness'); if (f.swelling) signs.push('swelling');
          if (f.instability) signs.push('instability'); if (f.locking) signs.push('locking');
          if (f.clicking) signs.push('clicking'); if (f.weakness) signs.push('weakness');
          if (f.impingement) signs.push('impingement'); if (f.stiffness) signs.push('stiffness');
          if (f.radiation) signs.push('radicular symptoms'); if (f.numbness) signs.push('numbness/tingling');
          if (signs.length) parts.push(signs.join(', '));
          if (f.rom) parts.push('ROM: ' + f.rom); if (f.notes) parts.push(f.notes);
          lines.push(parts.join(' '));
        });
        var ta = this.$el.closest('form').querySelector('[name="objective"]');
        if (ta) ta.value = lines.join('\n');
      },

      get serialized() {
        if (!this.selected.length) return '[]';
        var self = this;
        return JSON.stringify(this.selected.map(function (id) { return self.findings[id]; }).filter(Boolean));
      },

      regionFill(id, base) {
        if (this.activeId === id) return '#0ea5e9';
        if (this.selected.includes(id)) return '#38bdf8';
        return base || '#e2e8f0';
      },
      regionStroke(id) {
        if (this.selected.includes(id)) return '#0369a1';
        return '#cbd5e1';
      },
      regionTextFill(id) {
        return this.selected.includes(id) ? '#fff' : '#64748b';
      }
    };
  }

  // Read-only ortho diagram. CSP build can't do x-data="orthoReadView(JSON.parse(..))"
  // (JSON is a global + inline expr), so findings are read from a per-instance
  // <script type="application/json" data-ortho-findings> child island in init().
  function orthoReadView() {
    var FRONT_IDS = ['cervical', 'r_shoulder', 'l_shoulder', 'r_elbow', 'l_elbow', 'r_wrist', 'l_wrist', 'r_hip', 'l_hip', 'r_knee', 'l_knee', 'r_ankle', 'l_ankle'];
    var BACK_IDS  = ['cervical', 'thoracolumbar', 'r_shoulder', 'l_shoulder', 'r_hip', 'l_hip', 'r_knee', 'l_knee', 'r_ankle', 'l_ankle'];
    return {
      findings: [],
      init() {
        var island = this.$el.querySelector('script[data-ortho-findings]');
        if (island) {
          try { this.findings = JSON.parse(island.textContent) || []; }
          catch (e) { this.findings = []; }
        }
      },
      get selectedIds() { return this.findings.map(function (f) { return f.id; }); },
      hasFront() { return this.findings.some(function (f) { return FRONT_IDS.includes(f.id); }); },
      hasBack() { return this.findings.some(function (f) { return BACK_IDS.includes(f.id); }); },
      getColor(id) {
        var f = this.findings.find(function (x) { return x.id === id; });
        if (!f) return '#e2e8f0';
        var pain = parseInt(f.pain) || 0;
        if (pain >= 8) return '#ef4444';
        if (pain >= 5) return '#f97316';
        return '#2dd4bf';
      },
      getStroke(id) {
        return this.selectedIds.includes(id) ? 'rgba(0,0,0,0.1)' : '#cbd5e1';
      }
    };
  }

  // ──────────────────────────────────────────────────────────────────────
  // Clinical Notes panel — Overview tab (was cnPanel() in patient_workspace.html)
  // ──────────────────────────────────────────────────────────────────────
  function cnPanel() {
    return {
      panelOpen: false,
      activeMode: null,
      activeNoteId: null,
      openTabs: [],
      cnActiveField: null,   // tracks the last-focused Clinical Notes textarea

      openNew() {
        this.activeMode = 'new';
        this.activeNoteId = null;
        this.cnActiveField = null;
        this.panelOpen = true;
      },

      openNote(noteId) {
        this.panelOpen = true;
        this.activeMode = 'view';
        this.cnActiveField = null;  // clear stale field ref when switching notes
        if (!this.openTabs.includes(noteId)) {
          this.openTabs.push(noteId);
        }
        this.activeNoteId = noteId;
      },

      // CSP-safe replacements for multi-statement inline @click handlers.
      activateNew() {
        this.activeMode = 'new';
        this.activeNoteId = null;
      },
      activateNote(noteId) {
        this.activeNoteId = noteId;
        this.activeMode = 'view';
      },

      // Shared reset used by closeTab / closeNewTab / closePanel
      _resetPanel() {
        this.panelOpen = false;
        this.activeMode = null;
        this.activeNoteId = null;
        this.cnActiveField = null;
      },

      closeTab(noteId) {
        this.openTabs = this.openTabs.filter(function (id) { return id !== noteId; });
        if (this.activeNoteId === noteId) {
          if (this.openTabs.length > 0) {
            this.activeNoteId = this.openTabs[this.openTabs.length - 1];
            this.activeMode = 'view';
          } else if (this.activeMode === 'view') {
            this._resetPanel();
          }
        }
      },

      closeNewTab() {
        if (this.openTabs.length > 0) {
          this.activeMode = 'view';
          this.activeNoteId = this.openTabs[this.openTabs.length - 1];
        } else {
          this._resetPanel();
        }
      },

      closePanel() {
        this._resetPanel();
        this.openTabs = [];
      },

      // ── Quick-insert (Prescriptions, Orders, Labs → Clinical Notes) ────────

      // Called via @focusin delegation on the panel content div
      cnTrackField(el) {
        if (el && el.tagName === 'TEXTAREA') this.cnActiveField = el;
      },

      // Called by the window 'cn-insert' event (dispatched by insert buttons)
      cnHandleInsert(medName) {
        if (!this.panelOpen || this.activeMode !== 'new') {
          // Panel not open in new-note mode — open it first, then append to Subjective
          this.openNew();
          var self = this;
          this.$nextTick(function () {
            var el = self.$root.querySelector('[name="subjective"]');
            if (!el) return;
            self.cnActiveField = el;
            el.focus();
            self._cnDoInsert(el, el.value.length, medName);
          });
          return;
        }
        // Panel already open — insert at last known cursor position
        var el = this.cnActiveField || this.$root.querySelector('[name="subjective"]');
        if (!el) return;
        this._cnDoInsert(el, el.selectionStart, medName);
      },

      // Inserts `text` into `el` at character position `pos`
      _cnDoInsert(el, pos, text) {
        var val    = el.value;
        var before = val.substring(0, pos);
        var after  = val.substring(pos);
        // Add a space separator if inserting mid-word or at end of non-whitespace
        var sep = (before.length > 0 && !/[\s\n]$/.test(before)) ? ' ' : '';
        el.value = before + sep + text + after;
        var newPos = pos + sep.length + text.length;
        el.setSelectionRange(newPos, newPos);
        el.focus();
        // Trigger 'input' so x-model bindings (if any) pick up the new value
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    };
  }

  // ──────────────────────────────────────────────────────────────────────
  // Add-note form collapse (ws_notes.html) — was an inline x-data="{ open }";
  // promoted to a component so the Cancel handler (multi-statement + $el DOM
  // call + typeof window global) can live in a method under the CSP build.
  // ──────────────────────────────────────────────────────────────────────
  function noteForm(initOpen) {
    return {
      open: !!initOpen,
      toggle() { this.open = !this.open; },
      cancelNote() {
        this.open = false;
        var form = this.$root.querySelector('form');
        if (form) form.reset();
        if (typeof window.wsDraftClearCurrent === 'function') window.wsDraftClearCurrent();
      }
    };
  }

  document.addEventListener('alpine:init', function () {
    Alpine.data('orthoWorkspace', orthoWorkspace);
    Alpine.data('orthoReadView', orthoReadView);
    Alpine.data('cnPanel', cnPanel);
    Alpine.data('noteForm', noteForm);
  });
})();
