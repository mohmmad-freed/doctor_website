/**
 * phone_validator.js
 *
 * Unified, real-time phone number validator for phone numbers.
 * Accepted formats: 05XXXXXXXX  /  +9705XXXXXXXX  / +9725XXXXXXXX
 *
 * Usage — just add the attribute to any phone <input>:
 *
 *   <input type="tel" data-phone-field ...>
 *
 * If an indicator element already exists in the markup, point to it:
 *
 *   <input type="tel" data-phone-field data-phone-indicator="myIndicatorId" ...>
 *   <div class="phone-indicator" id="myIndicatorId">
 *     <span class="dot"></span><span class="msg">...</span>
 *   </div>
 *
 * Otherwise a new .phone-indicator div is created automatically right after
 * the input (or after its closest .input-group wrapper, if one exists).
 */
(function () {
    'use strict';

    var HINT       = '05XXXXXXXX';
    var VALID_MSG  = 'صيغة صحيحة ✓';
    var BAD_PREFIX = 'يجب أن يبدأ بـ 05';

    /** Normalise to local 10-digit form, then return pure digits. */
    function normalise(raw) {
        var v = raw.replace(/\s/g, '');
        if (v.startsWith('+970') || v.startsWith('+972')) v = '0' + v.slice(4);
        else if (v.startsWith('970') || v.startsWith('972')) v = '0' + v.slice(3);
        return v.replace(/\D/g, '');
    }

    /** Find or create the indicator <div> for this input. */
    function getIndicator(input) {
        var id = input.getAttribute('data-phone-indicator');
        if (id) {
            var el = document.getElementById(id);
            if (el) return el;
        }

        var ind = document.createElement('div');
        ind.className = 'phone-indicator';
        ind.innerHTML = '<span class="dot"></span><span class="msg">' + HINT + '</span>';

        // Insert after .input-group wrapper if present, otherwise after the input itself
        var container = input.closest('.input-group');
        if (container) {
            container.insertAdjacentElement('afterend', ind);
        } else {
            input.insertAdjacentElement('afterend', ind);
        }
        return ind;
    }

    /** Update indicator state + any sibling .valid-icon/.invalid-icon elements. */
    function updateIndicator(ind, state, text) {
        ind.classList.remove('valid', 'invalid');
        if (state === 'valid')   ind.classList.add('valid');
        if (state === 'invalid') ind.classList.add('invalid');
        var msgEl = ind.querySelector('.msg');
        if (msgEl) msgEl.textContent = text;
    }

    function updateIcons(container, state) {
        if (!container) return;
        var ok  = container.querySelector('.valid-icon');
        var bad = container.querySelector('.invalid-icon');
        if (!ok && !bad) return;
        if (state === 'valid') {
            if (ok)  ok.style.display  = 'block';
            if (bad) bad.style.display = 'none';
        } else if (state === 'invalid') {
            if (ok)  ok.style.display  = 'none';
            if (bad) bad.style.display = 'block';
        } else {
            if (ok)  ok.style.display  = 'none';
            if (bad) bad.style.display = 'none';
        }
    }

    function validate(input, ind) {
        var raw    = input.value;
        var digits = normalise(raw);
        var iconContainer = input.closest('.input-group') || input.parentNode;

        if (!digits) {
            updateIndicator(ind, '', HINT);
            updateIcons(iconContainer, 'neutral');
            return;
        }

        // Wrong prefix (detectable after 2 digits)
        if (digits.length >= 2 && !digits.startsWith('05')) {
            updateIndicator(ind, 'invalid', BAD_PREFIX);
            updateIcons(iconContainer, 'invalid');
            return;
        }

        // Full valid number
        if (/^05\d{8}$/.test(digits)) {
            updateIndicator(ind, 'valid', VALID_MSG);
            updateIcons(iconContainer, 'valid');
            return;
        }

        // Correct prefix but incomplete
        updateIndicator(ind, 'invalid', digits.length + ' / 10 أرقام');
        updateIcons(iconContainer, 'neutral');
    }

    function initPhoneField(input) {
        // Strip non-digit characters as the user types (allow leading + for intl)
        input.addEventListener('input', function () {
            var pos = this.selectionStart;
            var cleaned = this.value.replace(/[^0-9+]/g, '');
            if (this.value !== cleaned) {
                this.value = cleaned;
                // Restore caret position
                try { this.setSelectionRange(pos, pos); } catch (e) {}
            }
            validate(this, ind);
        });

        var ind = getIndicator(input);

        // Run once on init if there's already a value (e.g. form re-display after error)
        if (input.value) validate(input, ind);
    }

    // Auto-initialise all [data-phone-field] elements when DOM is ready
    function autoInit() {
        document.querySelectorAll('[data-phone-field]').forEach(initPhoneField);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInit);
    } else {
        autoInit();
    }

    // Expose for manual init if needed
    window.initPhoneField = initPhoneField;
}());
