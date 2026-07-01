# Clink Design System — "Vital"

> [!IMPORTANT]
> This is the **single source of truth** for Clink's interface. It replaces `UI_BRANDING.md` ("Modern Trust").
> Scope: **all five actors** (patient, doctor, secretary, owner, admin) × **two targets** (mobile web ≤640px, PC web ≥1024px) × **two themes** (light/dark) × **two languages** (Arabic RTL — default, English LTR).
> Live reference pages: `Start Here.html` → `Foundations.html` → `Patterns & States.html` → `Vital.html` (applied screens) → `Interactive.html` (working demo). Keep this file and those pages in sync.

**Last updated:** 2026-07-02 · **Status:** Approved

---

## 0. Principles

1. **Clinical precision** — crisp 1px structure, tabular data, intentional hierarchy. No decoration without purpose.
2. **Mobile-first** — design at ≤640px first; desktop is the enhancement.
3. **Arabic-first** — copy is written in Arabic and mirrored to English, not the reverse.
4. **Role-appropriate density** — staff portals (doctor/secretary/owner/admin) are dense; patient/guest surfaces are airy and calm.
5. **Token-only styling** — every color, radius, and shadow comes from a token. If it isn't a token, it doesn't ship.
6. **Safety patterns are law** — the medication-safety banner and status colors are mandatory and may never be restyled ad-hoc.

---

## 1. Design tokens

### 1.1 CSS custom properties (copy verbatim)

```css
:root {
  /* Brand */
  --color-primary:       #0F54E0;  /* blue 500 — actions, links, active nav */
  --color-primary-hover: #0A40AD;  /* blue 600 — hover/press */
  --color-primary-soft:  #EAF1FF;  /* blue 50  — selected bg, icon chips */
  --color-accent:        #0FB5AE;  /* teal — secondary accent */
  --color-accent-deep:   #0B928C;
  --color-accent-soft:   #D6F5F2;

  /* Semantic */
  --color-success: #16A34A;  --color-success-soft: #E7F6EC;
  --color-warning: #D97706;  --color-warning-soft: #FCF1DF;  /* text on soft: #B45309 */
  --color-danger:  #DC2626;  --color-danger-soft:  #FBE9E9;

  /* Surfaces & text (light) */
  --bg:     #F6F8FA;  /* app background */
  --panel:  #FFFFFF;  /* cards, sidebars, tables */
  --panel2: #F1F4F8;  /* inset surfaces, hover rows, segmented track */
  --border: #E1E7EE;  /* card & input borders */
  --line:   #EDF1F5;  /* hairline row dividers */
  --text:   #0B1220;  /* primary text (slate 900) */
  --muted:  #6B7888;  /* secondary text */
  --faint:  #A6B0BE;  /* placeholders, disabled */

  /* Geometry */
  --radius-sm: 6px;  --radius-md: 8px;  --radius-lg: 10px;  --radius-xl: 14px;  --radius-pill: 999px;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-6: 24px; --space-8: 32px; --space-12: 48px;

  /* Elevation */
  --shadow-sm: 0 1px 2px rgba(16,24,40,.06);
  --shadow-md: 0 8px 24px -12px rgba(16,24,40,.22);
  --shadow-lg: 0 18px 44px -18px rgba(16,24,40,.30);

  /* Type */
  --font-sans: 'IBM Plex Sans', system-ui, sans-serif;  /* Latin */
  --font-ar:   'Cairo', sans-serif;                      /* Arabic */
  --font-mono: 'IBM Plex Mono', monospace;               /* data, numerics, code */

  /* Motion */
  --ease: cubic-bezier(.2, .7, .3, 1);
  --dur-micro: 120ms;   /* press, toggle, hover */
  --dur-surface: 180ms; /* menu, modal, toast enter/exit */
  --dur-page: 200ms;    /* theme switch, page transition */
}

[data-theme="dark"] {
  --bg:     #0B1220;  --panel:  #131C2B;  --panel2: #18233A;
  --border: #212E44;  --line:   #1A2436;
  --text:   #EAF0F8;  --muted:  #8A98AE;  --faint:  #5E6E86;

  --color-primary:      #4F8DFF;                    /* lighter for contrast on dark */
  --color-primary-soft: rgba(79,141,255,.16);
  --color-accent:       #2DD4C4;  --color-accent-soft: rgba(45,212,196,.14);
  --color-success: #34D07F;  --color-success-soft: rgba(52,208,127,.14);
  --color-warning: #F0B24B;  --color-warning-soft: rgba(240,178,75,.16);
  --color-danger:  #FF6B6B;  --color-danger-soft:  rgba(255,107,107,.14);

  --shadow-sm: 0 1px 2px rgba(0,0,0,.40);
  --shadow-md: 0 8px 24px -12px rgba(0,0,0,.50);
  --shadow-lg: 0 18px 44px -18px rgba(0,0,0,.60);
}
```

