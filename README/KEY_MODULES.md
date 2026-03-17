# Key Modules & Architecture Responsibilities

> **Last updated**: 2026-03-17
> **Status**: Reflects the current implemented codebase. Future/planned items are explicitly labelled.

---

## 1. `accounts` App
**Core Responsibility**: Identity, Authentication & User Management (Global Scope)

**Models**: `CustomUser`, `City`, `IdentityClaim`

> ~~`OneTimePassword`~~ — does **not** exist as a DB model. OTP logic is session-based,
> implemented in `accounts/otp_utils.py` (no DB table).

**Key Logic**:
- Phone-based registration for Patients (3-step: phone → OTP → details → optional email)
- Clinic-owner (MAIN_DOCTOR) registration via 3-stage wizard (`register_clinic_step1/2/3`)
  plus phone/email OTP verification
- Legacy single-page `register_main_doctor` view — **kept for backward compatibility with tests only**
- Login (phone + password), logout
- Forgot-password via phone OTP
- Change phone number and change email (OTP/token verified)
- Email verification (token-based via `email_utils.py`)
- `IdentityClaim` model: tracks national-ID claim lifecycle
  (`UNVERIFIED → UNDER_REVIEW → VERIFIED / REJECTED`)
- Custom auth backend: `PhoneNumberAuthBackend` — normalizes 05x numbers
- `handle_pending_invitation_redirect()` — consumes session-stored invitation tokens
  after login/registration and redirects to the correct invitation inbox

**Email functions** (`accounts/email_utils.py`):
- `send_verification_email` — email address verification link
- `send_email_otp` — 6-digit OTP for clinic verification wizard
- `send_doctor_invitation_email` — invitation delivery
- `send_appointment_cancellation_email` — cancellation notice to patient
- `send_appointment_booking_email` — booking confirmation to patient (new)
- `send_appointment_reminder_email` — 24h reminder to patient (new)
- `send_appointment_rescheduled_email` — reschedule notice to patient (new)
- `send_verification_approved_email` / `send_verification_rejected_email` — doctor verification outcomes

**Email delivery rules** (enforced by all appointment email functions):
- Only sent when `user.email` is set AND `user.email_verified = True`
- Returns `True` on success, `False` on failure (non-raising)
- Failures are logged via `logger.error()`

**API**: JWT token endpoints (`/api/login/`, `/api/logout/`, `/api/token/refresh/`)
via `accounts/api_views.py` + SimpleJWT.

---

## 2. `clinics` App
**Core Responsibility**: Clinic Management (Clinic Scope)

**Models**: `Clinic`, `ClinicStaff`, `ClinicInvitation`, `ClinicSubscription`,
`ClinicActivationCode`, `ClinicVerification`, `ClinicWorkingHours`,
`ClinicHoliday`, `DoctorAvailabilityException`,
`PendingDoctorIdentity`, `InvitationAuditLog`

> ~~`ClinicSettings`~~, ~~`ClinicHolidays`~~ (plural) — ~~do **not** exist~~
> **CORRECTION**: `ClinicHoliday` (singular) **is now implemented** as of the current codebase.
> `ClinicSettings` still does not exist.

**Key Logic**:
- Clinic creation seeded from `ClinicActivationCode`
- Post-signup 4-step verification wizard (owner phone, owner email, clinic phone, clinic email)
- Per-clinic dashboard (`my_clinic`), multi-clinic list (`my_clinics`)
- Clinic switching — `switch_clinic` view sets `selected_clinic_id` in session
- `ClinicWorkingHours` — defines operating days/times per clinic with overlap prevention
- `ClinicHoliday` — clinic-level closure periods; blocks all bookings on covered dates
- `DoctorAvailabilityException` — doctor-specific day-off at a specific clinic; blocks that doctor's slots
- Staff management: invite, cancel invitation, remove staff, view schedule
- `create_invitation` service: handles both DOCTOR and SECRETARY role invitations;
  enforces phone-as-primary-identity, rate limits, subscription capacity caps,
  `PendingDoctorIdentity` lock for unregistered phones
- `accept_invitation` / `reject_invitation` / `cancel_invitation` services
- `InvitationAuditLog` — audit trail for every invitation lifecycle event
- Compliance settings management (`ClinicComplianceSettings`)
- Doctor credential review (approve/reject per-clinic specialty certificates)
- Reports dashboard (`reports_view`)
- Appointment types management (via `appointment_types_views.py`)
- `create_clinic_for_main_doctor` service — atomically creates Clinic, ClinicStaff (MAIN_DOCTOR),
  ClinicSubscription (seeded from activation code), and ClinicVerification

**`ClinicSubscription` key properties**:
- `PLAN_LIMITS` dict: `SMALL → {doctors:2, secretaries:5}`, `MEDIUM → {doctors:4, secretaries:5}`
  (ENTERPRISE has no defaults — admin sets limits explicitly)
- `is_effectively_active()` — `status=ACTIVE` AND `expires_at > now()`
- `can_add_doctor()` / `can_add_secretary()` — returns `True` if limit not reached; `0 = unlimited`
- `current_doctors_count()` / `current_secretaries_count()` — live counts from `ClinicStaff`

