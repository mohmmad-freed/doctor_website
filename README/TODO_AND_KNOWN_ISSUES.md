# TODOs & Known Issues

> **Last updated**: 2026-03-17

---

## 1. Current Implementation Gaps (Deferred to Next Phase)

These items are **not yet implemented** but are clearly intended features.

### 1.1 Advanced Appointment Lifecycle (Extended Statuses)
The following appointment statuses are **not yet implemented** in code:
- `HOLD` — short-lived slot reservation (10 min)
- `PENDING_APPROVAL` — awaiting secretary review
- `PROPOSED_TIME` — secretary proposed alternative time
- `EXPIRED` — auto-expired stale records
- `REJECTED` — secretary rejected the booking

Required model fields not yet added:
- `hold_expires_at`, `pending_expires_at`
- `proposed_start_at`, `proposed_end_at`

Management commands for auto-expiry not yet implemented.

Full spec: `README/WORKFLOWS/APPOINTMENT_BOOKING_WORKFLOW.md` Section 12.

### 1.2 In-App Notification Center UI (Bell / Feed)
The `AppointmentNotification` model and all notification creation logic are fully
implemented. However, there is **no frontend UI** to display these notifications to
patients or staff:
- No notification bell/counter in the navbar
- No notification feed/list page
- No "mark as read" interaction

The `is_read` field exists on `AppointmentNotification` but is never toggled from the UI.
This UI layer is deferred.

### 1.3 SMS Notifications (Deferred)
- `accounts/services/tweetsms.py` implements TweetsMS integration
- SMS is called from `cancel_appointment_by_staff()` only if `SMS_PROVIDER = "TWEETSMS"`
  and `TWEETSMS_API_KEY` / `TWEETSMS_SENDER` are configured
- SMS is **NOT** sent for booking confirmation, reminders, or reschedule events
- Appointment booking and reminder SMS wiring is deferred

### 1.4 Per-Clinic Patient Records (`ClinicPatient`)
- No `ClinicPatient` model exists
- Per-clinic notes, local file numbers, and clinic-specific history are not stored
- Patients are identified within a clinic only through `Appointment` records

### 1.5 AppointmentType Doctor Scoping — FIXED (2026-03-18)
- `DoctorClinicAppointmentType` model handles per-doctor-per-clinic type assignment
- `get_appointment_types_for_doctor_in_clinic()` service correctly returns doctor-specific types
- Three UI-layer views were incorrectly querying all clinic types instead of doctor-scoped ones:
  - `book_appointment_view` (appointments/views.py) — now uses service function
  - `doctor_availability_view` (doctors/views.py) — now uses service function; `selected_type`
    lookup now validates type is in doctor's enabled set before generating slots
  - `load_available_slots` HTMX endpoint (appointments/views.py) — now validates the
    appointment type is enabled for the doctor before generating slots
- Backward-compat fallback still applies: doctors with no DoctorClinicAppointmentType rows
  configured see all active clinic types (intended behaviour)

### 1.6 Billing & Invoicing
- No billing, invoicing, or insurance models exist
- Phase 2 feature — not yet scoped
- `ClinicSubscription` tracks plan/expiry but is not a full billing system

### 1.7 Reminder Cron Scheduling
- `send_appointment_reminders` management command is fully implemented and idempotent
- It is **not yet wired to any cron/task queue** in the deployment configuration
- Must be scheduled externally (e.g., Render cron job, Celery Beat, or system cron)

---

## 2. Technical Debt

### 2.1 Legacy `register_main_doctor` Route
- `accounts:register_main_doctor` is a legacy single-page registration view
- It is kept for **backward compatibility with existing tests only**
- New user-facing traffic should use the 3-stage wizard (`register_clinic_step1/2/3`)
- Should be retired and tests updated when test coverage allows