### 1.2 Full color ramps

- **Blue (primary):** 50 `#EAF1FF` · 100 `#D3E2FF` · 200 `#A9C7FF` · 300 `#6BA2FF` · 400 `#1A6DFF` · **500 `#0F54E0` (base)** · 600 `#0A40AD` · 700 `#072F82`
- **Teal (accent):** soft `#D6F5F2` · base `#0FB5AE` · deep `#0B928C`
- **Slate (neutrals):** `#0B1220` · `#3A4658` · `#6B7888` · `#8A96A6` · `#A6B0BE` · `#CBD4DF` · `#E1E7EE` · `#EDF1F5` · `#F6F8FA`
- **Brand gradient** (logo/avatar chips only, never large surfaces): `linear-gradient(135deg, #1A6DFF, #0FB5AE)`

### 1.3 Tailwind integration

```js
// tailwind.config.js
module.exports = {
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: { extend: {
    colors: {
      primary: 'var(--color-primary)',
      'primary-soft': 'var(--color-primary-soft)',
      accent: 'var(--color-accent)',
      bg: 'var(--bg)', panel: 'var(--panel)', panel2: 'var(--panel2)',
      line: 'var(--line)', muted: 'var(--muted)', faint: 'var(--faint)',
      success: 'var(--color-success)', warning: 'var(--color-warning)', danger: 'var(--color-danger)',
    },
    borderColor: { DEFAULT: 'var(--border)' },
    borderRadius: { sm:'6px', md:'8px', lg:'10px', xl:'14px' },
    fontFamily: {
      sans: ['IBM Plex Sans','system-ui','sans-serif'],
      ar:   ['Cairo','sans-serif'],
      mono: ['IBM Plex Mono','monospace'],
    },
  }},
};
```

Theme switching sets `data-theme="dark"` on `<html>` (persist in `localStorage`, honor `prefers-color-scheme` on first visit). **Never hardcode a hex in a template.**

---

## 2. Typography

| Style   | Font            | Size/Weight | Notes                                   |
|---------|-----------------|-------------|-----------------------------------------|
| Display | Plex Sans/Cairo | 40 / 700    | letter-spacing −.03em (Latin only)      |
| H1      | Plex Sans/Cairo | 32 / 700    | −.025em                                 |
| H2      | Plex Sans/Cairo | 24 / 600    | −.015em                                 |
| Body    | Plex Sans/Cairo | 16 / 400    | line-height 1.6 (1.7 for Arabic)        |
| Small   | Plex Sans/Cairo | 13 / 500    | secondary/helper text                   |
| Caption | Plex Mono       | 11 / 600    | UPPERCASE, letter-spacing .1em — labels |
| Data    | Plex Mono       | any / 500–600 | `font-variant-numeric: tabular-nums`  |

Rules:
- Arabic text always renders in **Cairo** (swap the stack by `dir`/lang, not per-element).
- All numbers users compare (prices, times, vitals, counts) use **Plex Mono + tabular-nums**.
- Currency is **₪ (ILS)**, mono, e.g. `₪4,280` / `₪150`.
- Never apply Latin negative letter-spacing to Arabic.
- Minimum rendered size 11px; body never below 13px on mobile.

