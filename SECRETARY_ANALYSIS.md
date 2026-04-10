# Secretary Workspace — Full Codebase Analysis

> Generated: 2026-04-09  
> Purpose: Guide all subsequent development of the secretary workspace.  
> Instructions: Do NOT modify any source files during this analysis phase.

---

## 1. PROJECT STRUCTURE & TECH STACK

### Framework & Language
- **Backend:** Django 6.0.1 (Python)
- **API Layer:** Django REST Framework 3.16.1 + Simple JWT 5.5.1
- **Database:** PostgreSQL (psycopg2-binary 2.9.11) — SQLite fallback for dev
- **Cache / Sessions:** Redis (django-redis 6.0.0 at localhost:6379)
- **WSGI Server:** Gunicorn 25.0.2
- **Static Files:** WhiteNoise 6.11.0

### Frontend Approach
- **Templating:** Django Templates (server-rendered HTML)
- **Interactivity:** HTMX (for live search / partial renders) + minimal vanilla JS
- **Styling:** Tailwind CSS 3 via CDN — **no build step**
- **Icons:** Font Awesome 6.4.0 (CDN)
- **Fonts:** Cairo (Arabic, wght 300–700) + Inter (Latin, wght 400–600) via Google Fonts
- **No React, Vue, Angular, Next.js, or any SPA framework**

### CSS / Styling
- Tailwind utility classes inline in templates
- One global CSS file: `static/accounts/css/loader.css` (643 lines) — loader animations only
- Dark mode: class-based (`.dark` on `<html>`) via localStorage
- Custom Tailwind config block inside `<script>` in each workspace base template
- **No CSS Modules, styled-components, or CSS preprocessor**

### UI Component Library
- **Custom-built only** — no shadcn/ui, MUI, Ant Design, or Chakra
- Shared visual language across workspaces (same badge classes, card shapes, table rows)

### State Management
- **Server-side sessions** for clinic context (`request.session["selected_clinic_id"]`)
- **localStorage** for theme preference only
- No Redux, Zustand, Pinia, or React Query

### Auth System
- **Custom phone-based auth** (`accounts/backends.py` → `PhoneNumberAuthBackend`)
- OTP via TweetsMS (Palestinian 059/056 numbers)
- Email OTP via Brevo (sib-api-v3-sdk)
- JWT for REST API (`/api/login/`, `/api/token/refresh/`)
- Sessions for web views

### SMS / Email Providers
- **TweetsMS** (custom Palestinian SMS provider) — delivers to `9705XXXXXXX` format
- **Brevo (Sendinblue)** — transactional email OTPs

---

## 2. EXISTING ROLES & PERMISSIONS

### Role Definition
Roles live on `CustomUser` in `accounts/models.py`:

```python
# Primary role (single, backward-compat)
role = CharField(choices=[PATIENT, MAIN_DOCTOR, DOCTOR, SECRETARY])

# All roles (ArrayField — a user can hold multiple simultaneously)
roles = ArrayField(CharField)  # e.g. ["PATIENT", "DOCTOR"]
```

Check method: `user.has_role("SECRETARY")` → `role in self.roles`

### Role Hierarchy (from `home_redirect`)
Priority routing in `accounts/views.py::home_redirect()`:
1. MAIN_DOCTOR (owns clinic) → `clinics:my_clinics`
2. DOCTOR (has DOCTOR role) → `doctors:dashboard`
3. SECRETARY (has SECRETARY role) → `secretary:dashboard`
4. Default → `patients:dashboard`

### RBAC Implementation Points

| Layer | File | Mechanism |
|---|---|---|
| Middleware | `clinics/middleware.py` | Sets `request.clinic`, blocks patients from staff URLs |
| View guards | `secretary/views.py::_require_secretary()` | Returns `ClinicStaff` or redirects |
| DRF Permission | `patients/permissions.py::IsPatient` | Checks `has_role("PATIENT")` |
| Template | `{% if request.user.has_role(...) %}` | Inline role checks |

### ClinicIsolationMiddleware (`clinics/middleware.py`)
1. Superusers bypass all checks
2. PATIENT role → 403 on `/doctors/`, `/secretary/`, `/clinics/` (with exceptions for public browsing)
3. Staff (MAIN_DOCTOR, DOCTOR, SECRETARY) must be assigned to an active clinic
4. Sets `request.clinic` and `request.clinic_id` from URL, then session, then first-owned clinic

### Secretary Permissions (current state)
- Can access `/secretary/*` routes
- Scoped to a single clinic (set by middleware from `ClinicStaff` record)
- Can: view/create/edit/cancel appointments, register patients, search patients, manage invitations
- Cannot: manage clinic settings, manage other staff, view subscription, manage working hours
- Cannot: access `/clinics/*` (blocked by middleware)
- Cannot: access `/doctors/*` (blocked by middleware, unless also has DOCTOR role)

