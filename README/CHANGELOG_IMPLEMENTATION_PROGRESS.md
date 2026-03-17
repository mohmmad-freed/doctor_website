# Implementation Progress Changelog

> **Last updated**: 2026-03-17
> This document tracks the major development phases of the system.
> Use `## CURRENT IMPLEMENTATION` sections in other docs for the live state.

---

## Phase 1: Foundation — Core Auth, Clinic Setup, Doctor Onboarding

### What was implemented
- `CustomUser` model with phone-based auth (`PhoneNumberAuthBackend`)
- Patient registration flow (phone → OTP → details)
- Clinic owner (MAIN_DOCTOR) registration via `ClinicActivationCode`
- `Clinic`, `ClinicStaff`, `ClinicInvitation`, `PendingDoctorIdentity` models
- Doctor invitation flow: invite → email link → accept/reject
- `DoctorProfile`, `DoctorVerification` dual-layer verification
- `ClinicWorkingHours` — recurring weekly schedule per clinic
- `DoctorAvailability` — weekly availability per doctor per clinic
- `generate_slots_for_date` — stateless slot engine with cross-clinic conflict check (R-03)
- Booking wizard (patient self-service): HTMX multi-step form
- `book_appointment()` service — compliance check, slot validation, `select_for_update()` race protection
- `AppointmentAnswer` + `AppointmentAttachment` for intake forms
- Basic `AppointmentNotification` model (CANCELLED, EDITED types only)

### Key files changed
- `accounts/models.py`, `accounts/views.py`, `accounts/backends.py`
- `clinics/models.py`, `clinics/services.py`, `clinics/views.py`
- `doctors/models.py`, `doctors/services.py`, `doctors/views.py`
- `appointments/models.py`, `appointments/services/booking_service.py`
- `appointments/services/intake_service.py`

### Decisions made
- Single-database shared-schema multi-tenancy (clinic_id filtering at every layer)
- Stateless slot generation (no pre-generated TimeSlot table)
- Phone number as primary identity key
- `select_for_update()` for booking concurrency protection

---

## Phase 2: QA / Security / Permissions Hardening

### What was implemented
- Enforced `clinic_id` scoping in all clinic-owner and secretary views (R-01)
- Added `select_for_update()` to invitation acceptance to prevent race conditions on plan caps
- `PendingDoctorIdentity` lock for unregistered phones being invited simultaneously
- `InvitationAuditLog` — tamper-evident audit trail for all invitation lifecycle events
- `DoctorVerification.identity_status` check added to `book_appointment()` (R-26 equivalent)
- Fixed invitation routing for SECRETARY role in `guest_accept_invitation_view`
- Added IDOR prevention in secretary `accept_invitation_view` (phone ownership check)
- `validate_file_signature` + `validate_file_size` in `core/validators/file_validators.py`
- Applied file validators to all upload fields

### Key files changed
- `clinics/services.py` — rate limits, plan cap re-check inside `select_for_update()`
- `doctors/views.py` — `guest_accept_invitation_view` routing fix
- `secretary/views.py` — phone ownership check in accept/reject views
- `core/validators/file_validators.py`

### Decisions made
- IDOR prevention via phone-based ownership check, not user_id check
- `InvitationAuditLog` is write-once (no `has_change_permission` in admin)
- File upload validation enforces MIME signature regardless of extension

---

## Phase 3: UI/UX Arabic Polish

### What was implemented
- Arabic RTL layout throughout (Bootstrap + custom CSS)
- Arabic display names on all `Status`, `Type`, and `ROLE_CHOICES` enums
- `AppointmentType.name_ar` + `display_name` property
- `DoctorIntakeQuestion` field types with Arabic labels
- `Specialty.name_ar` used preferentially in patient-facing views
- `patient_appointments_service._serialize_appointment()` prefers Arabic specialty/type names
- All notification messages in Arabic (titles + bodies in `appointment_notification_service.py`)
- All email templates in Arabic (Brevo via `accounts/email_utils.py`)
- Clinic verification wizard with Arabic error messages and OTP emails