---

## 3. Iconography

- Style: **Lucide-class line icons** — stroke `1.8`–`2`, `stroke-linecap="round"`, `stroke-linejoin="round"`, `fill="none"`, color via `currentColor` (inherits theme automatically).
- Sizes: **16** inline with text · **18** default UI · **20** nav · **24** touch/mobile.
- Directional icons (chevrons, arrows, back, send) **must mirror in RTL** — render them inside the RTL flow or flip explicitly.
- No emoji in product UI. No Font Awesome (replaced).

---

## 4. Layout & breakpoints

| Target      | Range    | Shell                                             | Gutters | Grid |
|-------------|----------|---------------------------------------------------|---------|------|
| Mobile web  | ≤ 640px  | Top app bar + **bottom tab bar** (4 + "More")     | 16px    | single column |
| PC web      | ≥ 1024px | Persistent **sidebar** 220–236px + content ≤1180px | 24px    | 12-col fluid |

- Between 640–1024: content reflows fluidly; sidebar collapses to the mobile pattern.
- **Nav rule:** the desktop sidebar's primary destinations become the mobile bottom tab bar; overflow lives in a grouped **"More" sheet** that mirrors the desktop section grouping. The raised center button is the role's primary quick action (patient: book; doctor: new note; secretary: new booking).
- Dense staff portals may use the **two-tier rail** (74px domain rail + 204–212px context panel) — secretary/owner/admin.
- **RTL:** use logical properties only (`inset-inline-start`, `margin-inline-end`, `padding-inline`, `ms-*/me-*` in Tailwind). The sidebar, rail, and app bar mirror automatically. Never `left`/`right` for layout.
- Touch targets **≥ 44px** on mobile; ≥ 36px + 8px spacing on desktop.

---

## 5. Components (core library)

Reference renders: `Foundations.html` § Components. Key specs:

- **Buttons** — heights: sm 36 / md 44 / lg 50; radius 9–11px; weight 600.
  Variants: Primary (primary bg, white text, shadow-sm), Secondary (panel bg, 1px border), Ghost (transparent, primary text), Danger (danger bg). Disabled: `--faint` bg, no shadow, `cursor:not-allowed`.
- **Inputs** — 44px height, radius 9px, 1px `--border`, bg `--bg`; label 11.5/500 `--muted` above.
  Focus: 1.5px `--color-primary` border + `0 0 0 3px var(--color-primary-soft)` ring. Error: same but danger + helper line 11/500 danger.
- **Checkbox/radio** — 18px; checked = primary fill + white glyph. Toggle — 42×24 pill, 20px knob. Segmented — `--panel2` track, active segment primary bg white text.
- **Status pills** — 999px radius, soft bg + solid dot + 600 text (see §7 for the fixed status colors).
- **Cards / KPI cards** — panel bg, 1px border, radius 11–14px, shadow-sm; KPI: icon chip (32–34px, soft bg) + label 11.5/600 muted + value 26/700 mono-tabular + delta 11/600 mono (▲ success / ▼ context-dependent).
- **Tables** — header row: 9.5/600 mono UPPERCASE `--faint`; rows 44–48px, `--line` dividers; hover `--panel2`; time/number columns mono-tabular.
- **Nav items** — 9px radius; active = `--color-primary-soft` bg + primary text + 3px start-edge bar; badge counts ride the end edge (mono, 999px).
- **Dropdown/menu** — panel, radius 11px, shadow-md; destructive actions in danger, separated by `--line`.
- **Tabs** — 2px underline on active (primary); Segmented for 2–3 options.
- **Modal** — max-w 368–420px, radius 16–18px, shadow-lg, scrim `rgba(8,12,20,.42)`; title 15/700, actions row: secondary + primary, destructive confirm uses Danger.
- **Toast** — dark surface (`--text` bg, `--bg` text) + status icon chip, top-center (mobile) / end-bottom (desktop), auto-dismiss 3–4s. **Alert/banner** — soft semantic bg + 1px 30% border, persists.
- **Chips** — selected: primary bg white text + ✕; unselected: `--panel2` + border; add: dashed border.
- **Avatars** — radius 10–12px (squircle, not circle); gradient brand fill for self, `--panel2` for others; presence dot 10–12px success + 2px panel ring; stacks overlap −10px.
- **Steps/progress** — 26px numbered circles joined by 2px lines (done = primary + check); linear bar 8px pill.
- **Tooltip** — `--text` bg, `--bg` text, 11/600, radius 8px + caret. **Badge** — min-w 18px, danger bg, mono 10/700, 2px panel ring.
- **Date picker** — day pills 46×~56 (MON/14 stacked), selected = primary bg + shadow. **Time slots** — grid of 38–42px mono cells; selected = 1.5px primary border + soft bg; unavailable = line-through `--faint`.
- **Search/⌘K** — 44px field with search icon + `⌘K` kbd chip; staff portals expose command-palette jump.
- **Skeleton** — `--panel2`→`--line` shimmer, 1.25s linear infinite; block radius 7px.
- **Empty state** — 52px icon chip + 13.5/600 title + 12/400 muted line + primary CTA, centered.
- **Breadcrumb** — 12/500 muted, chevron separators (mirror in RTL), current 600 text. **Pagination** — 32px squares, active primary.