---

## 3. DATABASE SCHEMA

### `accounts_customuser` (CustomUser)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| phone | CharField unique | Primary login key. Format: 05XXXXXXXX |
| name | CharField | Full name |
| email | EmailField null/blank | Optional |
| role | CharField | PRIMARY role: PATIENT / MAIN_DOCTOR / DOCTOR / SECRETARY |
| roles | ArrayField(CharField) | ALL roles, e.g. ["PATIENT","DOCTOR"] |
| is_verified | BooleanField | Phone OTP verified |
| email_verified | BooleanField | |
| pending_email | EmailField null | Temp storage during email change |
| national_id | CharField blank | Patient NID |
| city | FK → City null | |
| password | (Django AbstractUser) | hashed |
| is_active, is_staff, is_superuser | (Django) | |
| date_joined, last_login | (Django) | |

### `accounts_city` (City)

| Field | Type |
|---|---|
| id | BigAutoField PK |
| name | CharField unique |

### `accounts_identityclaim` (IdentityClaim)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| user | FK → CustomUser | |
| national_id | CharField | |
| status | CharField | UNVERIFIED / VERIFIED / REJECTED |
| evidence_file | FileField | |
| verified_by | FK → CustomUser null | |
| rejection_reason | TextField | |
| Constraint | unique_verified_per_nid | Only one VERIFIED claim per national_id |

---

### `clinics_clinic` (Clinic)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| name | CharField | |
| address | TextField | |
| phone | CharField null | |
| email | EmailField null | |
| description | TextField blank | |
| specialization | TextField blank | |
| specialties | M2M → Specialty | |
| city | FK → City | |
| main_doctor | FK → CustomUser PROTECT | Clinic owner |
| status | CharField | PENDING / ACTIVE / SUSPENDED (default PENDING) |
| is_active | BooleanField | |
| created_at | DateTimeField auto | |

### `clinics_clinicstaff` (ClinicStaff)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| clinic | FK → Clinic | |
| user | FK → CustomUser | |
| role | CharField | MAIN_DOCTOR / DOCTOR / SECRETARY |
| added_by | FK → CustomUser | |
| added_at | DateTimeField auto | |
| is_active | BooleanField default True | |
| revoked_at | DateTimeField null | Soft revocation timestamp |
| Constraint | unique_active_staff | Unique (clinic, user, role) for active only |

### `clinics_clinicinvitation` (ClinicInvitation)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| clinic | FK → Clinic | |
| invited_by | FK → CustomUser | |
| doctor_name | CharField | Invitee display name |
| doctor_phone | CharField | |
| doctor_email | EmailField | |
| doctor_national_id | CharField blank | |
| specialties | M2M → Specialty | (for doctor invitations) |
| role | CharField | DOCTOR / SECRETARY |
| status | CharField | PENDING / ACCEPTED / REJECTED / EXPIRED / CANCELLED |
| token | UUIDField unique | Acceptance link token |
| expires_at | DateTimeField | |
| Constraint | unique_pending_per_phone | Unique pending per (clinic, phone) |

### `clinics_clinicsubscription` (ClinicSubscription)

| Field | Type | Notes |
|---|---|---|
| id | BigAutoField PK | |
| clinic | OneToOneField → Clinic | |
| plan_type | CharField | MONTHLY / YEARLY |
| plan_name | CharField | SMALL / MEDIUM / ENTERPRISE |
| expires_at | DateTimeField | |
| max_doctors | PositiveIntegerField | 0 = unlimited |
| max_secretaries | PositiveIntegerField | 0 = unlimited |
| status | CharField | ACTIVE / EXPIRED / SUSPENDED |
| activated_by | FK → CustomUser | |
| Methods | can_add_doctor(), can_add_secretary() | Checks count vs max |

### `clinics_clinicverification` (ClinicVerification)

| Field | Type | Notes |
|---|---|---|
| clinic | OneToOneField | |
| owner_phone_verified_at | DateTimeField null | |
| owner_email_verified_at | DateTimeField null | |
| clinic_phone_verified_at | DateTimeField null | |
| clinic_email_verified_at | DateTimeField null | |
| Property | is_fully_verified | True if owner phone verified |

### `clinics_clinicworkinghours` (ClinicWorkingHours)

| Field | Type | Notes |
|---|---|---|
| clinic | FK → Clinic | |
| weekday | IntegerField | 0=Monday … 6=Sunday (Arabic week) |
| start_time, end_time | TimeField null | Null if closed |
| is_closed | BooleanField | |

