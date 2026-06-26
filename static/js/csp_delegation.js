/*
 * csp_delegation.js — CSP-safe replacements for inline event-handler attributes.
 *
 * Loaded once on every base template. Because 'unsafe-inline' has been dropped
 * from script-src, inline on*= attributes (onclick, onchange, …) are blocked
 * under CSP enforcement and nonces never cover them. This file reproduces the
 * common inline-handler patterns via event delegation on `document`, so it also
 * covers HTMX-swapped content without re-binding.
 *
 * Supported data-* hooks:
 *   data-confirm="msg"        on <form>      → window.confirm() before submit
 *   data-confirm="msg"        on <a>/button  → window.confirm() before click/navigation
 *   data-action="print"                       → window.print()
 *   data-action="back"                        → history.back() (replaces href="javascript:")
 *   data-href="/url"                          → navigate to url on click
 *   data-click-target="id"                    → forward click to #id (e.g. hidden file input)
 *   data-autosubmit  (+ optional data-form="id") → submit the form on change
 *   data-check-nid="indicatorId"              → live national-id validation on input (window.checkNID)
 *   data-toggle-class="cls" data-toggle-target="id" → toggle cls (default "open") on #id
 *   data-toggle-hidden="id[,id2]"             → toggle .hidden on the id(s)
 *   data-rotate-chev                          → toggle .rotate-180 on this element's .chev child
 *   data-hide="id[,id2]"                      → add .hidden to the id(s)
 *   data-show="id[,id2]"                      → remove .hidden from the id(s)
 *   data-hide-self                            → add .hidden to the clicked element
 *   data-tab-pill                             → exclusive active state among .filter-pill buttons
 *   data-stop-propagation                     → stopPropagation() (element-bound, HTMX-aware)
 *   data-cn-insert  (on a button w/ data-med) → mousedown: dispatch 'cn-insert' CustomEvent({text})
 *   data-locked-tooltip="msg"                 → hover: fixed-position #lbb-tooltip showing msg
 *
 * Page-specific behaviour that doesn't fit these hooks stays in each page's own
 * nonce'd <script> (full pages) or its externalized static file (HTMX fragments).
 */