---

## 6. Motion

| Tier      | Duration | Use                              |
|-----------|----------|----------------------------------|
| Micro     | 120ms    | press, hover, toggle             |
| Surface   | 180ms    | menu/modal/toast enter-exit (fade + 7px translate + .985 scale) |
| Page/theme| 200ms    | theme swap, route transitions    |

Easing: `cubic-bezier(.2,.7,.3,1)`. Card hover lift: `translateY(-3px)` + shadow-md, 160ms.
Honor `prefers-reduced-motion: reduce` — collapse all of the above to opacity-only.

---

## 7. Appointment status system *(BUSINESS_RULES R-08)*

One fixed color per status, identical on every surface (doctor table, patient card, secretary queue):

| Status | Arabic | Color pair |
|---|---|---|
| `PENDING` | قيد الانتظار | warning / warning-soft |
| `CONFIRMED` | مؤكد | primary / primary-soft |
| `CHECKED_IN` | تم الوصول | accent / accent-soft |
| `IN_PROGRESS` | قيد الكشف | primary / primary-soft + **pulsing dot** |
| `COMPLETED` | مكتمل | success / success-soft + check icon |
| `CANCELLED` | ملغى | danger / danger-soft + ✕ icon |
| `NO_SHOW` | لم يحضر | muted / panel2 + 1px border |

**Doctor transition map (R-08a, backend-enforced — UI must only offer these):**
`PENDING → CONFIRMED → CHECKED_IN → IN_PROGRESS → COMPLETED`;
`PENDING|CONFIRMED → CANCELLED` (notifies patient); `CONFIRMED → NO_SHOW`.
Invalid transitions are silently ignored server-side — never render them as options.

---

## 8. Feedback & message catalog

Four variants map 1:1 to Django message tags — fixed icon + color, **Arabic copy only** from the catalog (`README/ERROR_MESSAGES.md`), never hardcoded:

- `success` → check-circle, success pair — e.g. تم إرسال الدعوة بنجاح.
- `error` → x-circle, danger pair — e.g. لا يمكن حجز هذا الموعد بسبب وجود تضارب.
- `warning` → triangle-alert, warning pair — e.g. لا توجد عيادة مرتبطة بحسابك.
- `info` → info-circle, primary pair — e.g. يرجى تسجيل الدخول للتحقق من بريدك.

Toasts auto-dismiss; inline banners persist until resolved. Representative catalog keys: `INVALID_PHONE`, `DOCTOR_NOT_VERIFIED`, `DAILY_INVITE_LIMIT`, `INVITATION_ACCEPTED`, `SESSION_EXPIRED` — the full authoritative list lives in `README/ERROR_MESSAGES.md`.