### `clinics_clinicholiday` (ClinicHoliday)

| Field | Type |
|---|---|
| clinic | FK → Clinic |
| title | CharField |
| start_date, end_date | DateField |
| is_active | BooleanField |
| created_by | FK → CustomUser |

### `clinics_doctoravailabilityexception` (DoctorAvailabilityException)

| Field | Type |
|---|---|
| doctor, clinic | FKs |
| start_date, end_date | DateField |
| reason | CharField |
| is_active | BooleanField |

### `clinics_drugfamily` / `clinics_drugproduct` (Drug Catalog)

| DrugFamily | DrugProduct |
|---|---|
| clinic FK, name | clinic FK, family FK |
| unique per clinic | generic_name, commercial_name |
| | default_dosage/frequency/duration |
| | is_active |

### `clinics_ordercatalogitem` (Non-Drug Orders)

| Field | Notes |
|---|---|
| clinic FK | |
| category | LAB / RADIOLOGY / MICROBIOLOGY / PROCEDURE |
| name | |
| unique (clinic, category, name) | |

---

### `doctors_specialty` (Specialty)

| Field | Notes |
|---|---|
| name, name_ar | Both unique |
| description | |

### `doctors_doctorprofile` (DoctorProfile)

| Field | Notes |
|---|---|
| user | OneToOneField → CustomUser |
| bio | TextField |
| years_of_experience | PositiveIntegerField |
| specialties | M2M → Specialty through DoctorSpecialty |
| Properties: primary_specialty, secondary_specialties | |

### `doctors_doctoravailability` (DoctorAvailability)

| Field | Notes |
|---|---|
| doctor, clinic | FKs |
| day_of_week | 0–6 |
| start_time, end_time | TimeField |
| is_active | BooleanField |
| Constraint | unique (doctor, clinic, day, start_time) |

### `doctors_doctorintakeformtemplate` (DoctorIntakeFormTemplate)

| Field | Notes |
|---|---|
| doctor FK | |
| appointment_type FK null | NULL = all types |
| title, title_ar | CharField |
| description | TextField |
| is_active | BooleanField |
| show_reason_field | BooleanField |
| reason_field_label/placeholder/required | |
| Constraint | unique active (doctor, appointment_type) |

### `doctors_doctorintakequestion` (DoctorIntakeQuestion)

| Field | Notes |
|---|---|
| template FK | |
| question_text, question_text_ar | |
| field_type | TEXT/TEXTAREA/SELECT/MULTISELECT/CHECKBOX/DATE/FILE/DATED_FILES |
| choices | JSONField (for SELECT types) |
| is_required | BooleanField |
| order | PositiveIntegerField |
| placeholder, help_text_content | |
| max_file_size_mb, allowed_extensions | For FILE types |

### `doctors_doctorintakerule` (DoctorIntakeRule)

| Field | Notes |
|---|---|
| source_question, target_question | FKs |
| expected_value | CharField |
| operator | EQUALS/NOT_EQUALS/CONTAINS/IN |
| action | SHOW/HIDE |

### `doctors_doctorverification` (DoctorVerification)

| Field | Notes |
|---|---|
| user | OneToOneField |
| identity_status | IDENTITY_UNVERIFIED / PENDING_REVIEW / VERIFIED / REJECTED / REVOKED |
| identity_document, medical_license | FileField |
| Reviewed by/at/reason | |

### `doctors_clinicdoctorcredential` (ClinicDoctorCredential)

| Field | Notes |
|---|---|
| doctor, clinic, specialty | FKs |
| credential_status | CREDENTIALS_PENDING / VERIFIED / REJECTED / REVOKED |
| specialty_certificate | FileField |
| Constraint | unique (doctor, clinic, specialty) |

---

### `patients_patientprofile` (PatientProfile)

| Field | Notes |
|---|---|
| user | OneToOneField |
| date_of_birth | DateField |
| gender | M / F / O |
| blood_type | CharField |
| medical_history, allergies | TextField |
| emergency_contact_name, emergency_contact_phone | CharField |
| avatar | ImageField |

### `patients_clinicpatient` (ClinicPatient)

| Field | Notes |
|---|---|
| clinic, patient | FKs |
| registered_by | FK → CustomUser |
| registered_at | auto |
| notes | TextField |
| Constraint | unique (clinic, patient) |

### `patients_clinicalnote` (ClinicalNote — SOAP format)

| Field | Notes |
|---|---|
| patient, clinic, doctor | FKs |
| appointment | FK null |
| subjective, objective, assessment, plan | TextField (SOAP) |
| free_text | TextField |