(function () {
  'use strict';

  function ids(val) {
    return (val || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function eachTarget(idList, fn) {
    ids(idList).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) { fn(el); }
    });
  }
  // Literal "\n" in a data-confirm attribute → real newline in the dialog.
  function confirmMsg(el) {
    return (el.getAttribute('data-confirm') || '').replace(/\\n/g, '\n');
  }

  // ---- click delegation -------------------------------------------------
  document.addEventListener('click', function (e) {
    // confirm() guard for links and type=button controls. Submit buttons are
    // handled by the form 'submit' listener below (data-confirm on the <form>).
    var confirmEl = e.target.closest('[data-confirm]');
    if (confirmEl && (confirmEl.tagName === 'A' || confirmEl.type === 'button')) {
      if (!window.confirm(confirmMsg(confirmEl))) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
    }

    // Exclusive active state among .filter-pill tab buttons.
    var pill = e.target.closest('[data-tab-pill]');
    if (pill) {
      document.querySelectorAll('.filter-pill').forEach(function (b) {
        b.classList.remove('filter-pill-active');
        b.setAttribute('aria-selected', 'false');
      });
      pill.classList.add('filter-pill-active');
      pill.setAttribute('aria-selected', 'true');
    }

    var el = e.target.closest(
      '[data-action],[data-href],[data-click-target],[data-toggle-class],' +
      '[data-toggle-hidden],[data-hide],[data-show],[data-hide-self],[data-rotate-chev]'
    );
    if (!el) { return; }

    if (el.getAttribute('data-action') === 'print') { window.print(); return; }

    if (el.getAttribute('data-action') === 'back') { e.preventDefault(); window.history.back(); return; }

    if (el.hasAttribute('data-href')) { window.location = el.getAttribute('data-href'); return; }

    if (el.hasAttribute('data-click-target')) {
      var proxied = document.getElementById(el.getAttribute('data-click-target'));
      if (proxied) { proxied.click(); }
      return;
    }

    if (el.hasAttribute('data-toggle-class')) {
      var cls = el.getAttribute('data-toggle-class') || 'open';
      var tgt = document.getElementById(el.getAttribute('data-toggle-target'));
      if (tgt) { tgt.classList.toggle(cls); }
    }
    if (el.hasAttribute('data-toggle-hidden')) {
      eachTarget(el.getAttribute('data-toggle-hidden'), function (t) { t.classList.toggle('hidden'); });
    }
    if (el.hasAttribute('data-rotate-chev')) {
      var chev = el.querySelector('.chev');
      if (chev) { chev.classList.toggle('rotate-180'); }
    }
    if (el.hasAttribute('data-hide')) {
      eachTarget(el.getAttribute('data-hide'), function (t) { t.classList.add('hidden'); });
    }
    if (el.hasAttribute('data-show')) {
      eachTarget(el.getAttribute('data-show'), function (t) { t.classList.remove('hidden'); });
    }
    if (el.hasAttribute('data-hide-self')) {
      el.classList.add('hidden');
    }
  });

  // ---- submit delegation (confirm) -------------------------------------
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form && form.matches && form.matches('form[data-confirm]')) {
      if (!window.confirm(confirmMsg(form))) {
        e.preventDefault();
      }
    }
  });

  // ---- change delegation (autosubmit) ----------------------------------
  document.addEventListener('change', function (e) {
    var el = e.target.closest('[data-autosubmit]');
    if (!el) { return; }
    var formId = el.getAttribute('data-form');
    var form = formId ? document.getElementById(formId) : el.closest('form');
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
  });

  // ---- input delegation (live national-id validation) ------------------
  document.addEventListener('input', function (e) {
    var el = e.target.closest('[data-check-nid]');
    if (el && typeof window.checkNID === 'function') {
      window.checkNID(el, el.getAttribute('data-check-nid'));
    }
  });

  // ---- data-stop-propagation (element-bound; works with HTMX swaps) ----
  function bindStop(root) {
    (root || document).querySelectorAll('[data-stop-propagation]:not([data-sp-bound])').forEach(function (el) {
      el.setAttribute('data-sp-bound', '');
      el.addEventListener('click', function (ev) { ev.stopPropagation(); });
    });
  }
  if (document.readyState !== 'loading') { bindStop(document); }
  else { document.addEventListener('DOMContentLoaded', function () { bindStop(document); }); }
  document.addEventListener('htmx:afterSwap', function (e) { bindStop(e.target); });
  document.addEventListener('htmx:load', function (e) { bindStop(e.target); });

  // ---- mousedown: "insert into notes" buttons (preserve editor focus) --
  document.addEventListener('mousedown', function (e) {
    var el = e.target.closest('[data-cn-insert]');
    if (!el) { return; }
    e.preventDefault();
    window.dispatchEvent(new CustomEvent('cn-insert', { detail: { text: el.getAttribute('data-med') || '' } }));
  });

  // ---- hover tooltip that escapes overflow-hidden parents (fixed pos) ---
  // Used by the "locked" book-appointment CTA. mouseover/mouseout bubble
  // (unlike mouseenter/leave) so they delegate; relatedTarget guards flicker.
  function lbbTooltip() {
    var t = document.getElementById('lbb-tooltip');
    if (!t) {
      t = document.createElement('div');
      t.id = 'lbb-tooltip';
      t.style.cssText = 'position:fixed;z-index:9999;background:#111827;color:#fff;font-size:0.75rem;padding:6px 12px;border-radius:6px;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.3);pointer-events:none;transition:opacity .15s;opacity:0;';
      document.body.appendChild(t);
    }
    return t;
  }
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest('[data-locked-tooltip]');
    if (!el || el.contains(e.relatedTarget)) { return; }
    var t = lbbTooltip();
    t.textContent = el.getAttribute('data-locked-tooltip') || '';
    var r = el.getBoundingClientRect();
    t.style.display = 'block';
    t.style.opacity = '0';
    var tw = t.offsetWidth;
    var left = r.left + r.width / 2 - tw / 2;
    if (left < 8) { left = 8; }
    if (left + tw > window.innerWidth - 8) { left = window.innerWidth - 8 - tw; }
    t.style.left = left + 'px';
    t.style.top = (r.top - t.offsetHeight - 8) + 'px';
    t.style.opacity = '1';
  });
  document.addEventListener('mouseout', function (e) {
    var el = e.target.closest('[data-locked-tooltip]');
    if (!el || el.contains(e.relatedTarget)) { return; }
    var t = document.getElementById('lbb-tooltip');
    if (t) { t.style.opacity = '0'; }
  });
})();