### Key files changed
- `appointments/models.py` — Arabic choices on Status, AppointmentNotification.Type
- `appointments/services/appointment_notification_service.py` — Arabic message strings
- `accounts/email_utils.py` — Arabic email bodies
- All template files (`*.html`) — RTL layout, Arabic labels

### Decisions made
- Arabic is the primary UI language; English enum values retained for code clarity
- Email via Brevo (sib_api_v3_sdk); SMS via TweetsMS (conditionally enabled)

---

## Phase 4: Subscriptions + Plan Limits + Manual Billing + Holidays + Availability Exceptions

### What was implemented

#### Subscription System
- `ClinicSubscription` model with `plan_name` (SMALL/MEDIUM/ENTERPRISE), `plan_type`, `expires_at`, `status`
- `PLAN_LIMITS` dict: `SMALL → {doctors:2, secretaries:5}`, `MEDIUM → {doctors:4, secretaries:5}`
- ENTERPRISE: no defaults in `PLAN_LIMITS`; admin sets `max_doctors`/`max_secretaries` explicitly
- `0 = unlimited` as an explicit admin opt-in for ENTERPRISE plans
- `is_effectively_active()` — requires both `status=ACTIVE` AND `expires_at > now()`
- `can_add_doctor()` / `can_add_secretary()` — plan cap enforcement helpers
- `max_secretaries` field added (previously only `max_doctors` existed)
- `activated_by` FK — stamps who last activated/extended the subscription

#### Admin Billing Actions
- `ClinicSubscriptionAdmin` with 4 bulk actions:
  - Activate, Suspend, Extend 30 days, Extend 365 days
  - All stamp `activated_by = request.user`
- `ClinicActivationCode` updated to include `plan_name`, `max_secretaries`, `subscription_expires_at`
- `create_clinic_for_main_doctor` applies `PLAN_LIMITS` corrections for old activation codes

#### Clinic Holidays
- `ClinicHoliday` model: `clinic`, `title`, `start_date`, `end_date`, `is_active`, `created_by`
- `ClinicHolidayAdmin` registered in Django admin
- Holiday check added to `generate_slots_for_date` (returns `[]`)
- Holiday check added to `book_appointment()` (raises `BookingError(code="clinic_holiday")`)

#### Doctor Availability Exceptions
- `DoctorAvailabilityException` model: `doctor`, `clinic`, `start_date`, `end_date`, `reason`, `is_active`
- `DoctorAvailabilityExceptionAdmin` registered in Django admin
- Exception check added to `generate_slots_for_date` (returns `[]`)
- Exception check added to `book_appointment()` (raises `BookingError(code="doctor_exception")`)

#### Subscription Booking Gate
- `book_appointment()` now calls `subscription.is_effectively_active()` before creating appointment
- If no subscription record exists, booking is allowed (backward-compatible)

### Key files changed
- `clinics/models.py` — added `ClinicHoliday`, `DoctorAvailabilityException`, updated `ClinicSubscription`
- `clinics/admin.py` — `ClinicSubscriptionAdmin` with actions, `ClinicHolidayAdmin`, `DoctorAvailabilityExceptionAdmin`
- `clinics/services.py` — `create_clinic_for_main_doctor` updated for plan limits
- `doctors/services.py` — `generate_slots_for_date` checks holidays and exceptions
- `appointments/services/booking_service.py` — subscription check, holiday check, exception check
- `clinics/tests/test_plan_limits.py` — new test file

### Decisions made
- Defense in depth: check holiday/exception in BOTH slot generation AND booking service
- ENTERPRISE plans have no `PLAN_LIMITS` entry — prevents accidental cap application
- `0 = unlimited` is a deliberate admin opt-in, not a default
- `is_effectively_active()` method preferred over raw `status` check to prevent bookings on expired-but-ACTIVE subscriptions