### `patients_order` (Order — prescriptions/labs)

| Field | Notes |
|---|---|
| patient, clinic, doctor | FKs |
| appointment FK null | |
| order_type | DRUG / LAB / RADIOLOGY / MICROBIOLOGY / PROCEDURE |
| title | CharField |
| status | PENDING / COMPLETED / CANCELLED |
| notes | TextField |
| dosage, frequency, duration | CharField (DRUG type only) |

### `patients_prescription` / `patients_prescriptionitem`

| Prescription | PrescriptionItem |
|---|---|
| patient, clinic, doctor FKs | prescription FK |
| appointment FK null | medication_name |
| notes, is_active | dosage, frequency, duration, instructions |

### `patients_medicalrecord` (MedicalRecord)

| Field | Notes |
|---|---|
| patient, clinic FKs | |
| uploaded_by FK | |
| title | |
| category | LAB / RADIOLOGY / GENERAL |
| file | FileField |
| original_name, file_size | |
| notes | |

---

### `appointments_appointmenttype` (AppointmentType)

| Field | Notes |
|---|---|
| clinic FK | |
| name, name_ar | Both CharField |
| duration_minutes | PositiveIntegerField |
| price | DecimalField |
| description | TextField |
| is_active | BooleanField |
| Constraint | unique (clinic, name) |

### `appointments_doctorclinicappointmenttype` (DoctorClinicAppointmentType)

| Field | Notes |
|---|---|
| doctor, clinic, appointment_type | FKs |
| is_active | BooleanField |
| Fallback | if no records for (doctor, clinic), ALL clinic types available |

### `appointments_appointment` (Appointment — core record)

| Field | Notes |
|---|---|
| patient, clinic, doctor | FKs |
| appointment_type FK null | |
| appointment_date | DateField |
| appointment_time | TimeField |
| status | PENDING / CONFIRMED / CHECKED_IN / IN_PROGRESS / COMPLETED / CANCELLED / NO_SHOW |
| reason | TextField |
| intake_responses | JSONField (legacy) |
| notes | TextField |
| patient_edit_count | PositiveIntegerField (max 2) |
| reminder_sent | BooleanField |
| created_by | FK → CustomUser |
| Properties | can_patient_edit, edits_remaining |

### `appointments_appointmentanswer` (AppointmentAnswer)

| Field | Notes |
|---|---|
| appointment, question | FKs |
| answer_text | TextField |
| Constraint | unique (appointment, question) |

### `appointments_appointmentattachment` (AppointmentAttachment)

| Field | Notes |
|---|---|
| appointment, question | FKs |
| file | FileField |
| original_name, file_size, mime_type | |
| file_group_date | DateField |
| uploaded_by | FK |
| Constants | MAX_FILE_GROUPS=7, MAX_FILES_PER_GROUP=5, MAX_TOTAL_UPLOAD_MB=200 |

### `appointments_appointmentnotification` (AppointmentNotification)

| Field | Notes |
|---|---|
| patient, appointment | FKs |
| context_role | PATIENT / DOCTOR / SECRETARY / CLINIC_OWNER |
| notification_type | APPOINTMENT_CANCELLED / EDITED / BOOKED / REMINDER / RESCHEDULED / STATUS_CHANGED |
| title, message | CharField, TextField |
| cancelled_by_staff | FK → ClinicStaff null |
| is_read, is_delivered, sent_via_email | BooleanField |
| Constraint | unique (appointment, notification_type) |

---

## 4. EXISTING SECRETARY FILES

### Models
- `secretary/models.py` — **empty** (no secretary-specific models; uses shared models)

### Views (`secretary/views.py`)

| View Function | Status | Notes |
|---|---|---|
| `dashboard()` | Functional | Today's appointments + stats cards + quick actions |
| `appointments_list()` | Functional | Full roster with status/date filters |
| `create_appointment()` | Functional | Validates doctor ∈ clinic, type enabled, subscription limits |
| `edit_appointment()` | Functional | Update date/time/type/reason |
| `cancel_appointment()` | Functional | Cancel with reason |
| `register_patient()` | Functional | Registration form display |
| `register_patient_submit()` | Functional | Form processing + ClinicPatient creation |
| `patient_search_htmx()` | Functional | HTMX live search by phone |
| `patient_detail_htmx()` | Functional | HTMX patient card display |
| `secretary_invitations_inbox()` | Functional | Pending clinic invitations for this secretary |
| `accept_invitation_view()` | Functional | Accept as logged-in user |
| `reject_invitation_view()` | Functional | Reject as logged-in user |
| `guest_accept_invitation_view()` | Functional | Accept via public UUID token (unauthenticated) |
| `_require_secretary()` | Helper | Returns `ClinicStaff` or None; redirects if unauthorized |