---

## 9. Doctor verification & onboarding states *(DOCTOR_STATES.md)*

Dashboard banner per state; each says what's blocked + the next action:

| State | Look | Rules |
|---|---|---|
| `PENDING_REVIEW` قيد المراجعة | warning banner, clock icon | can set availability; not visible/bookable |
| `VERIFIED` موثّق | success banner, check | public + bookable (if clinic active) |
| `REJECTED` مرفوض | danger banner | **must show admin's rejection reason inline**; re-upload restarts review |
| `REVOKED` موقوف | neutral/muted banner | hidden from search, not bookable; admin-reversible |

Second layer: per-clinic credential (`ClinicDoctorCredential`: `PENDING / VERIFIED / REJECTED`) uses the same visual language for the clinic-credential badge.

---

## 10. Medication safety & allergy banner — **MANDATORY**

> [!WARNING]
> Shown at prescribe/order time. This pattern may **never** be restyled, reduced, or skipped.

1. **Recorded allergies** — danger-soft card, triangle icon, allergy list in 600 text.
2. **Live match** — if a typed drug matches a recorded allergy: danger line with the drug name underlined (e.g. Amoxicillin ↔ Penicillin).
3. **Active medications** — neutral card of chips (name + dose, mono-friendly).
4. **Acknowledgement** — warning-soft checkbox row, required before submit:
   *"راجعتُ حساسية المريض والأدوية الفعّالة قبل الوصف. / I reviewed the patient's allergies and active medications before prescribing."*
5. **Empty state** — success-soft: "لا توجد حساسية مسجّلة. / No allergies recorded for this patient."

---

## 11. Contextual warnings — by actor

Same alert component (§8), role-specific copy:

- **Patient:** can't cancel/edit within **2 hours** of the slot (R-10) · reschedule limited to **2 edits** then cancel-and-rebook (R-09) · compliance block after repeated no-shows.
- **Doctor:** "لا توجد عيادة مرتبطة بحسابك" (no clinic linked) · allergy acknowledgement (§10) · not-verified = not visible (§9).
- **Secretary:** outstanding-debt warning on patient select · conflicting-appointments warning when blocking time · stale-date warning on re-opened edit forms.
- **Owner:** subscription-expiry warning · plan-cap reached ("max doctors/secretaries", R-26).
- **Admin:** review queue badge for `PENDING_REVIEW` doctors · moderation actions confirm via Danger modal.

---

## 12. Loading *(LOADING_INDICATORS.md)*

- **Heartbeat overlay** — blocking form submits. Pulsing heart (1.2s beat) inside a soft halo + **two staggered expanding rings** (1.8s, .9s offset) + **ECG waveform** with traveling pulse (1.6s linear) + "جارٍ الحفظ…" with animated dots. Includes a **double-submit guard** (disable + flag on first submit).
- **Skeleton reveal** — page transitions: 3px top progress bar (blue→teal gradient, 2s ease) + shimmering skeleton blocks (§5) in the real layout's shape.
- Use skeletons for content areas, spinners only inside buttons (16px, 2px stroke).

---

## 13. System & error pages

Branded, bilingual, standalone (no nav/DB dependency). **400 / 403 / 404 / 500 already exist** (`clinic_website/errors.py`) — restyle to this spec:

| Page | Icon chip | Copy (EN / AR) | CTA |
|---|---|---|---|
| 404 | primary-soft compass | Page not found / الصفحة غير موجودة | Back to home · العودة |
| 403 | warning-soft lock | Not authorized / لا تملك صلاحية الوصول | Switch account · تبديل |
| 500 | danger-soft triangle | Something went wrong / حدث خطأ ما | Retry · إعادة المحاولة |
| 400 | reuses 404 layout with its own copy | | |
| OFFLINE | muted wifi-off | No connection / لا يوجد اتصال بالإنترنت | Retry · إعادة المحاولة |