---

## Phase 5: Advanced Appointment Workflow + Notifications + Reminders + Plan Limit Correction

### What was implemented

#### Doctor & Secretary Views (Previously Stubs)
- `doctors/views.appointments_list` — full filterable appointment list for the doctor
- `doctors/views.appointment_detail` — full detail view with `_TRANSITION_MAP`, intake answers, notes
- `doctors/views.patients_list` — distinct patient list with visit counts
- `secretary/views.dashboard` — daily appointment overview
- `secretary/views.appointments_list` — full filterable list
- `secretary/views.create_appointment` — secretary books for a patient (phone lookup, CONFIRMED)
- `secretary/views.edit_appointment` — secretary reschedules (fires notification on date/time change)
- `secretary/views.cancel_appointment` — delegates to `cancel_appointment_by_staff()`

#### Notification System
- `appointments/services/appointment_notification_service.py` — central notification service
- `notify_appointment_booked` — fires from `book_appointment()` via `transaction.on_commit()`
- `notify_appointment_cancelled_by_staff` — fires when doctor or secretary cancels
- `notify_appointment_rescheduled_by_staff` — fires when secretary reschedules
- `notify_staff_patient_cancelled` — notifies doctor + secretaries when patient cancels
- `notify_staff_patient_edited` — notifies doctor + secretaries when patient edits
- `notify_appointment_reminder` — called by management command
- `AppointmentNotification.Type` expanded: added `APPOINTMENT_BOOKED`, `APPOINTMENT_REMINDER`, `APPOINTMENT_RESCHEDULED`, `APPOINTMENT_STATUS_CHANGED`
- `AppointmentNotification.sent_via_email` field added
- `UniqueConstraint(appointment, notification_type)` removed
- `Appointment.reminder_sent` field added

#### Email Functions (new)
- `send_appointment_booking_email` — booking confirmation
- `send_appointment_reminder_email` — 24h reminder
- `send_appointment_rescheduled_email` — reschedule notice

#### Reminder Management Command
- `appointments/management/commands/send_appointment_reminders.py`
- Finds CONFIRMED appointments within next 24h with `reminder_sent=False`
- Idempotent — `reminder_sent=True` blocks re-processing
- `REMINDER_HOURS_BEFORE = 24`

#### Plan Limit Correction
- `create_clinic_for_main_doctor` fixed to apply `PLAN_LIMITS` corrections
  when old activation codes carry stale default values

#### Test Suite Restructuring
- `appointments/tests/` converted from single file to package
- `appointments/tests/test_main.py` — core booking, notification tests
- `appointments/tests/test_appointment_types.py` — appointment type tests
- `clinics/tests/test_plan_limits.py` — plan limit enforcement tests
- Total: 313 tests passing

### Key files changed
- `doctors/views.py` — full implementation of appointment_detail, appointments_list, patients_list
- `secretary/views.py` — full implementation of dashboard, appointments_list, create/edit/cancel
- `appointments/models.py` — added `reminder_sent`, `notes`, updated `AppointmentNotification`
- `appointments/services/appointment_notification_service.py` — new file (central service)
- `appointments/management/commands/send_appointment_reminders.py` — new file
- `accounts/email_utils.py` — new appointment email functions
- `appointments/tests/` — restructured as package
- `clinics/tests/test_plan_limits.py` — new file

### Decisions made
- `transaction.on_commit()` used for all notifications (fires only after successful DB write)
- In-app notification always created first; email is strictly non-blocking
- `sent_via_email` on `AppointmentNotification` used for email delivery tracking (no separate log model)
- `reminder_sent` flag on `Appointment` for idempotent reminder processing (simpler than UniqueConstraint)
- UniqueConstraint removed from AppointmentNotification to allow multiple notifications of same type
- All notification messages written in Arabic to match platform language