### URLs (`secretary/urls.py`)

```
/secretary/                          → dashboard
/secretary/appointments/             → appointments_list
/secretary/appointments/create/      → create_appointment
/secretary/appointments/<id>/edit/   → edit_appointment
/secretary/appointments/<id>/cancel/ → cancel_appointment
/secretary/patients/register/        → register_patient (GET)
/secretary/patients/register/submit/ → register_patient_submit (POST)
/secretary/patients/search/          → patient_search_htmx (HTMX GET)
/secretary/patients/<id>/card/       → patient_detail_htmx (HTMX GET)
/secretary/invites/                  → secretary_invitations_inbox
/secretary/invites/<id>/accept/      → accept_invitation_view
/secretary/invites/<id>/reject/      → reject_invitation_view
/secretary/invites/accept/<token>/   → guest_accept_invitation_view (public)
```

### Templates (`secretary/templates/secretary/`)

| Template | Status | Description |
|---|---|---|
| `base_secretary.html` | Complete | Layout with navbar, mobile bottom nav, dark mode, notification bell |
| `dashboard.html` | Functional | Stats cards + today's table + quick actions |
| `appointments_list.html` | Functional | Filter form + appointments table |
| `create_appointment.html` | Functional | Patient search + booking form |
| `edit_appointment.html` | Functional | Edit existing appointment |
| `register_patient.html` | Functional | Patient registration form |
| `invitations_inbox.html` | Functional | Pending invitations list |
| `htmx/patient_search_results.html` | Functional | HTMX partial — search results list |
| `htmx/patient_card.html` | Functional | HTMX partial — patient detail card |

### Context Processors (`secretary/context_processors.py`)
- `unread_secretary_notification_count` — adds unread notification count to all secretary templates

---

## 5. DOCTOR & CLINIC OWNER SIDE PATTERNS

### File / Folder Naming Convention
```
<app>/
  views.py              — all view functions (no class-based views observed)
  models.py             — all models
  urls.py               — URL patterns
  forms.py              — Django Form/ModelForm subclasses
  templates/<app>/      — app-namespaced templates
    base_<role>.html    — workspace layout
    dashboard.html      — main dashboard
    <feature>.html      — feature pages
    partials/           — reusable HTML fragments (non-HTMX)
    htmx/               — HTMX partial responses
  static/<app>/
    css/                — custom CSS
    js/                 — custom JS
  context_processors.py — template context injection
  middleware.py         — request-level hooks (clinics only)
  services.py           — business logic (clinics only)
  admin.py              — Django Admin registration
  apps.py               — AppConfig
```

### Component Structure Pattern
```
base_<role>.html         ← workspace layout (navbar, sidebar, footer)
  └── dashboard.html     ← page
        ├── stat cards section
        ├── table/list section
        └── action buttons
```
No layout sub-templates; each page `extends` the base directly.

### Form Handling
- **Django Forms / ModelForms** — no third-party library
- Forms rendered manually in templates (not `{{ form.as_p }}`)
- Each field hand-coded with Tailwind classes
- Inline validation errors: `{% if field.errors %}` → red text below input
- Client-side: minimal JS (phone format validator, confirmation dialogs)
- Submission: standard POST with CSRF token; loader overlay on submit
- HTMX forms: POST to endpoint → returns HTML fragment

### Tables / Data Lists
- Custom HTML `<table>` with Tailwind classes
- Responsive: `<table class="w-full">` wrapped in `<div class="overflow-x-auto">`
- Column visibility: `hidden sm:table-cell`, `hidden md:table-cell`
- Hover: `hover:bg-gray-50 dark:hover:bg-gray-700/30`
- No tanstack-table, DataTable, or external table library
- Pagination: not yet implemented (full list rendered)

### Modals / Dialogs
- **Browser `confirm()`** for destructive actions (cancel, delete)
- **No custom modal component** exists yet
- Overlay pattern: heartbeat loader overlay for form submissions (CSS-based in `loader.css`)

### Toasts / Notifications
- **Django Messages framework** — `{% for message in messages %}` block in base template
- Display as colored alert banners at top of page
- No JS toast library (no Toastify, SweetAlert, etc.)

### Loading / Error State
- **Page-level:** skeleton loader overlay (CSS in `loader.css`) triggered on navigation
- **Form submission:** heartbeat medical loader overlay (CSS) triggered on submit
- **HTMX:** `hx-indicator` attribute pattern (spinner or content swap)
- **Error state:** inline form errors (red text) + Django messages

### Sidebar / Navigation Structure