**Admin** (`clinics/admin.py`):
- `ClinicSubscriptionAdmin` — actions: Activate, Suspend, Extend 30 days, Extend 365 days
  (all stamp `activated_by = request.user`)
- `ClinicHolidayAdmin` — manage clinic holidays
- `DoctorAvailabilityExceptionAdmin` — manage doctor exceptions

**Middleware**: `clinics/middleware.py` — sets `request.selected_clinic` from session
for multi-clinic owners.

**Context Processor**: `clinics/context_processors.py` — injects active clinic
context into templates.

---

## 3. `doctors` App
**Core Responsibility**: Provider Management

**Models**: `Specialty`, `DoctorProfile`, `DoctorSpecialty`, `DoctorVerification`,
`ClinicDoctorCredential`, `DoctorAvailability`,
`DoctorIntakeFormTemplate`, `DoctorIntakeQuestion`, `DoctorIntakeRule`

> ~~`Specialization`~~ — actual model is `Specialty`.
> ~~`IntakeQuestion`~~ — actual model is `DoctorIntakeQuestion`.
> ~~`DoctorForm`~~, ~~`FormField`~~ — legacy models, kept for migration compatibility only,
> not used by any current view or service.

**Key Logic**:
- Doctor dashboard with identity verification status, clinic cards, today's appointments
- Doctor profile (bio, years of experience) — `doctor_profile_view`
- Doctor appointment list (`appointments_list`) — filterable by date, status, clinic
- Doctor appointment detail (`appointment_detail`) — status transitions, intake answers, doctor notes
- Doctor patients list (`patients_list`) — distinct patients with visit counts
- Invitation inbox — `doctor_invitations_inbox`; doctor accept/reject views
- `guest_accept_invitation_view` — universal public endpoint for email invitation links;
  handles both DOCTOR and SECRETARY role invitations; redirects to correct inbox after login
- Dual-layer verification:
  - **Layer A** (platform identity): `DoctorVerification` — statuses:
    `IDENTITY_UNVERIFIED → IDENTITY_PENDING_REVIEW → IDENTITY_VERIFIED / IDENTITY_REJECTED / REVOKED`
  - **Layer B** (per-clinic credential): `ClinicDoctorCredential` — statuses:
    `CREDENTIALS_PENDING → CREDENTIALS_VERIFIED / CREDENTIALS_REJECTED`
- Document upload: identity documents and medical license (`doctor_upload_credentials`),
  per-clinic specialty certificates (`doctor_upload_clinic_credential`)
- **Availability Engine**: `DoctorAvailability` — weekly schedule slots validated against
  clinic working hours and cross-clinic overlap
- `generate_slots_for_date` service — generates bookable time slots for a given date/doctor/clinic;
  checks `ClinicHoliday` and `DoctorAvailabilityException` before slot generation
- Intake form configuration: `DoctorIntakeFormTemplate` / `DoctorIntakeQuestion` / `DoctorIntakeRule`
  — conditional field logic serialized as JSON for client-side rendering

**API**: `api_views.py` — specialties list, doctor list, doctors by specialty,
doctor availability list, available slots, appointment types.

---

## 4. `patients` App
**Core Responsibility**: Patient Records & Booking Interface

**Models**: `PatientProfile`

> ~~`ClinicPatient`~~ — does **not** exist. There is no per-clinic patient record model.
> All patient identification is through `CustomUser` (global) and `Appointment` (per booking).

**Key Logic**:
- Patient dashboard (`dashboard`)
- Browse doctors and clinics (`browse_doctors`, `clinics_list`)
- Appointment list, cancel, edit with intake form re-submission (`my_appointments`,
  `cancel_appointment_view`, `edit_appointment_view`)
- Book appointment (delegates to `appointments` app)
- Patient profile view/edit (`profile`, `edit_profile`)
- `ensure_patient_profile` service — creates or retrieves a `PatientProfile` for a user
- `PatientProfile` permissions: `patients/permissions.py`

**API**: `PatientProfileAPIView` — `GET /api/patient/profile/` (JWT auth, patient role)

---

## 5. `appointments` App
**Core Responsibility**: Booking Engine & Notifications

**Models**: `Appointment`, `AppointmentType`, `AppointmentAnswer`,
`AppointmentAttachment`, `AppointmentNotification`

> ~~`TimeSlot`~~ — does **not** exist as a DB model. Slots are virtual/computed
> by `generate_slots_for_date` in `doctors/services.py`.

**`Appointment` key fields**:
- `status` — one of 7 `Status` choices
- `reminder_sent` — `BooleanField(default=False)`; set to `True` by `send_appointment_reminders` command
- `patient_edit_count` — incremented each time the patient edits; max = `MAX_PATIENT_EDITS = 2`
- `created_by` — FK to the user who created the record (patient or secretary)

**`AppointmentNotification` key fields**:
- `notification_type` — one of 6 `Type` choices (see below)
- `sent_via_email` — `BooleanField(default=False)`; `True` if email was successfully sent
- `is_delivered` — always `True` for in-app notifications
- `cancelled_by_staff` — FK to `ClinicStaff` (for CANCELLED notifications, records who cancelled)

