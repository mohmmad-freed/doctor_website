# Appointment Booking System — Workflow Specification

> **Last updated**: 2026-03-17
> **Status**: Reflects the **current implemented system**.
> Planned/future enhancements are explicitly labelled **[PLANNED]**.

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Actors](#2-system-actors)
3. [Current Appointment Status Model](#3-current-appointment-status-model)
4. [Patient Booking Flow (Implemented)](#4-patient-booking-flow-implemented)
5. [Secretary Booking Flow (Implemented)](#5-secretary-booking-flow-implemented)
6. [Intake Form Flow (Implemented)](#6-intake-form-flow-implemented)
7. [Slot Calculation Flow (Implemented)](#7-slot-calculation-flow-implemented)
8. [Patient Cancel/Edit Flow (Implemented)](#8-patient-canceledit-flow-implemented)
9. [Compliance Block Check (Implemented)](#9-compliance-block-check-implemented)
10. [Concurrency Protection (Implemented)](#10-concurrency-protection-implemented)
11. [Notification System (Implemented)](#11-notification-system-implemented)
12. [Reminder System (Implemented)](#12-reminder-system-implemented)
13. [No-Show Processing (Implemented)](#13-no-show-processing-implemented)
14. [Planned Enhancements (Not Yet Implemented)](#14-planned-enhancements-not-yet-implemented)

---

## 1. Overview

The appointment booking system supports:
1. **Patient self-service booking** via an HTMX-driven multi-step web form.
2. **Secretary-mediated booking** on behalf of a patient via the secretary dashboard.

All bookings result in a `CONFIRMED` appointment. Doctors advance the appointment through
the lifecycle via the `appointment_detail` view.

### Current Design Principles

1. **Safety** — No double bookings. Concurrent access protected via `select_for_update()`.
2. **Stateful Lifecycle** — Appointments follow a defined state machine (see Section 3).
3. **Compliance-Aware** — Blocked patients cannot book. Checked on every booking attempt.
4. **Intake-First** — Structured intake answers (`AppointmentAnswer`) are collected at booking.
5. **Holiday/Exception Aware** — Clinic holidays and doctor exceptions block slots at generation
   and at booking (defense in depth).
6. **Subscription-Gated** — Clinics with inactive/expired subscriptions cannot receive bookings.

---

## 2. System Actors

### 2.1 Patient
- Registered user with `role = "PATIENT"` and a linked `PatientProfile`.
- Books, cancels, and edits their own appointments via the patient portal.
- Can cancel up to 2 hours before appointment; can edit up to `MAX_PATIENT_EDITS = 2` times.

### 2.2 Doctor / Main Doctor
- User with `role = "DOCTOR"` or `"MAIN_DOCTOR"` linked to a `DoctorProfile`.
- Manages their availability schedule and appointment types.
- Views appointment list (`appointments_list`) and appointment detail (`appointment_detail`).
- Advances appointment status via `_TRANSITION_MAP` in `appointment_detail`.

### 2.3 Secretary (ClinicStaff)
- User with `role = "SECRETARY"` linked to a `Clinic` via `ClinicStaff`.
- Creates appointments on behalf of patients.
- Edits and cancels appointments.
- Secretary-created appointments start as `CONFIRMED`.

### 2.4 System (Automated)
- Django management commands scheduled externally:
  - `process_no_shows` — marks missed appointments as `NO_SHOW`
  - `run_auto_forgiveness` — resets compliance scores after dormancy period
  - `send_appointment_reminders` — sends 24h advance reminders

---

## 3. Current Appointment Status Model

The `Appointment.Status` enum defines **7 statuses**:

| Status | Meaning |
|---|---|
| `PENDING` | Created but not yet confirmed (can be used for patient self-booking in some flows) |
| `CONFIRMED` | Default state after booking or secretary creation |
| `CHECKED_IN` | Patient arrived at clinic |
| `IN_PROGRESS` | Consultation in progress |
| `COMPLETED` | Consultation completed |
| `CANCELLED` | Cancelled by patient, doctor, or clinic |
| `NO_SHOW` | Patient did not arrive; processed by management command |

### Status Transitions

#### Doctor-triggered transitions (from `_TRANSITION_MAP` in `doctors/views.appointment_detail`)
```
PENDING      → CONFIRMED, CANCELLED
CONFIRMED    → CHECKED_IN, CANCELLED, NO_SHOW
CHECKED_IN   → IN_PROGRESS
IN_PROGRESS  → COMPLETED
COMPLETED    → [terminal]
CANCELLED    → [terminal]
NO_SHOW      → [terminal]
```

#### Secretary-triggered transitions
- Secretary can cancel any non-terminal appointment via `secretary:cancel_appointment`.
- Secretary cannot transition to CHECKED_IN, IN_PROGRESS, or COMPLETED.

#### Patient-triggered transitions
- Patient can cancel `PENDING` or `CONFIRMED` appointments (within 2-hour window).

#### System-triggered transitions
- `process_no_shows` — transitions `PENDING`/`CONFIRMED` appointments with past dates to `NO_SHOW`.

---

## 4. Patient Booking Flow (Implemented)

### Step 1 — Browse Clinics
**View**: `patients:clinics_list` | **URL**: `/patients/clinics/`
Patient sees active clinics with name, city, and specialization.

### Step 2 — Browse Doctors
**View**: `patients:browse_doctors` | **URL**: `/patients/doctors/`
Filter by specialty, city, or clinic. Each card shows bio and specialties.

### Step 3 — Start Booking Wizard
**View**: `appointments:book_appointment` | **URL**: `/appointments/book/<clinic_id>/`

Multi-step HTMX form:
1. Select doctor from the clinic's active staff
2. Select appointment type (HTMX: `htmx_appointment_types`)
3. Select date and time slot (HTMX: `htmx_slots`)
4. Fill intake form if configured (HTMX: `htmx_intake_form`)
5. Submit

### Step 4 — Booking Validation (in `booking_service.book_appointment`)
On POST, the service validates in this order:
1. Verify patient is role=PATIENT
2. Date is not in the past (or a past time today)
3. Clinic is active (`is_active=True`)
4. Patient is not blocked (`is_patient_blocked(clinic, patient)`)
5. Doctor is active staff at this clinic
6. Doctor identity is verified (`IDENTITY_VERIFIED`)
7. **Subscription is effectively active** (`subscription.is_effectively_active()`)
8. **No active clinic holiday** on the requested date (`ClinicHoliday` check)
9. **No active doctor exception** on the requested date (`DoctorAvailabilityException` check)
10. Appointment type is active for this clinic
11. Slot is within doctor's availability (`generate_slots_for_date`)
12. Slot passes pre-check availability
13. `Appointment.objects.select_for_update()` — acquire lock
14. Re-validate slot under lock (race condition protection)
15. Create `Appointment` with `status = CONFIRMED`
16. Fire `notify_appointment_booked(appointment)` via `transaction.on_commit()`

### Step 5 — Intake Answers Saved
`save_intake_answers()` creates `AppointmentAnswer` records linked to the appointment.
File fields create `AppointmentAttachment` records.

### Step 6 — Confirmation Page
**View**: `appointments:booking_confirmation` | **URL**: `/appointments/confirmation/<id>/`
Shows appointment details, intake answers (structured), and uploaded attachments.

---

## 5. Secretary Booking Flow (Implemented)

**View**: `secretary:create_appointment` | **URL**: `/secretary/appointments/create/`

1. Secretary selects doctor (from clinic's staff) and appointment type.
2. Secretary enters patient phone number (looked up by normalized phone — never by raw ID).
3. Secretary selects date and time.
4. Server validates:
   - Doctor belongs to this clinic (IDOR prevention — S-02)
   - Patient exists with that phone
   - Patient has `role = "PATIENT"`
5. `book_appointment()` is called — same validation pipeline as patient booking.
6. `appointment.created_by = request.user` (secretary) is saved.
7. `notify_appointment_booked(appointment)` fires via `transaction.on_commit()`.

Secretary-created appointments start as `CONFIRMED`.

---

## 6. Intake Form Flow (Implemented)

- `DoctorIntakeFormTemplate` — one active template per doctor/appointment-type pair
- `DoctorIntakeQuestion` — field types: `TEXT`, `TEXTAREA`, `SELECT`, `MULTISELECT`,
  `CHECKBOX`, `DATE`, `FILE`, `DATED_FILES`
- `DoctorIntakeRule` — conditional show/hide logic serialized as JSON for client-side JS
- At booking: `get_active_intake_template(doctor_id, appointment_type_id)` returns the
  active template and its questions
- Answers stored as `AppointmentAnswer` (text) and `AppointmentAttachment` (files)
- Legacy `intake_responses` JSON field maintained for backward compatibility

---

## 7. Slot Calculation Flow (Implemented)

`generate_slots_for_date(doctor_id, clinic_id, target_date, duration_minutes)`:

1. **Check `ClinicHoliday`** — if an active holiday covers `target_date`, return `[]`
2. **Check `DoctorAvailabilityException`** — if an active exception covers this doctor/clinic/date, return `[]`
3. Get `DoctorAvailability` records for doctor/clinic/weekday
4. For each availability window: generate candidate slots at `duration_minutes` intervals
5. Load all `CONFIRMED`/`COMPLETED` appointments for the doctor on that date
   across all clinics (cross-clinic conflict check — R-03)
6. Mark each slot `is_available = False` if blocked by any existing appointment
7. Return slot list (all slots, with availability flag)

---

## 8. Patient Cancel/Edit Flow (Implemented)

**Views**: `patients:cancel_appointment`, `patients:edit_appointment`

### Cancel
- Patient can cancel their own `PENDING` or `CONFIRMED` appointment.
- Blocked within `CANCELLATION_WINDOW_HOURS = 2` hours of appointment time.
- Terminal statuses (COMPLETED, CANCELLED, NO_SHOW) cannot be cancelled.
- Fires `notify_staff_patient_cancelled(appointment)` via `transaction.on_commit()`
  — notifies doctor and all clinic secretaries (in-app only).

### Edit
- Patient can re-select date/time and re-submit the intake form.
- Only `PENDING` or `CONFIRMED` appointments can be edited.
- Limited to `MAX_PATIENT_EDITS = 2` edits (tracked via `patient_edit_count`).
- Same 2-hour time-based policy as cancellation.
- New slot validated against `generate_slots_for_date` (includes holiday/exception checks).
- Atomic update with `select_for_update()` for race protection.
- Fires `notify_staff_patient_edited(appointment, old_date, old_time, old_type)` via `transaction.on_commit()`
  — notifies doctor and all clinic secretaries (in-app only).

---

## 9. Compliance Block Check (Implemented)

Before creating a booking, `booking_service.py` calls:
```python
from compliance.services.compliance_service import is_patient_blocked
if is_patient_blocked(clinic, patient):
    raise BookingError("You are blocked from booking at this clinic due to repeated no-shows.")
```

The compliance service checks `PatientClinicCompliance.status == "BLOCKED"` for
the patient/clinic pair. Blocked patients receive a user-facing error and cannot proceed.

---

## 10. Concurrency Protection (Implemented)

To prevent double bookings under concurrent requests:
- `Appointment.objects.select_for_update()` locks the appointment rows for the
  doctor's date before creating a new one.
- The slot conflict check runs inside this locked read, ensuring only one booking
  succeeds if two requests arrive simultaneously for the same slot.
- Used in both `book_appointment()` (patient/secretary booking) and `edit_appointment()`
  (patient rescheduling).

---

## 11. Notification System (Implemented)

### AppointmentNotification Model
- 6 notification types: `APPOINTMENT_BOOKED`, `APPOINTMENT_CANCELLED`, `APPOINTMENT_EDITED`,
  `APPOINTMENT_REMINDER`, `APPOINTMENT_RESCHEDULED`, `APPOINTMENT_STATUS_CHANGED`
- `sent_via_email` field — `True` if email was successfully sent for this notification
- `cancelled_by_staff` field — FK to `ClinicStaff` for cancellation audit
- No `UniqueConstraint` on `(appointment, notification_type)` — removed; multiple same-type
  notifications can be created for one appointment

### Central Service
`appointments/services/appointment_notification_service.py`

Rules:
- In-app notification is **always** created first; email failures never block it.
- Email sent only if `user.email` present AND `user.email_verified = True`.
- All functions are safe to call from `transaction.on_commit()`.

### Event Matrix
| Event | Notify Whom | Channels |
|---|---|---|
| Patient self-books or secretary books | Patient | In-app + Email |
| Doctor or secretary cancels | Patient | In-app + Email |
| Secretary reschedules | Patient | In-app + Email |
| Patient cancels | Doctor + Secretaries | In-app only |
| Patient edits | Doctor + Secretaries | In-app only |
| 24h before appointment | Patient | In-app + Email |

### DEFERRED
- In-app notification center UI (bell, feed, read/unread toggle)
- SMS for booking and reminder events

---

## 12. Reminder System (Implemented)

Management command: `appointments/management/commands/send_appointment_reminders.py`

```
python manage.py send_appointment_reminders
```

When run as a scheduled job:
1. Finds all `CONFIRMED` appointments within the next `REMINDER_HOURS_BEFORE = 24` hours
   with `reminder_sent=False`
2. Performs exact window check (not just date-range) to avoid premature reminders
3. Calls `notify_appointment_reminder(appointment)` — in-app + email
4. Sets `appointment.reminder_sent = True`

Idempotent — safe to run multiple times. `reminder_sent = True` prevents duplicates.

> **Note**: This command must be scheduled externally (cron job, Render scheduled task, etc.).
> It is not wired to any built-in task scheduler.

---

## 13. No-Show Processing (Implemented)

Management command: `compliance/management/commands/process_no_shows.py`

When run as a scheduled job:
1. Finds all `PENDING` or `CONFIRMED` appointments whose `appointment_date <= today`
2. Calls `process_appointment_no_show(appointment)` in the compliance service,
   which checks grace period and transitions to `NO_SHOW` if exceeded
3. Increments the patient's no-show score at that clinic

---

## 14. Planned Enhancements (Not Yet Implemented)

The following items are **documented design goals** but are **not yet implemented**
in the codebase. They should not be assumed to be live.

### 14.1 Advanced Status Machine
The following statuses are designed but absent from the current `Appointment.Status` enum:

| Planned Status | Purpose |
|---|---|
| `HOLD` | Short-lived (10 min) slot reservation while patient fills form |
| `PENDING_APPROVAL` | Awaiting secretary review; expires after 2 hours if not actioned |
| `PROPOSED_TIME` | Secretary proposed an alternative time; awaiting patient response |
| `EXPIRED` | Auto-expired HOLD or PENDING_APPROVAL record |
| `REJECTED` | Secretary rejected the booking request |

### 14.2 Fields Required for Advanced Statuses
- `hold_expires_at` — expiry timestamp for HOLD state
- `pending_expires_at` — expiry timestamp for PENDING_APPROVAL state
- `proposed_start_at` / `proposed_end_at` — for PROPOSED_TIME alternative

### 14.3 Secretary Approval Workflow
- Secretary approval/rejection/rescheduling of patient-initiated PENDING_APPROVAL bookings
- Alternative time proposal and patient response flow

### 14.4 Auto-Expiry Management Commands
- Command to expire stale HOLD records (> 10 minutes)
- Command to expire stale PENDING_APPROVAL records (> 2 hours)

### 14.5 SMS Notification Wiring
- SMS for booking creation, reminders, and reschedule events is deferred
- `TweetsMS` service exists in `accounts/services/tweetsms.py`
- SMS for cancellation is conditionally sent if `SMS_PROVIDER = "TWEETSMS"` is configured

### 14.6 AppointmentType Doctor Scoping
- Current: `AppointmentType` is scoped to `Clinic` only (no doctor FK)
- Planned: Add `doctor` FK so appointment types are per-doctor-per-clinic

### 14.7 In-App Notification Center UI
- `AppointmentNotification` records are created but not displayed to users
- Needs: navbar bell with unread count, notification feed, mark-as-read