**Desktop navbar (sticky top):**
```
[Logo + Role Badge] [Nav Links...] [Notification Bell] [Theme Toggle] [User Dropdown]
```

**Mobile navigation (fixed bottom bar):**
```
[Dashboard] [Appointments] [Patients] [More]
```

No sidebar drawer — horizontal navbar only.

### Shared / Reusable Components
There is **no shared component library** — each app re-implements its own:

**Pattern used consistently:**
- **Status badges:** `<span class="inline-flex ...">` with status-specific color classes
- **Stat cards:** `<div class="bg-white rounded-2xl shadow-sm p-6">` with icon + number + label
- **Action buttons:** `<a class="inline-flex items-center gap-2 bg-purple-600 text-white rounded-xl px-4 py-2">`
- **Empty states:** icon + h3 + p + CTA button, centered in card
- **Table header:** `<th class="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">`
- **Form inputs:** `<input class="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-purple-500">`
- **Section headers:** `<h2 class="text-lg font-bold text-gray-900 dark:text-white">` + optional subtitle

---

## 6. API PATTERNS

### REST API Structure
- Endpoints under `/api/` prefix
- Patient profile API: `/api/patient/profile/`
- Auth API: `/api/login/`, `/api/logout/`, `/api/token/refresh/`
- DRF ViewSets / APIView classes (not used heavily — most logic is in web views)

### Authentication in API Routes
- JWT via `Authorization: Bearer <token>` header
- `MyTokenObtainPairView` extends Simple JWT with custom claims (`roles` field in token)
- Web views use Django sessions + `@login_required`

### Data Validation
- **Django Forms** for web views (built-in validators + custom `clean_*` methods)
- **DRF Serializers** for API views
- No Zod, Yup, or Marshmallow
- Arabic-language ValidationError messages in form clean methods

### Error Handling Pattern
- **Web views:** `form.errors` dict rendered in template; `messages.error()` for non-form errors
- **API views:** DRF default 400/401/403/404 JSON responses
- **Business logic errors:** `ValidationError` raised in services/forms → caught in view → added to messages

### Shared API Utilities
- `accounts/otp_utils.py` — `request_otp()`, `verify_otp()`, `is_otp_in_cooldown()`
- `accounts/email_utils.py` — `send_email_otp()`, `verify_email_otp()`, `is_email_otp_in_cooldown()`
- `clinics/services.py` — `create_clinic_for_main_doctor()` (atomic transaction)
- `patients/services.py` — `ensure_patient_profile()` (idempotent get-or-create)

---

## 7. DESIGN SYSTEM

### Color Palette

**Secretary workspace (from `base_secretary.html` Tailwind config):**
```javascript
primary: {
  50:  '#fdf4ff',   // lavender lightest
  100: '#fae8ff',
  400: '#c084fc',
  500: '#a855f7',   // main purple
  600: '#9333ea',   // hover purple
  700: '#7e22ce',
  900: '#3b0764',
}
accent: {
  50:  '#f0fdfa',
  500: '#14b8a6',   // teal
  600: '#0d9488',
}
```

**Doctor workspace:** Blue-based palette
**Clinic owner workspace:** Indigo/blue-based palette
**Patient workspace:** Teal/green-based palette

**Appointment Status Badge Colors:**

| Status | Light BG | Dark text |
|---|---|---|
| PENDING | #fefce8 (yellow-50) | #713f12 (yellow-900) |
| CONFIRMED | #d1fae5 (green-100) | #065f46 (green-900) |
| CHECKED_IN | #dbeafe (blue-100) | #1e40af (blue-900) |
| IN_PROGRESS | #ede9fe (purple-100) | #5b21b6 (purple-900) |
| COMPLETED | #f3f4f6 (gray-100) | #374151 (gray-700) |
| CANCELLED | #fee2e2 (red-100) | #991b1b (red-900) |
| NO_SHOW | #fef3c7 (amber-100) | #92400e (amber-900) |

### Typography
- **Arabic text:** Cairo (Google Fonts) — 300, 400, 500, 600, 700
- **English/Latin text:** Inter (Google Fonts) — 400, 500, 600
- Base font size: 0.875rem (14px) for tables, 1rem (16px) for body
- Headings: `text-xl font-bold` to `text-3xl font-bold`

### Spacing & Sizing Pattern
- Container padding: `px-4 sm:px-6 lg:px-8`
- Card padding: `p-6`
- Section gap: `space-y-6`
- Input height: `py-3 px-4` (approx 48px)
- Button height: `py-2 px-4` (approx 40px) or `py-3 px-6` for prominent