**Note**: The `UniqueConstraint(fields=["appointment", "notification_type"])` that existed
in an earlier design has been **removed**. Multiple notifications of the same type can now
be created for a single appointment (e.g., multiple reminders are theoretically possible,
though `reminder_sent` flag on `Appointment` prevents this in practice).

**`AppointmentNotification.Type` choices**:
| Type | Trigger |
|---|---|
| `APPOINTMENT_CANCELLED` | Doctor or secretary cancels; or patient cancels (sent to staff) |
| `APPOINTMENT_EDITED` | Patient edits appointment (sent to doctor + secretaries) |
| `APPOINTMENT_BOOKED` | New booking confirmed (sent to patient) |
| `APPOINTMENT_REMINDER` | 24h reminder (sent to patient by management command) |
| `APPOINTMENT_RESCHEDULED` | Secretary reschedules (sent to patient) |
| `APPOINTMENT_STATUS_CHANGED` | Reserved for future use |

**Services**:
- `booking_service.py` — `book_appointment()` — single entry point for appointment creation;
  validates patient, clinic, doctor, subscription active, holiday, exception, slot, concurrency;
  fires `notify_appointment_booked` on `transaction.on_commit()`
- `appointment_notification_service.py` — central notification service; all `notify_*` functions
- `patient_appointments_service.py` — patient-side cancel and edit logic;
  `cancel_appointment_by_staff()` for staff cancellations;
  `CANCELLATION_WINDOW_HOURS = 2`
- `intake_service.py` — collects, validates, and saves `AppointmentAnswer` + `AppointmentAttachment`
- `appointment_type_service.py` — creates/updates `AppointmentType` records

**Management commands**:
- `send_appointment_reminders` — idempotent 24h reminder sender

**Tests** (`appointments/tests/`):
- `test_main.py` — core booking, cancellation, notification tests
- `test_appointment_types.py` — appointment type management tests
- Total: 313 tests passing

**API**: `BookAppointmentAPIView` — `POST /api/book/` (session auth)

---

## 6. `secretary` App
**Core Responsibility**: Secretary (Receptionist) Role Management

**Models**: None (empty `models.py`)

**Key Logic — IMPLEMENTED**:
- Secretary dashboard (`dashboard`) — today's appointments for the secretary's clinic
- Secretary appointment list (`appointments_list`) — filterable by status and date
- Create appointment (`create_appointment`) — books on behalf of a patient by phone lookup;
  validates patient is a registered PATIENT role user; validates doctor belongs to clinic
- Edit appointment (`edit_appointment`) — reschedule date/time, change type, update reason;
  blocked for CHECKED_IN, IN_PROGRESS, COMPLETED, CANCELLED, NO_SHOW statuses;
  fires `notify_appointment_rescheduled_by_staff` on date/time change
- Cancel appointment (`cancel_appointment`) — delegates to `cancel_appointment_by_staff()`
- Secretary invitation inbox — `secretary_invitations_inbox`
  (shows PENDING invitations for the logged-in user's phone, role=SECRETARY)
- Accept/reject invitation — `accept_invitation_view`, `reject_invitation_view`
  (POST handlers; delegate to `clinics.services.accept_invitation` / `reject_invitation`)
- `guest_accept_invitation_view` — public token-based endpoint for secretary email links

---

## 7. `compliance` App
**Core Responsibility**: Patient No-Show & Penalty Tracking

**Models**: `PatientClinicCompliance`, `ComplianceEvent`, `ClinicComplianceSettings`

**Key Logic**:
- `PatientClinicCompliance` — per-patient per-clinic no-show score and block status
- `ComplianceEvent` — audit trail of no-show, waiver, and forgiveness events
- `ClinicComplianceSettings` — per-clinic thresholds and auto-forgiveness config
- `compliance_service.py`:
  - `is_patient_blocked(clinic, patient)` — called by booking service
  - `record_no_show(appointment)` — increments score, blocks if threshold exceeded
  - `run_auto_forgiveness()` — resets scores after configured dormancy period
- Management commands:
  - `process_no_shows` — marks expired `PENDING`/`CONFIRMED` appointments as `NO_SHOW`
  - `run_auto_forgiveness` — run as a scheduled job for auto-forgiveness
- `signals.py` — listens for appointment status changes to trigger compliance events

---

## 8. `core` App
**Core Responsibility**: Shared Validators & Utilities

- `core/validators/file_validators.py` — `validate_file_extension`,
  `validate_file_signature`, `validate_file_size`; used by all file-upload fields

---

## 9. `clinic_website` (Main Project Config)
**Core Responsibility**: Django project configuration, root URL routing

- `settings.py` — project configuration
- `urls.py` — root URL dispatcher; includes all app URLconfs under:
  - `""` → `accounts.urls`
  - `"patients/"` → `patients.urls`
  - `"doctors/"` → `doctors.urls`
  - `"secretary/"` → `secretary.urls`
  - `"clinics/"` → `clinics.urls`
  - `"appointments/"` → `appointments.urls`
  - `"api/patient/profile/"` → `PatientProfileAPIView` (direct)
