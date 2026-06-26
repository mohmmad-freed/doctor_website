/* ortho_workspace.js — CSP-safe Alpine factories for the orthopedic exam widget.
 *
 * Externalized from the inline <script> blocks of the HTMX-swapped doctor-workspace
 * tabs (ws_overview.html + ws_notes.html). An inline <script> inside an HTMX-swapped
 * fragment loses its nonce on re-injection and is blocked under CSP enforcement, so
 * these factory definitions move here and load ONCE (eagerly, before Alpine) on the
 * always-present parent patient_workspace.html — the globals must exist when Alpine's
 * MutationObserver initializes a freshly-swapped tab.
 *
 * The two tabs defined slightly different orthoWorkspace() variants:
 *   - overview had generateSummary() and read #ortho-edit-init-overview
 *   - notes had objectiveText/quickPick/livePreview/applyToObjective + an initObjective
 *     arg and read #ortho-edit-init
 * This single definition is the UNION — every method either tab's markup calls is
 * present with identical behavior, and init() reads whichever init island the current
 * tab provides. orthoReadView() was identical in both. (Phase 2a converts these to
 * Alpine.data() to drop 'unsafe-eval'.)
 *
 * Region labels are localized (Arabic in RTL, English otherwise — matching the old
 * {% if IS_RTL %} blocks) using the <html dir> the base template already sets.
 */
(function () {
  'use strict';

  var RTL = document.documentElement.dir === 'rtl';
  function L(ar, en) { return RTL ? ar : en; }

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

  var mkFinding = function (id) {
    return {
      id: id, pain: '', tenderness: false, swelling: false, rom: '', notes: '',
      instability: false, locking: false, clicking: false, weight_bearing_diff: false,
      active_rom: '', passive_rom: '', weakness: false, impingement: false,
      stiffness: false, radiation: false, numbness: false, posture_notes: ''
    };
  };

  var PRESETS = {
    normal:      { pain: '0', tenderness: false, swelling: false, rom: 'Full range, painless', notes: 'No abnormality detected.' },
    tenderness:  { pain: '3', tenderness: true,  swelling: false, rom: 'Normal',               notes: 'Tenderness on palpation.' },
    swollen:     { pain: '4', tenderness: true,  swelling: true,  rom: 'Restricted',           notes: 'Swelling and tenderness present.' },
    painful_rom: { pain: '5', tenderness: false, swelling: false, rom: 'Painful and restricted', notes: 'Pain on ROM testing.' },
    post_trauma: { pain: '7', tenderness: true,  swelling: true,  rom: 'Severely restricted',  notes: 'Post-traumatic findings.' },
  };

  window.orthoWorkspace = function orthoWorkspace(initObjective) {
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
        const el = document.getElementById('ortho-edit-init') ||
                   document.getElementById('ortho-edit-init-overview');
        const data = el ? JSON.parse(el.textContent) : [];
        if (Array.isArray(data) && data.length) {
          data.forEach(f => {
            this.selected.push(f.id);
            this.findings[f.id] = { ...mkFinding(f.id), ...f };
          });
          this.activeId = this.selected[0] || null;
          this.open = true;
        }
        const form = this.$el.closest('form');
        if (form) {
          const _this = this;
          form.addEventListener('formdata', function (e) {
            e.formData.set('ortho_findings', _this.serialized);
          });
        }
      },

      toggleRegion(id) {
        if (this.selected.includes(id)) {
          this.selected = this.selected.filter(r => r !== id);
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

      get activeFinding() {
        if (!this.activeId) return null;
        if (!this.findings[this.activeId]) this.findings[this.activeId] = mkFinding(this.activeId);
        return this.findings[this.activeId];
      },

      getRegion(id) { return this.regions.find(r => r.id === id); },

      applyPreset(key) {
        if (!this.activeId || !PRESETS[key]) return;
        Object.assign(this.findings[this.activeId], PRESETS[key]);
      },

      isKnee(id)     { return id === 'r_knee' || id === 'l_knee'; },
      isShoulder(id) { return id === 'r_shoulder' || id === 'l_shoulder'; },
      isSpine(id)    { return id === 'cervical' || id === 'thoracolumbar'; },

      get livePreview() {
        if (!this.selected.length) return '';
        const lines = [];
        this.selected.forEach(id => {
          const f = this.findings[id];
          if (!f) return;
          const reg = this.getRegion(id);
          if (!reg) return;
          const parts = [reg.labelEn + ':'];
          if (f.pain !== '' && f.pain !== null) parts.push('Pain ' + f.pain + '/10');
          const signs = [];
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
        const preview = this.livePreview;
        if (preview) this.objectiveText = preview;
      },

      // Overview tab writes the generated summary straight into the form's
      // <textarea name="objective"> (it has no objectiveText x-model).
      generateSummary() {
        if (!this.selected.length) return;
        const lines = [];
        this.selected.forEach(id => {
          const f = this.findings[id]; if (!f) return;
          const reg = this.getRegion(id); if (!reg) return;
          const parts = [reg.labelEn + ':'];
          if (f.pain !== '' && f.pain !== null) parts.push('Pain ' + f.pain + '/10');
          const signs = [];
          if (f.tenderness) signs.push('tenderness'); if (f.swelling) signs.push('swelling');
          if (f.instability) signs.push('instability'); if (f.locking) signs.push('locking');
          if (f.clicking) signs.push('clicking'); if (f.weakness) signs.push('weakness');
          if (f.impingement) signs.push('impingement'); if (f.stiffness) signs.push('stiffness');
          if (f.radiation) signs.push('radicular symptoms'); if (f.numbness) signs.push('numbness/tingling');
          if (signs.length) parts.push(signs.join(', '));
          if (f.rom) parts.push('ROM: ' + f.rom); if (f.notes) parts.push(f.notes);
          lines.push(parts.join(' '));
        });
        const ta = this.$el.closest('form').querySelector('[name="objective"]');
        if (ta) ta.value = lines.join('\n');
      },

      get serialized() {
        if (!this.selected.length) return '[]';
        return JSON.stringify(this.selected.map(id => this.findings[id]).filter(Boolean));
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
  };

  window.orthoReadView = function orthoReadView(findingsJson) {
    const FRONT_IDS = ['cervical', 'r_shoulder', 'l_shoulder', 'r_elbow', 'l_elbow', 'r_wrist', 'l_wrist', 'r_hip', 'l_hip', 'r_knee', 'l_knee', 'r_ankle', 'l_ankle'];
    const BACK_IDS  = ['cervical', 'thoracolumbar', 'r_shoulder', 'l_shoulder', 'r_hip', 'l_hip', 'r_knee', 'l_knee', 'r_ankle', 'l_ankle'];
    return {
      findings: findingsJson || [],
      get selectedIds() { return this.findings.map(f => f.id); },
      hasFront() { return this.findings.some(f => FRONT_IDS.includes(f.id)); },
      hasBack() { return this.findings.some(f => BACK_IDS.includes(f.id)); },
      getColor(id) {
        const f = this.findings.find(x => x.id === id);
        if (!f) return '#e2e8f0';
        const pain = parseInt(f.pain) || 0;
        if (pain >= 8) return '#ef4444';
        if (pain >= 5) return '#f97316';
        return '#2dd4bf';
      },
      getStroke(id) {
        return this.selectedIds.includes(id) ? 'rgba(0,0,0,0.1)' : '#cbd5e1';
      }
    };
  };
})();