### Dark Mode
- **Mechanism:** `class` strategy — `dark` class on `<html>` element
- **Toggle:** localStorage key `theme` = `'dark'` | `'light'`
- **Init:** `<script>` in `<head>` reads localStorage and applies class before paint (no flash)
- **Coverage:** All templates use `dark:` Tailwind variants
- **Colors:** Surfaces → gray-800/gray-900; Text → gray-100/gray-200; Borders → gray-700

### RTL / Arabic Support
- `<html dir="rtl" lang="ar">` — full document RTL
- Cairo font handles Arabic text natively
- Tailwind RTL-aware classes used throughout
- Form fields: `text-right` implicitly from RTL direction
- Icons: Font Awesome renders correctly in RTL context
- Date format: `dd/mm/yyyy`
- Day names: Arabic (اثنين، ثلاثاء، أربعاء، خميس، جمعة، سبت، أحد)
- Month names: Arabic
- Status labels: Arabic (مؤكد، ملغى، مكتمل، قيد التنفيذ، إلخ)

---

## 8. FULL URL MAP (all apps)

```
/                                    → accounts:landing_page
/dashboard/                          → accounts:home_redirect (role router)
/login/                              → accounts:login_view
/logout/                             → accounts:logout_view

# Patient registration
/register/patient/phone/             → register_patient_phone
/register/patient/verify/            → register_patient_verify
/register/patient/details/           → register_patient_details
/register/patient/email/             → register_patient_email

# Clinic owner registration
/register/clinic/step-1/             → register_clinic_step1
/register/clinic/step-2/             → register_clinic_step2
/register/clinic/step-3/             → register_clinic_step3
/register/clinic/verify-phone/       → register_clinic_verify_phone
/register/clinic/verify-email/       → register_clinic_verify_email

# Password recovery
/forgot-password/                    → forgot_password_phone
/forgot-password/verify/             → forgot_password_verify
/forgot-password/reset/              → forgot_password_reset

# Profile
/profile/change-email/               → change_email_request (legacy)
/profile/change-email-otp/           → change_email_otp_request
/profile/change-email-otp/verify/    → change_email_otp_verify
/profile/change-phone/               → change_phone_request
/profile/change-phone/verify/        → change_phone_verify

# API
/api/login/                          → MyTokenObtainPairView (JWT)
/api/logout/                         → LogoutAPIView
/api/token/refresh/                  → TokenRefreshView
/api/patient/profile/                → PatientProfileAPIView

# Patients workspace
/patients/                           → patients:dashboard
/patients/...                        → (booking, records, etc.)

# Doctors workspace
/doctors/                            → doctors:dashboard
/doctors/...                         → (availability, intake forms, credentials, etc.)

# Secretary workspace
/secretary/                          → secretary:dashboard
/secretary/appointments/             → appointments_list
/secretary/appointments/create/      → create_appointment
/secretary/appointments/<id>/edit/   → edit_appointment
/secretary/appointments/<id>/cancel/ → cancel_appointment
/secretary/patients/register/        → register_patient
/secretary/patients/register/submit/ → register_patient_submit
/secretary/patients/search/          → patient_search_htmx
/secretary/patients/<id>/card/       → patient_detail_htmx
/secretary/invites/                  → secretary_invitations_inbox
/secretary/invites/<id>/accept/      → accept_invitation_view
/secretary/invites/<id>/reject/      → reject_invitation_view
/secretary/invites/accept/<token>/   → guest_accept_invitation_view

# Clinics workspace
/clinics/                            → my_clinics
/clinics/reports/                    → reports_view
/clinics/add/                        → add_clinic_code_view
/clinics/add/details/                → add_clinic_details_view
/clinics/switch/<clinic_id>/         → switch_clinic
/clinics/<clinic_id>/                → my_clinic (dashboard)
/clinics/<clinic_id>/appointments/   → appointments_panel_view
/clinics/<clinic_id>/staff/          → manage_staff
/clinics/<clinic_id>/staff/add/      → add_staff
/clinics/<clinic_id>/staff/add-self/ → add_self_as_staff
/clinics/<clinic_id>/staff/<id>/remove/   → remove_staff
/clinics/<clinic_id>/staff/<id>/schedule/ → doctor_schedule_panel
/clinics/<clinic_id>/invitations/    → invitations_list
/clinics/<clinic_id>/invitations/create/  → create_invitation_view
/clinics/<clinic_id>/invitations/create-secretary/ → create_secretary_invitation_view
/clinics/<clinic_id>/invitations/<id>/cancel/ → cancel_invitation_view
/clinics/<clinic_id>/verify/owner-phone/  → verify_owner_phone
/clinics/<clinic_id>/verify/owner-email/  → verify_owner_email
/clinics/<clinic_id>/verify/clinic-phone/ → verify_clinic_phone
/clinics/<clinic_id>/verify/clinic-email/ → verify_clinic_email
/clinics/<clinic_id>/appointment-types/   → appointment_types_list
/clinics/<clinic_id>/appointment-types/create/      → appointment_type_create
/clinics/<clinic_id>/appointment-types/<id>/edit/   → appointment_type_update
/clinics/<clinic_id>/appointment-types/<id>/toggle/ → appointment_type_toggle
/clinics/<clinic_id>/settings/working-hours/        → clinic_working_hours_list_view
/clinics/<clinic_id>/settings/working-hours/create/ → clinic_working_hours_create_view
/clinics/<clinic_id>/settings/working-hours/<id>/update/ → clinic_working_hours_update_view
/clinics/<clinic_id>/settings/working-hours/<id>/delete/ → clinic_working_hours_delete_view
/clinics/<clinic_id>/settings/compliance/           → compliance_settings_view
/clinics/<clinic_id>/settings/compliance/update/    → compliance_settings_update_view
/clinics/<clinic_id>/credentials/                   → clinic_credentials_list
/clinics/<clinic_id>/credentials/<id>/approve/      → clinic_credential_approve
/clinics/<clinic_id>/credentials/<id>/reject/       → clinic_credential_reject

# Appointments
/appointments/...                    → appointment booking flow

# Admin
/admin/                              → Django Admin
```