No separate **401** — unauthenticated → redirect to login; 403 covers "signed in but not permitted."
Layout: centered, 46px icon chip, big mono code, EN title + AR subtitle, one-line explanation, single CTA.

### 13.1 Offline — build spec *(the one open gap)*

- **DETECT** — `online`/`offline` events + `navigator.onLine`, confirmed by a tiny `/health` ping (`cache:'no-store'`) with exponential backoff 2→30s (events lie on captive Wi-Fi).
- **GUARD** — while offline: disable submits, **queue** in-flight writes (e.g. a booking) instead of failing; reuse the Heartbeat double-submit flag.
- **RESUME** — on confirmed reconnect: flush queue → hide banner → success toast "عاد الاتصال" (auto-dismiss 2s). Never silently drop a queued action.
- **SCOPE** — in-app loss → dismissible top banner (warning-soft, spinner, "لا يوجد اتصال بالإنترنت — سنعيد الاتصال تلقائياً" + retry link). Hard navigation with no network → full OFFLINE page (requires a service worker caching the app shell + that page; optional PWA step).

```js
addEventListener('offline', showBanner);
addEventListener('online',  reconnect);
if (!navigator.onLine) showBanner();

const ping = () => fetch('/health', {cache:'no-store'}).then(() => true).catch(() => false);

async function reconnect() {            // confirm, then resume
  if (await ping()) { await flushQueue(); hideBanner(); toast('عاد الاتصال'); }
}
```

---

## 14. Accessibility

- **Contrast:** body text ≥ 4.5:1 in both themes (token pairs are pre-checked AA).
- **Touch:** ≥ 44px mobile, ≥ 36px desktop.
- **Focus:** visible ring on every interactive element — 1.5px primary border + 3px primary-soft halo. Never `outline: none` without replacement.
- **Motion:** honor `prefers-reduced-motion`.
- Status is never color-only — pills pair color with a dot/icon + label.
- Toasts: `role="status"`; blocking errors: `role="alert"`. Modals trap focus, `Esc` closes, focus returns to the trigger.

---

## 15. Global switching (account · language · theme)

The **avatar menu** (top end corner, desktop; profile tab sheet, mobile) hosts:
1. **Switch account type** — list of the user's roles with a check on the active one; switching swaps shell + nav live.
2. **Language** — ع / EN segmented toggle; flips `dir`, font stack, and mirrors layout instantly.
3. **Theme** — Light/Dark segmented toggle; sets `data-theme`, persists.
Plus Settings and Log out. Quick single-tap toggles for language + theme may also sit in the top bar (globe pill + sun/moon), as in `Interactive.html`.

---

## 16. Governance — how to change anything

1. **Token first** — add/confirm the value in §1 (both themes) and in `Foundations.html`.
2. **Component** — build it from tokens only; add it to the Foundations component section.
3. **Patterns** — document its states (default/hover/focus/disabled/error/loading/empty) in `Patterns & States.html` and here.
4. **Applied screen** — show it in context in `Vital.html` (or the Interactive demo if behavioral).

PR checklist: tokens only · Arabic copy from catalog · logical properties (RTL-tested with ع) · both themes tested · ≥44px targets · statuses/feedback reuse §7–§8 · reduced-motion respected.

---

## Appendix — file map

| File | Role |
|---|---|
| `DESIGN_SYSTEM.md` | this document — canonical written spec |
| `Clink - Start Here.dc.html` / `offline/Start Here.html` | front door + developer handoff |
| `Clink - Foundations.dc.html` / `offline/Foundations.html` | tokens, type, icons, breakpoints, components, motion, a11y |
| `Clink - Patterns.dc.html` / `offline/Patterns & States.html` | product layer: §7–§13 rendered live |
| `Clink - Vital.dc.html` / `offline/Vital.html` | applied screens (home, guest, patient, doctor, secretary × desktop + mobile) |
| `Clink - Interactive.dc.html` / `offline/Interactive.html` | working demo: role/language/theme switching + booking flow |
| `Clink Design System.dc.html` | archive — original three-direction exploration |