### 2.2 Legacy `DoctorForm` / `FormField` Models
- `doctors/models.py` contains legacy `DoctorForm` and `FormField` models
- These are not used by any current view, service, or API
- They are **kept only for migration compatibility** (removing them requires careful migration management)
- New intake form logic uses `DoctorIntakeFormTemplate` / `DoctorIntakeQuestion`

### 2.3 Root Debug Scripts
- `debug_booking.py`, `hack.py`, `reset_db.py` exist at the project root
- These are **development/debug tools**, not part of the application
- They should not be deployed to production

### 2.4 Root Preview HTML Files
- Multiple `preview_*.html` and `*_preview_*.html` files exist at the project root
- These are UI prototyping artifacts
- They are not served by any Django view and should not be deployed to production

### 2.5 Appointment Arabic Month/Day Dict Encoding
- `appointments/views.py` lines 31-37 contain garbled encoding in the `ARABIC_DAYS`
  and `ARABIC_MONTHS` dicts (the Arabic strings appear byte-mangled)
- The `format_date_ar()` function may return corrupted Arabic date strings
- Needs investigation and fix

---

## 3. Infrastructure / Operational Notes

### 3.1 Scalability — Appointment Conflict Query
- The global conflict check (`appointments WHERE doctor_id=X AND date=Y`) runs on every
  slot generation. As the appointment table grows, this needs indexing on
  `(doctor_id, appointment_date)`.

### 3.2 Authentication
- Session (web) + JWT (API) dual authentication
- JWT token blacklist requires `rest_framework_simplejwt.token_blacklist` to be
  installed and migrated for server-side invalidation on API logout

### 3.3 `ENFORCE_PHONE_VERIFICATION` Feature Flag
- Controls whether phone verification is required at login
- Default: `True`
- Intended as a temporary switch — should be made mandatory once OTP flow is stable

### 3.4 File Upload Security
- Extension, MIME signature, and size validation is implemented in
  `core/validators/file_validators.py`
- Malware scanning (ClamAV or similar) is **not yet implemented**

### 3.5 Timezones
- System currently assumes all clinics operate in a single timezone
- `TIME_ZONE` in `settings.py` must be set correctly for the target region
- Cross-timezone or telehealth scenarios are not yet handled

### 3.6 Reminder Command Cron Gap
- `send_appointment_reminders` must be scheduled externally
- If not scheduled, no reminder notifications will be sent
- Recommended: run hourly or every 6 hours

---

## 4. Fixed Issues (Resolved — 2026-03-17)

- **Secretary views were stubs**: Secretary dashboard, appointment list, create, edit,
  and cancel appointment views are now fully implemented in `secretary/views.py`.
- **Secretary invitation routes unreachable**: The 4 invitation views in
  `secretary/views.py` had no URL registrations. Added to `secretary/urls.py`.
- **Secretary routing after email link**: `doctors/views.py`
  `guest_accept_invitation_view` stored `pending_invitation_app = "doctors"` even
  for SECRETARY-role invitations. Fixed to use `"secretary"` app slug.
- **Doctor views were stubs**: `appointments_list`, `appointment_detail`, and
  `patients_list` in `doctors/views.py` are now fully implemented.
- **Notification system was unimplemented**: `appointment_notification_service.py`
  now provides full in-app + email notifications for all appointment events.
- **No booking confirmation email**: `send_appointment_booking_email` and
  `notify_appointment_booked` are now implemented and called from `book_appointment`.
- **No reminder system**: `send_appointment_reminders` management command is now
  implemented with idempotent reminder logic.
- **Plan limit stale data**: `create_clinic_for_main_doctor` now applies `PLAN_LIMITS`
  corrections when activation codes carry old default values.
- **Subscription not checked on booking**: `book_appointment()` now calls
  `subscription.is_effectively_active()` before creating any appointment.
- **Missing holiday/exception enforcement**: `ClinicHoliday` and
  `DoctorAvailabilityException` are now checked in both slot generation and booking.
- **Stale documentation**: Multiple docs updated to reflect current implementation.