---

## 9. SECRETARY WORKSPACE — GAPS & WHAT'S MISSING

Based on analysis, the following features are **NOT yet implemented** in the secretary workspace:

### Patient Management
- [ ] Patient detail/profile page (currently only HTMX card fragment)
- [ ] Patient appointment history view
- [ ] Patient medical records view (read-only)
- [ ] Edit patient profile / contact info
- [ ] Patient list page (browse all clinic patients)

### Appointments
- [ ] Appointment detail page (full view of a single appointment)
- [ ] Status change flow (CONFIRMED → CHECKED_IN → IN_PROGRESS → COMPLETED)
- [ ] No-show marking
- [ ] Calendar/schedule view (appointments by day/week)
- [ ] Doctor availability check before booking (time slot validation)
- [ ] Conflict detection when creating/editing appointments

### Notifications
- [ ] Notification list / inbox page
- [ ] Mark notification as read
- [ ] Notification detail (the bell shows unread count but no inbox view)

### Reports / Analytics
- [ ] Secretary-level reports (daily summary, no-show rates, etc.)

### Profile / Settings
- [ ] Secretary profile page
- [ ] Change password / phone from secretary workspace

### Multi-clinic Support
- [ ] If a secretary belongs to multiple clinics, clinic switcher UI

---

## 10. KEY ARCHITECTURAL DECISIONS TO RESPECT

1. **No build step** — All JS/CSS is CDN or inline. New code must follow this pattern.
2. **Arabic-first RTL** — All UI text should be in Arabic; `dir="rtl"` is global.
3. **HTMX for partial updates** — Live search and card loads use `hx-get`, `hx-target`, `hx-swap`.
4. **Django Messages for user feedback** — Not JS toasts; use `messages.success()` / `messages.error()`.
5. **Tenant isolation via middleware** — `request.clinic` is always available in secretary views; never bypass it.
6. **No sidebar** — Navigation is top navbar (desktop) + bottom bar (mobile). Do not introduce a left sidebar.
7. **Multi-role users** — Always use `has_role()` not `== "SECRETARY"` for role checks.
8. **Soft deletes** — Staff revocation uses `is_active=False` + `revoked_at` timestamp; never hard delete.
9. **Service layer** — Business logic in `services.py`; views should call services, not embed logic.
10. **Atomic transactions** — Multi-step DB operations (like `create_clinic_for_main_doctor`) are wrapped in `@transaction.atomic`.

---

## 11. TEMPLATE SKELETON (for new secretary pages)

```html
{% extends "secretary/base_secretary.html" %}
{% block title %}عنوان الصفحة{% endblock %}

{% block content %}
<div class="space-y-6">
  <!-- Page header -->
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">العنوان</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">وصف قصير</p>
    </div>
    <a href="..." class="inline-flex items-center gap-2 bg-purple-600 hover:bg-purple-700 text-white font-medium rounded-xl px-4 py-2 transition-colors">
      <i class="fas fa-plus text-sm"></i>
      إجراء
    </a>
  </div>

  <!-- Content card -->
  <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
    <!-- Table or content goes here -->
  </div>
</div>
{% endblock %}
```

---

*End of analysis. This document should be consulted before writing any secretary workspace code.*
