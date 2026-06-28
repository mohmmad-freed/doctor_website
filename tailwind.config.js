/** @type {import('tailwindcss').Config} */
// CSP Phase 2b: compiled, self-hosted Tailwind (replaces the Tailwind Play CDN).
// Pinned to v3.4 (keeps the v3 config format + older-browser support; NOT v4).
//
// Per-portal color clash (doctor=indigo, secretary=purple, patient=blue all use the
// same primary-50..900 class names) is solved with a CSS-variable-driven palette:
// the channels are set per portal via :root in each base template. Each portal sets
// ONLY the shades its original inline config defined — shades left unset stay colorless,
// matching the pre-compile behavior exactly.
module.exports = {
  darkMode: 'class',
  content: [
    './*/templates/**/*.html', // every app's templates
    './*/static/**/*.js',      // app static JS (intake_form, followup_slots, ws_prescriptions, alpine_components…)
    './static/**/*.js',        // root static JS (csp_delegation.js, vendor)
  ],
  theme: {
    extend: {
      // Body font is still set per-base via the dir-based <style> blocks; this is the
      // fallback for the `font-sans` utility.
      fontFamily: { sans: ['Inter', 'Cairo', 'sans-serif'] },
      colors: {
        // CSS-variable-driven; space-separated RGB channels set per portal in :root.
        primary: {
          50:  'rgb(var(--color-primary-50)  / <alpha-value>)',
          100: 'rgb(var(--color-primary-100) / <alpha-value>)',
          200: 'rgb(var(--color-primary-200) / <alpha-value>)',
          300: 'rgb(var(--color-primary-300) / <alpha-value>)',
          400: 'rgb(var(--color-primary-400) / <alpha-value>)',
          500: 'rgb(var(--color-primary-500) / <alpha-value>)',
          600: 'rgb(var(--color-primary-600) / <alpha-value>)',
          700: 'rgb(var(--color-primary-700) / <alpha-value>)',
          800: 'rgb(var(--color-primary-800) / <alpha-value>)',
          900: 'rgb(var(--color-primary-900) / <alpha-value>)',
        },
        // accent (doctor/secretary) + secondary (patient) are the SAME teal everywhere → static.
        // Only the originally-defined shades {50,100,500,600}; other shades stay colorless as before.
        accent:    { 50: '#f0fdfa', 100: '#ccfbf1', 500: '#14b8a6', 600: '#0d9488' },
        secondary: { 50: '#f0fdfa', 100: '#ccfbf1', 500: '#14b8a6', 600: '#0d9488' },
      },
      // Waiting-room kiosk "Enter Now" badge.
      animation: { 'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite' },
    },
  },
  plugins: [],
};
