# UI Brand Guidelines: Modern Trust

> [!IMPORTANT]
> This document defines the official visual identity for the project.  
> **Concept Selected:** Modern Trust (Clean, Corporate Medical, High Structure)

## 1. Brand Essence
- **Core Values:** Professionalism, Reliability, Clarity, Trust.
- **Visual Style:** Minimalist, clean lines, ample whitespace, medical-grade precision.
- **Primary Interface:** RTL (Arabic First).

---

## 2. Color System

### Primary Branding
| Token | Light Mode (Hex) | Dark Mode (Hex) | Usage |
| :--- | :--- | :--- | :--- |
| `primary-500` | `#0078D4` (Trust Blue) | `#3B8FD9` (Bright Blue) | Primary actions, links, active states |
| `primary-600` | `#005A9E` | `#6CA9E6` | Hover states |
| `secondary-500` | `#00BFA5` (Medical Teal) | `#00D9BD` (Bright Teal) | Success, accents, medical highlights |
| `secondary-100` | `#E0F2F1` | `#004D40` (Rich Teal BG) | Subtle backgrounds, badges |

### Neutrals & Backgrounds
| Token | Light Mode (Hex) | Dark Mode (Hex) | Usage |
| :--- | :--- | :--- | :--- |
| `surface-ground` | `#FFFFFF` | `#0B1120` (Deep Navy) | Main page background |
| `surface-card` | `#F8FAFC` | `#151E32` (Light Navy) | Cards, modals, sidebars |
| `surface-border` | `#E2E8F0` | `#2D3A54` | Borders, dividers |
| `text-main` | `#1E293B` (Slate 800) | `#F1F5F9` (Slate 100) | Headings, primary text |
| `text-body` | `#475569` (Slate 600) | `#94A3B8` (Slate 400) | Paragraphs, secondary text |
| `text-muted` | `#94A3B8` (Slate 400) | `#64748B` (Slate 500) | Placeholders, disabled text |

---

## 3. Typography System
**Font Family:** `Inter` (English) + `Cairo` (Arabic) or `IBM Plex Sans Arabic`.
**Direction:** `RTL` (Right-to-Left) default.

| Scale | Size (px) | Line Height | Weight | Usage |
| :--- | :--- | :--- | :--- | :--- |
| `Display` | 48px | 1.2 | Bold (700) | Hero Headlines |
| `H1` | 36px | 1.3 | SemiBold (600) | Page Titles |
| `H2` | 30px | 1.3 | SemiBold (600) | Section Headers |
| `H3` | 24px | 1.4 | Medium (500) | Card Titles |
| `Body-L` | 18px | 1.6 | Regular (400) | Lead text, intros |
| `Body-M` | 16px | 1.5 | Regular (400) | Default content |
| `Small` | 14px | 1.5 | Regular (400) | Captions, labels |

---

## 4. Design Tokens

### Spacing (8pt Grid)
- `space-1`: 4px
- `space-2`: 8px
- `space-3`: 12px
- `space-4`: 16px (Base unit)
- `space-6`: 24px
- `space-8`: 32px
- `space-12`: 48px
- `space-16`: 64px (Section padding)

### Border Radius
- `radius-sm`: 4px (Checkboxes, inputs)
- `radius-md`: 8px (Cards, buttons)
- `radius-lg`: 16px (Modals, large containers)
- `radius-full`: 9999px (Pills, avatars)

### Shadows
- `shadow-sm`: `0 1px 2px 0 rgb(0 0 0 / 0.05)`
- `shadow-md`: `0 4px 6px -1px rgb(0 0 0 / 0.1)`
- `shadow-lg`: `0 10px 15px -3px rgb(0 0 0 / 0.1)` (Hover effects)

---

## 5. UI Components

### Buttons
- **Primary:** `primary-500` background, white text. `radius-md`.
- **Secondary:** Transparent background, `primary-500` border & text.
- **Ghost:** Transparent background, `text-body` text, `surface-border` on hover.

### Inputs
- **Base:** White bg, `surface-border` border, `radius-sm`.
- **Focus:** `primary-500` border ring.
- **Error:** Red border, error message below.

### Cards
- **Style:** Flat with subtle border (`surface-border`) OR `shadow-sm`.
- **Background:** `surface-card`.
- **Padding:** `space-6`.

---

## 6. Accessibility Notes
- **Contrast:** Ensure text contrast ratio is at least 4.5:1 against background.
- **Focus States:** All interactive elements must have visible focus rings.
- **Semantic HTML:** Use proper `<main>`, `<nav>`, `<header>`, `<footer>` tags.
- **Alt Text:** Required for all images (especially medical diagrams).
- **RTL Support:** Ensure padding, margins, and icons are mirrored correctly (e.g., arrows flip in RTL).

---

## 7. Responsive Guidelines

### Breakpoints
| Device | Breakpoint (min-width) | Container Width |
| :--- | :--- | :--- |
| **Mobile** | `320px` | `100%` (Fluid) |
| **Tablet** | `768px` | `720px` |
| **Desktop** | `1024px` | `960px` |
| **Wide** | `1440px` | `1320px` |

### Responsive Behavior
- **Mobile First:** Start with mobile styles and scale up using min-width media queries.
- **Grids:**
    - Mobile: 1 Column, 16px gutter.
    - Tablet: 2-6 Columns, 24px gutter.
    - Desktop: 12 Columns, 32px gutter.
- **Typography:** Scale down headers on mobile (e.g., `H1` becomes `30px`).
- **Navigation:**
    - Desktop: Horizontal menu.
    - Mobile/Tablet: Hamburger menu with slide-out drawer.
- **Touch Targets:** Ensure interactive elements are at least `44px` height on mobile devices.

