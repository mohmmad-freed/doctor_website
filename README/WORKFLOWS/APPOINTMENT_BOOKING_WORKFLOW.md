# Appointment Booking System — Workflow Specification

> **Document Type:** Architecture Specification — Workflow & Process Flows  
> **Version:** 1.0  
> **Status:** Draft  
> **Last Updated:** 2026-02-17

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Actors](#2-system-actors)
3. [Complete Patient Flow](#3-complete-patient-flow)
4. [Doctor Flow](#4-doctor-flow)
5. [Secretary Flow (ClinicStaff)](#5-secretary-flow-clinicstaff)
6. [Intake Form Flow](#6-intake-form-flow)
7. [Slot Calculation Flow](#7-slot-calculation-flow)
8. [HOLD Flow](#8-hold-flow)
9. [Pending Approval Flow](#9-pending-approval-flow)
10. [Alternative Time Proposal Flow](#10-alternative-time-proposal-flow)
11. [Patient Response Flow](#11-patient-response-flow)
12. [Auto-Expire Flow](#12-auto-expire-flow)
13. [Reminder Flow (24h Before)](#13-reminder-flow-24h-before)
14. [SMS Notification Flow](#14-sms-notification-flow)
15. [Concurrency Protection Flow](#15-concurrency-protection-flow)
16. [State Machine Diagram](#16-state-machine-diagram)
17. [Error and Edge Cases Handling](#17-error-and-edge-cases-handling)

---

## 1. Overview

This document specifies the complete workflow for a **multi-clinic, multi-doctor appointment booking system** built on the existing Django SaaS platform. The system supports:

- Patient self-service booking through a web interface
- Dynamic intake forms per doctor/appointment type
- Variable appointment durations per `AppointmentType`
- Secretary-mediated approval workflow with alternative time proposals
- Real-time slot availability with race condition protection
- Automated expiration, reminders, and SMS notifications

### Design Principles

1. **Safety First** — No double bookings. Concurrent access is handled via database-level locks and the HOLD mechanism.
2. **Stateful Lifecycle** — Every appointment follows a strict state machine. Invalid transitions are rejected.
3. **Auditability** — All state transitions, notifications, and system actions are logged.
4. **Scalability** — Slot computation is stateless and re-calculated per request. No pre-generated slot tables.

---

## 2. System Actors

### 2.1 Patient

- A registered user with `role = "PATIENT"` and a linked `PatientProfile`.
- Can browse clinics and doctors, book appointments, fill intake forms, respond to alternative time proposals, and cancel their own appointments.

### 2.2 ClinicStaff (Secretary)

- A user with `role = "SECRETARY"` linked to a `Clinic` via the `ClinicStaff` model (`role = "SECRETARY"`).
- Operates on behalf of the clinic: reviews pending appointments, approves, rejects, or proposes alternative times.
- Can also create appointments on behalf of walk-in patients.

### 2.3 DoctorProfile

- A user with `role = "DOCTOR"` or `"MAIN_DOCTOR"` linked to a `DoctorProfile`.
- Manages their own availability schedule (`DoctorAvailability`), appointment types (`AppointmentType`), and intake form templates.
- Reviews appointment details and patient intake answers.

### 2.4 System (Automated)

- Background processes driven by Django management commands or Celery tasks.
- Responsibilities:
  - Expire stale HOLD records (> 10 minutes)
  - Expire stale PENDING_APPROVAL records (> 2 hours)
  - Send SMS reminders 24 hours before appointments
  - Clean up orphaned records

---

## 3. Complete Patient Flow

The patient booking journey touches the following existing views in order:

### Step 1 — Browse Clinics

**View:** `patients.clinics_list`  
**URL:** `/patients/clinics/`

- Patient sees a list of active clinics.
- Each clinic card shows name, specialization, city, and a "Browse Doctors" action.

### Step 2 — Browse Doctors

**View:** `patients.browse_doctors`  
**URL:** `/patients/doctors/`

- Patient filters doctors by specialty, city, or clinic.
- Each doctor card shows name, specialties (from `DoctorSpecialty`), bio (from `DoctorProfile`), and a "Book Appointment" action.

### Step 3 — Initiate Booking

**View:** `patients.book_appointment`  
**URL:** `/patients/appointments/book/<int:clinic_id>/`

- This is a thin redirect/wrapper that routes the patient into the booking flow for a specific clinic.
- Internally delegates to the `appointments` app.

### Step 4 — Booking Page

**View:** `appointments.book_appointment_view`  
**URL:** `/appointments/book/<int:clinic_id>/`

This is the main booking page. It is a multi-step form powered by HTMX partials:

1. **Select Doctor** — Patient picks a doctor from the clinic's roster.
2. **Select Appointment Type** — Loaded dynamically via `load_appointment_types`.
3. **Select Date & Time Slot** — Loaded dynamically via `load_available_slots`.
4. **Fill Intake Form** — Dynamic per-doctor questions (if configured).
5. **Review & Confirm** — Summary of selections before final submission.

### Step 4a — Load Appointment Types

**View:** `appointments.load_appointment_types`  
**URL:** `/appointments/<int:clinic_id>/htmx/appointment-types/`

- HTMX partial triggered when the patient selects a doctor.
- Returns active `AppointmentType` records for the selected `doctor_id` + `clinic_id`.
- Displays name, Arabic name, duration, and price for each type.

### Step 4b — Load Available Slots

**View:** `appointments.load_available_slots`  
**URL:** `/appointments/<int:clinic_id>/htmx/slots/`

- HTMX partial triggered when the patient selects an appointment type and a date.
- Computes available time slots using the Slot Calculation Flow (Section 7).
- Returns a grid of bookable time slots.
- Slots currently under HOLD or PENDING_APPROVAL by other patients are excluded.

### Step 5 — Submit Booking

**Action:** POST to `appointments.book_appointment_view`

Upon submission:

1. System validates all inputs.
2. System creates an `Appointment` record with `status = HOLD`.
3. `hold_expires_at` is set to `now() + 10 minutes`.
4. If the clinic requires secretary approval → status transitions to `PENDING_APPROVAL` after intake form is completed.
5. If no approval required → status transitions directly to `CONFIRMED`.
6. SMS notification is sent to the patient.

### Step 6 — Booking Confirmation

**View:** `appointments.booking_confirmation`  
**URL:** `/appointments/confirmation/<int:appointment_id>/`

- Displays the appointment summary, status, and next steps.
- If `PENDING_APPROVAL`: shows "Awaiting clinic confirmation" message.
- If `CONFIRMED`: shows confirmed appointment details with calendar add option.

### Step 7 — My Appointments

**View:** `patients.my_appointments`  
**URL:** `/patients/appointments/`

- Lists all patient's appointments grouped by status.
- Active appointments (CONFIRMED, PENDING_APPROVAL) shown at top.
- Allows cancellation of future appointments.
- Shows proposed alternative times requiring patient response.

---

## 4. Doctor Flow

### 4.1 Manage Availability

**View:** `doctors.doctor_availability_view`  
**URL:** `/doctors/<int:doctor_id>/availability/`

- Doctor defines their recurring weekly schedule per clinic.
- Each `DoctorAvailability` record specifies: `clinic_id`, `day_of_week`, `start_time`, `end_time`.
- The system enforces no overlapping availability across clinics (existing validation in `DoctorAvailability.clean()`).

### 4.2 Manage Appointment Types

**View:** `doctors.doctor_appointment_types_view`  
**URL:** `/doctors/<int:doctor_id>/appointment-types/`

- Doctor creates/edits `AppointmentType` records.
- Each type defines: `name`, `name_ar`, `duration_minutes`, `price`, `description`.
- Types are scoped to a specific `(doctor, clinic)` pair.
- Deactivated types (`is_active = False`) are hidden from patients but preserved for historical appointments.

### 4.3 View Appointments

**View:** `doctors.appointments_list`  
**URL:** `/doctors/appointments/`

- Lists all appointments for the logged-in doctor.
- Filterable by: status, date range, clinic, patient name.
- Shows upcoming appointments first.

### 4.4 Appointment Detail

**View:** `doctors.appointment_detail`  
**URL:** `/doctors/appointments/<int:appointment_id>/`

- Full appointment details including:
  - Patient information (from `PatientProfile`).
  - Appointment type, duration, and time.
  - Intake form answers (from `AppointmentAnswer`).
  - Attached files (from `AppointmentAttachment`).
  - Appointment status history.
- Doctor can add notes and update status (COMPLETED, NO_SHOW).

---

## 5. Secretary Flow (ClinicStaff)

The secretary (a `ClinicStaff` with `role = "SECRETARY"`) manages the clinic's appointment queue.

### 5.1 Dashboard

**View:** `secretary.dashboard`

- Overview of today's appointments, pending approvals count, and upcoming schedule.

### 5.2 Appointment Queue

**View:** `secretary.appointments_list`

- Lists appointments filtered by status.
- Primary focus: `PENDING_APPROVAL` appointments requiring action.

### 5.3 Accept Appointment

**Trigger:** Secretary clicks "Accept" on a PENDING_APPROVAL appointment.

**Flow:**

1. System verifies appointment is still in `PENDING_APPROVAL` status.
2. System verifies the time slot is still available (no CONFIRMED overlap).
3. Status transitions: `PENDING_APPROVAL → CONFIRMED`.
4. `pending_expires_at` is cleared (set to `NULL`).
5. SMS sent to patient: "Your appointment on {date} at {time} with Dr. {name} has been confirmed."
6. `NotificationLog` record created.

### 5.4 Reject Appointment

**Trigger:** Secretary clicks "Reject" on a PENDING_APPROVAL appointment.

**Flow:**

1. System verifies appointment is in `PENDING_APPROVAL` status.
2. Status transitions: `PENDING_APPROVAL → REJECTED`.
3. `pending_expires_at` is cleared.
4. Secretary may optionally provide a rejection reason.
5. SMS sent to patient: "Your appointment request for {date} has been declined. Please book a new time."
6. `NotificationLog` record created.

### 5.5 Propose Alternative Time

**Trigger:** Secretary clicks "Propose Alternative" on a PENDING_APPROVAL appointment.

**Flow:**

1. Secretary selects a new `proposed_start_at` and `proposed_end_at`.
2. System validates the proposed slot is available (no overlap with existing CONFIRMED/HOLD/PENDING appointments).
3. Status transitions: `PENDING_APPROVAL → PROPOSED_TIME`.
4. `proposed_start_at` and `proposed_end_at` are populated.
5. `pending_expires_at` is reset to `now() + 2 hours` (patient gets 2 hours to respond).
6. SMS sent to patient: "The clinic has proposed an alternative time: {proposed_date} at {proposed_time}. Please respond within 2 hours."
7. `NotificationLog` record created.

---

## 6. Intake Form Flow

### 6.1 Overview

Doctors can configure dynamic intake forms that patients must fill out during booking. Intake forms are defined per doctor (optionally per `AppointmentType`) using three models:

- `DoctorIntakeFormTemplate` — The form container, linked to a doctor.
- `DoctorIntakeQuestion` — Individual questions within a template.
- `DoctorIntakeRule` — Conditional display logic for questions.

### 6.2 Form Configuration (Doctor Side)

1. Doctor navigates to their intake form settings.
2. Creates a `DoctorIntakeFormTemplate` with a title (e.g., "New Patient Intake", "Follow-up Questionnaire").
3. Adds `DoctorIntakeQuestion` records with:
   - `question_text` / `question_text_ar` — Bilingual question content.
   - `field_type` — One of: `TEXT`, `TEXTAREA`, `SELECT`, `MULTISELECT`, `CHECKBOX`, `DATE`, `FILE`.
   - `choices` — JSON array for SELECT/MULTISELECT types.
   - `is_required` — Whether the question must be answered.
   - `order` — Display order.
4. Optionally adds `DoctorIntakeRule` records for conditional logic:
   - "Show question X only if question Y has answer Z."

### 6.3 Form Rendering (Patient Side — During Booking)

1. During Step 4 of the booking flow, after selecting appointment type:
   - System checks if the doctor has an active `DoctorIntakeFormTemplate` for this `AppointmentType`.
   - If yes, questions are loaded in order and rendered dynamically.
   - Conditional rules are evaluated client-side (JavaScript) and server-side (validation).
2. Patient fills in answers and optionally uploads attachments.

### 6.4 Form Submission

1. Answers are saved as `AppointmentAnswer` records linked to the `Appointment`.
2. File uploads are saved as `AppointmentAttachment` records.
3. All answers are validated:
   - Required fields must be non-empty.
   - File type and size restrictions are enforced.
   - Conditional rules are re-evaluated server-side.

### 6.5 Form Viewing (Doctor/Secretary Side)

- Intake answers are displayed on the `appointment_detail` view.
- Attachments are downloadable with proper access control (only the assigned doctor and clinic staff can view).

---

## 7. Slot Calculation Flow

### 7.1 Overview

Slot calculation is performed **on-the-fly** each time a patient requests available times. No pre-generated slot tables are stored. This ensures accuracy and eliminates stale data.

### 7.2 Algorithm

**Inputs:**

- `doctor_id` — The selected doctor.
- `clinic_id` — The selected clinic.
- `appointment_type_id` — Determines `duration_minutes`.
- `target_date` — The date the patient wants to book.

**Steps:**

1. **Retrieve Availability Windows**
   - Query `DoctorAvailability` where `doctor_id`, `clinic_id`, `day_of_week = target_date.weekday()`, and `is_active = True`.
   - Result: list of `(start_time, end_time)` windows for that day.

2. **Generate Candidate Slots**
   - For each availability window, generate slots of `duration_minutes` length:
     ```
     slot_start = window.start_time
     while slot_start + duration_minutes <= window.end_time:
         candidate_slots.append((slot_start, slot_start + duration_minutes))
         slot_start += duration_minutes
     ```

3. **Retrieve Blocking Appointments**
   - Query `Appointment` records for the same `doctor_id` on `target_date` where `status` is in:
     - `HOLD` (and `hold_expires_at > now()` — not yet expired)
     - `PENDING_APPROVAL`
     - `PROPOSED_TIME`
     - `CONFIRMED`
   - These represent time ranges that are **unavailable**.

4. **Filter Out Blocked Slots**
   - For each candidate slot, check if it overlaps with any blocking appointment:
     ```
     overlap = (slot.start_at < blocking.end_at) AND (slot.end_at > blocking.start_at)
     ```
   - Remove overlapping candidates.

5. **Filter Out Past Slots**
   - If `target_date == today`, remove any slots where `slot.start_at <= now()`.

6. **Return Available Slots**
   - Return the remaining candidate slots as the available options.

### 7.3 Performance Considerations

- The blocking appointments query should use the composite index on `(doctor_id, start_at, status)`.
- Availability lookup uses the existing `unique_doctor_clinic_day_start` constraint for efficient querying.
- Results can be cached per `(doctor_id, clinic_id, date)` with a short TTL (30 seconds) to reduce database load during high traffic.

---

## 8. HOLD Flow

### 8.1 Purpose

The HOLD mechanism prevents two patients from simultaneously claiming the same time slot. When a patient selects a slot, a 10-minute HOLD is placed, reserving it while the patient completes the booking form and intake questions.

### 8.2 Flow

1. **Patient Selects a Slot**
   - Patient clicks on an available time slot in the booking interface.
   - Client sends a request to create a HOLD.

2. **System Creates HOLD**
   - Within a database transaction with `SELECT ... FOR UPDATE` on overlapping appointments:
     - Verify no existing HOLD, PENDING_APPROVAL, PROPOSED_TIME, or CONFIRMED appointment occupies this slot.
     - If clear → create `Appointment` record:
       - `status = HOLD`
       - `start_at = selected_slot_start`
       - `end_at = selected_slot_end`
       - `hold_expires_at = now() + 10 minutes`
       - `patient_id = current_user.id`
       - `doctor_id`, `clinic_id`, `appointment_type_id` populated.
     - If occupied → return error: "This slot is no longer available."

3. **Patient Completes Booking**
   - Patient fills intake form and confirms booking.
   - On confirmation:
     - System verifies the HOLD has not expired (`hold_expires_at > now()`).
     - If valid → transition to `PENDING_APPROVAL` (or `CONFIRMED` if no approval required).
     - If expired → return error: "Your hold has expired. Please select a new time."

4. **HOLD Expiration**
   - A periodic task (every 2 minutes) queries:
     ```
     Appointment.objects.filter(status='HOLD', hold_expires_at__lte=now())
     ```
   - Expired HOLD records are transitioned to `EXPIRED`.
   - The slot becomes available for other patients.

### 8.3 Constraints

- A patient may hold **at most one slot** per doctor at a time.
- Selecting a new slot automatically releases any existing HOLD by the same patient for the same doctor.
- HOLD records are **not visible** to the patient in `my_appointments` (they are transient).

---

## 9. Pending Approval Flow

### 9.1 Purpose

When a clinic requires secretary approval, the appointment enters `PENDING_APPROVAL` after the patient completes the booking form. The secretary has **2 hours** to act before the system auto-expires the appointment.

### 9.2 Flow

1. **HOLD → PENDING_APPROVAL Transition**
   - Triggered when the patient completes the booking form and the clinic has approval enabled.
   - System updates the appointment:
     - `status = PENDING_APPROVAL`
     - `hold_expires_at = NULL` (HOLD is consumed)
     - `pending_expires_at = now() + 2 hours`
   - Intake answers are saved.
   - SMS sent to patient: "Your appointment request has been submitted. You will receive a confirmation within 2 hours."
   - Notification sent to clinic staff (secretary dashboard shows the new request).

2. **Secretary Reviews**
   - Secretary sees the appointment in their queue with all details:
     - Patient info, selected time, appointment type, intake answers.
   - Three actions available: **Accept**, **Reject**, **Propose Alternative Time**.
   - See Section 5 for detailed action flows.

3. **Timeout — Auto-Expire**
   - If no secretary action within 2 hours:
     - `pending_expires_at` is reached.
     - System transitions: `PENDING_APPROVAL → EXPIRED`.
     - SMS sent to patient: "Your appointment request has expired. Please book a new time."
   - See Section 12 for the auto-expire mechanism.

### 9.3 Constraints

- While in `PENDING_APPROVAL`, the time slot is **locked** — other patients cannot book overlapping times.
- The patient can cancel a `PENDING_APPROVAL` appointment, which transitions it to `CANCELLED` and frees the slot.

---

## 10. Alternative Time Proposal Flow

### 10.1 Purpose

When the secretary cannot approve the originally requested time but wants to offer a nearby alternative, they propose a new time. This creates a negotiation between the clinic and the patient.

### 10.2 Flow

1. **Secretary Selects Alternative Time**
   - Secretary opens the `PENDING_APPROVAL` appointment.
   - Selects new time from available slots (system shows the doctor's availability).
   - Confirms the proposal.

2. **System Processes Proposal**
   - Validates the proposed slot does not overlap with any existing CONFIRMED, HOLD, or PENDING_APPROVAL appointment.
   - Updates appointment:
     - `status = PROPOSED_TIME`
     - `proposed_start_at = new_start`
     - `proposed_end_at = new_end`
     - `pending_expires_at = now() + 2 hours` (patient has 2 hours to respond)
   - The **original** `(start_at, end_at)` is preserved for reference.
   - SMS sent to patient with the proposed time details.

3. **Slot Locking During Proposal**
   - The **original slot** `(start_at, end_at)` is released — no longer blocked.
   - The **proposed slot** `(proposed_start_at, proposed_end_at)` is now blocked for other patients.
   - The slot calculation engine (Section 7) must check both active `(start_at, end_at)` ranges AND `(proposed_start_at, proposed_end_at)` for `PROPOSED_TIME` appointments.

### 10.3 Constraints

- Only one proposal may be active at a time per appointment.
- The secretary can propose a new alternative even after a previous proposal was rejected by the patient — this cycles the appointment back to `PROPOSED_TIME` with updated proposed times.

---

## 11. Patient Response Flow

### 11.1 Accept Proposed Time

**Trigger:** Patient clicks "Accept" on the proposed time (from `my_appointments` or the SMS link).

**Flow:**

1. System verifies appointment is in `PROPOSED_TIME` status.
2. System verifies `pending_expires_at > now()` (not expired).
3. System verifies the proposed slot is still available (no conflict created since the proposal).
4. Updates appointment:
   - `start_at = proposed_start_at`
   - `end_at = proposed_end_at`
   - `proposed_start_at = NULL`
   - `proposed_end_at = NULL`
   - `status = CONFIRMED`
   - `pending_expires_at = NULL`
5. SMS sent to patient: "Your appointment has been confirmed for {new_date} at {new_time}."
6. Notification sent to clinic staff.

### 11.2 Reject Proposed Time

**Trigger:** Patient clicks "Reject" on the proposed time.

**Flow:**

1. System verifies appointment is in `PROPOSED_TIME` status.
2. Updates appointment:
   - `status = CANCELLED`
   - `proposed_start_at = NULL`
   - `proposed_end_at = NULL`
   - `pending_expires_at = NULL`
3. The proposed slot is released.
4. SMS sent to patient: "You have declined the proposed time. You may book a new appointment."
5. Notification sent to clinic staff.

### 11.3 Patient Does Not Respond

- If `pending_expires_at` is reached without patient action:
  - System transitions: `PROPOSED_TIME → EXPIRED`.
  - Both original and proposed slots are released.
  - SMS sent to patient: "Your appointment proposal has expired."

---

## 12. Auto-Expire Flow

### 12.1 Mechanism

A periodic background task runs every **2 minutes** to clean up stale appointments.

### 12.2 Expiration Rules

| Status              | Expiration Field     | Timeout     | Transition To |
|---------------------|----------------------|-------------|---------------|
| `HOLD`              | `hold_expires_at`    | 10 minutes  | `EXPIRED`     |
| `PENDING_APPROVAL`  | `pending_expires_at` | 2 hours     | `EXPIRED`     |
| `PROPOSED_TIME`     | `pending_expires_at` | 2 hours     | `EXPIRED`     |

### 12.3 Task Pseudocode

```
FUNCTION expire_stale_appointments():

    # 1. Expire HOLDs
    expired_holds = Appointment.objects.filter(
        status='HOLD',
        hold_expires_at__lte=now()
    )
    FOR each appointment IN expired_holds:
        appointment.status = 'EXPIRED'
        appointment.save()

    # 2. Expire PENDING_APPROVAL
    expired_pending = Appointment.objects.filter(
        status='PENDING_APPROVAL',
        pending_expires_at__lte=now()
    )
    FOR each appointment IN expired_pending:
        appointment.status = 'EXPIRED'
        appointment.save()
        send_sms(appointment.patient, "Your appointment request has expired.")
        create_notification_log(appointment, 'EXPIRED')

    # 3. Expire PROPOSED_TIME
    expired_proposals = Appointment.objects.filter(
        status='PROPOSED_TIME',
        pending_expires_at__lte=now()
    )
    FOR each appointment IN expired_proposals:
        appointment.status = 'EXPIRED'
        appointment.proposed_start_at = NULL
        appointment.proposed_end_at = NULL
        appointment.save()
        send_sms(appointment.patient, "Your appointment proposal has expired.")
        create_notification_log(appointment, 'EXPIRED')
```

### 12.4 Deployment Options

- **Option A (Recommended):** Celery Beat periodic task running every 2 minutes.
- **Option B:** Django management command invoked via cron: `python manage.py expire_appointments`.
- **Option C:** Lazy expiration — check on read and expire if stale. Less reliable but works without task infrastructure.

---

## 13. Reminder Flow (24h Before)

### 13.1 Purpose

Reduce no-shows by sending an SMS reminder 24 hours before a confirmed appointment.

### 13.2 Flow

1. **Periodic Task** runs every **30 minutes**.
2. Query:
   ```
   Appointment.objects.filter(
       status='CONFIRMED',
       start_at__range=(now() + 23.5 hours, now() + 24.5 hours)
   ).exclude(
       id__in=NotificationLog.objects.filter(
           notification_type='REMINDER_24H'
       ).values('appointment_id')
   )
   ```
3. For each matching appointment:
   - Send SMS: "Reminder: You have an appointment tomorrow at {time} with Dr. {name} at {clinic}."
   - Create `NotificationLog` with `notification_type = 'REMINDER_24H'`.
4. The `NotificationLog` check prevents duplicate reminders.

### 13.3 Edge Cases

- If an appointment is cancelled after the reminder is sent, no action needed — the cancellation SMS is sufficient.
- If the appointment time is changed (via alternative proposal accepted), the original reminder `NotificationLog` should be marked as void, and a new reminder cycle applies.

---

## 14. SMS Notification Flow

### 14.1 Notification Triggers

| Event                          | Recipient      | Message Summary                              |
|--------------------------------|----------------|----------------------------------------------|
| Booking submitted              | Patient        | "Your request has been submitted"            |
| Booking confirmed              | Patient        | "Your appointment is confirmed"              |
| Booking rejected               | Patient        | "Your request has been declined"             |
| Alternative time proposed      | Patient        | "A new time has been proposed: ..."          |
| Patient accepts proposal       | ClinicStaff    | "Patient accepted the proposed time"         |
| Patient rejects proposal       | ClinicStaff    | "Patient rejected the proposed time"         |
| PENDING/PROPOSED expired       | Patient        | "Your request has expired"                   |
| Appointment cancelled          | Doctor/Staff   | "Appointment cancelled by patient"           |
| 24h Reminder                   | Patient        | "Reminder: appointment tomorrow at ..."      |
| Appointment completed          | Patient        | "Thank you for your visit"                   |

### 14.2 SMS Delivery Architecture

1. All SMS is sent through the existing `TweetsMS` integration (see `accounts` app utils).
2. Every SMS attempt is logged in `NotificationLog` with:
   - `appointment_id` — the related appointment.
   - `recipient_phone` — the phone number.
   - `notification_type` — enum identifying the trigger.
   - `message_text` — the actual message content.
   - `status` — `SENT`, `FAILED`, `PENDING`.
   - `provider_response` — raw API response for debugging.
   - `sent_at` — timestamp.
3. Failed SMS are retried up to 3 times with exponential backoff (1 min, 5 min, 15 min).

### 14.3 Message Templates

- Messages are stored as configurable templates with placeholders:
  ```
  "مرحبًا {patient_name}، تم تأكيد موعدك مع د. {doctor_name} يوم {date} الساعة {time} في عيادة {clinic_name}."
  ```
- Arabic is the primary language. English fallback is optional.

---

## 15. Concurrency Protection Flow

### 15.1 Problem Statement

When **two patients** attempt to book the **same time slot** simultaneously, the system must guarantee that **exactly one** succeeds and the other receives a clear error.

### 15.2 Scenario: Two Patients, Same Slot

**Timeline:**

```
T0: Patient A views available slots → Slot 10:00–10:30 is AVAILABLE
T1: Patient B views available slots → Slot 10:00–10:30 is AVAILABLE (same result)
T2: Patient A clicks "Book 10:00–10:30" → Request reaches server
T3: Patient B clicks "Book 10:00–10:30" → Request reaches server (near-simultaneous)
```

### 15.3 Resolution via Database Locking

**Patient A's request (T2):**

1. Begin database transaction.
2. `SELECT ... FOR UPDATE` on `Appointment` rows where:
   - `doctor_id = X`
   - `start_at < 10:30` AND `end_at > 10:00` (overlap check)
   - `status IN ('HOLD', 'PENDING_APPROVAL', 'PROPOSED_TIME', 'CONFIRMED')`
3. No conflicting rows found → **INSERT** new `Appointment` with `status = HOLD`.
4. COMMIT transaction.
5. Patient A sees: "Slot reserved. Complete your booking within 10 minutes."

**Patient B's request (T3):**

1. Begin database transaction.
2. `SELECT ... FOR UPDATE` on same criteria → **BLOCKS** because Patient A's transaction holds the row lock (or the advisory lock on the slot range).
3. After Patient A's transaction commits, Patient B's `SELECT` now finds the newly created HOLD appointment.
4. Conflicting row exists → **ROLLBACK** transaction.
5. Patient B sees: "Sorry, this slot was just booked. Please select another time."

### 15.4 Implementation Strategy

```
Strategy: Pessimistic Locking with SELECT FOR UPDATE

1. Wrap the HOLD creation in:
   with transaction.atomic():
       # Lock all appointments that overlap the target slot
       conflicting = Appointment.objects.select_for_update().filter(
           doctor_id=doctor_id,
           start_at__lt=slot_end,
           end_at__gt=slot_start,
           status__in=['HOLD', 'PENDING_APPROVAL', 'PROPOSED_TIME', 'CONFIRMED']
       )
       if conflicting.exists():
           raise SlotUnavailableError()
       # Safe to create
       Appointment.objects.create(...)

2. Database-level constraint (belt-and-suspenders):
   PostgreSQL exclusion constraint using tstzrange to prevent
   ANY overlapping appointments for the same doctor with active statuses.
```

### 15.5 Additional Concurrency Guards

- **Idempotency:** Each HOLD request includes a client-generated `idempotency_key`. Duplicate requests within the HOLD window return the existing HOLD instead of creating a new one.
- **Optimistic UI Refresh:** After a failed booking attempt, the slot grid is automatically refreshed to show current availability.
- **Rate Limiting:** Each patient is limited to 5 booking attempts per minute to prevent abuse.

---

## 16. State Machine Diagram

### 16.1 Status Definitions

| Status              | Description                                              |
|---------------------|----------------------------------------------------------|
| `HOLD`              | Slot temporarily reserved while patient completes form   |
| `PENDING_APPROVAL`  | Submitted, awaiting secretary review                     |
| `PROPOSED_TIME`     | Secretary proposed alternative time, awaiting patient    |
| `CONFIRMED`         | Approved and scheduled                                   |
| `REJECTED`          | Declined by secretary                                    |
| `EXPIRED`           | Timed out (HOLD, PENDING, or PROPOSED)                   |
| `CANCELLED`         | Cancelled by patient or clinic                           |
| `COMPLETED`         | Visit completed                                          |
| `NO_SHOW`           | Patient did not attend                                   |

### 16.2 State Transition Diagram

```
                          ┌─────────────────────────────────────────┐
                          │                                         │
                          ▼                                         │
    ┌──────┐    complete form    ┌──────────────────┐               │
    │ HOLD │ ──────────────────► │ PENDING_APPROVAL │               │
    └──┬───┘                     └────────┬─────────┘               │
       │                                  │                         │
       │ expire (10 min)         ┌────────┼──────────┐              │
       │                         │        │          │              │
       ▼                         ▼        ▼          ▼              │
  ┌─────────┐            ┌──────────┐ ┌────────┐ ┌───────────────┐ │
  │ EXPIRED │            │ CONFIRMED│ │REJECTED│ │ PROPOSED_TIME │ │
  └─────────┘            └────┬─────┘ └────────┘ └──────┬────────┘ │
       ▲                      │                          │          │
       │                ┌─────┼─────┐          ┌─────────┼────────┐ │
       │                │     │     │          │         │        │ │
       │                ▼     ▼     ▼          ▼         ▼        │ │
       │         ┌─────────┐ ┌──┐ ┌───────┐ ┌─────────┐ ┌────────┘ │
       │         │COMPLETED│ │  │ │CANCEL-│ │ EXPIRED │ │CANCELLED │
       │         └─────────┘ │  │ │  LED  │ └─────────┘ └──────────┘
       │                     │  │ └───────┘
       │                     ▼  │
       │              ┌─────────┐
       │              │ NO_SHOW │
       │              └─────────┘
       │
       └──── expire (2 hours): PENDING_APPROVAL / PROPOSED_TIME
```

### 16.3 Transition Rules

| From                | To                  | Trigger                                    | Actor     |
|---------------------|---------------------|--------------------------------------------|-----------|
| `HOLD`              | `PENDING_APPROVAL`  | Patient completes booking form             | Patient   |
| `HOLD`              | `CONFIRMED`         | Direct confirm (no approval required)      | System    |
| `HOLD`              | `EXPIRED`           | `hold_expires_at` reached                  | System    |
| `PENDING_APPROVAL`  | `CONFIRMED`         | Secretary accepts                          | Secretary |
| `PENDING_APPROVAL`  | `REJECTED`          | Secretary rejects                          | Secretary |
| `PENDING_APPROVAL`  | `PROPOSED_TIME`     | Secretary proposes alternative             | Secretary |
| `PENDING_APPROVAL`  | `EXPIRED`           | `pending_expires_at` reached               | System    |
| `PENDING_APPROVAL`  | `CANCELLED`         | Patient cancels                            | Patient   |
| `PROPOSED_TIME`     | `CONFIRMED`         | Patient accepts proposed time              | Patient   |
| `PROPOSED_TIME`     | `CANCELLED`         | Patient rejects proposed time              | Patient   |
| `PROPOSED_TIME`     | `EXPIRED`           | `pending_expires_at` reached               | System    |
| `CONFIRMED`         | `COMPLETED`         | Doctor/secretary marks completed           | Doctor    |
| `CONFIRMED`         | `NO_SHOW`           | Doctor/secretary marks no-show             | Doctor    |
| `CONFIRMED`         | `CANCELLED`         | Patient or clinic cancels                  | Any       |

### 16.4 Terminal States

The following statuses are **final** — no further transitions are allowed:

- `COMPLETED`
- `NO_SHOW`
- `REJECTED`
- `EXPIRED`
- `CANCELLED`

---

## 17. Error and Edge Cases Handling

### 17.1 Slot No Longer Available

- **Scenario:** Patient selects a slot, but by the time the server processes the HOLD request, another patient has booked it.
- **Handling:** The `SELECT FOR UPDATE` mechanism detects the conflict. The patient receives a clear error message and the slot grid refreshes automatically.

### 17.2 HOLD Expired During Form Filling

- **Scenario:** Patient takes longer than 10 minutes to complete the booking form.
- **Handling:** On form submission, the system checks `hold_expires_at`. If expired, the patient is informed and must re-select a slot. Partially filled intake data can be preserved in the session for convenience.

### 17.3 Doctor Availability Changed After Slot Display

- **Scenario:** Doctor deactivates an availability window while a patient is viewing slots.
- **Handling:** Slot availability is re-validated at HOLD creation time. If the availability window no longer exists, the HOLD is rejected.

### 17.4 Double Cancellation

- **Scenario:** Patient clicks "Cancel" twice rapidly.
- **Handling:** The status transition check ensures the appointment is still in a cancellable state. Second request returns "Appointment already cancelled" without side effects.

### 17.5 Secretary Approves Expired PENDING

- **Scenario:** Secretary clicks "Accept" moments after the appointment expires.
- **Handling:** The approval action first verifies `status == PENDING_APPROVAL`. Since the auto-expire task already changed it to `EXPIRED`, the action is rejected with "This appointment has already expired."

### 17.6 Patient Books Multiple Appointments Same Day

- **Scenario:** Patient tries to book two appointments with the same doctor on the same day.
- **Handling:** This is **allowed** (different appointment types may warrant multiple visits). However, overlapping time slots for the same patient+doctor are prevented by the overlap check.

### 17.7 Network Timeout During Booking

- **Scenario:** Patient's browser loses connection after the HOLD is created but before intake form is submitted.
- **Handling:** The HOLD auto-expires after 10 minutes, releasing the slot. If the patient reconnects, they can resume — the HOLD is checked on page reload.

### 17.8 SMS Delivery Failure

- **Scenario:** SMS provider is unreachable or returns an error.
- **Handling:**
  1. First attempt failure is logged in `NotificationLog` with `status = FAILED`.
  2. Up to 3 retries with exponential backoff.
  3. After all retries fail, the `NotificationLog` is marked `PERMANENTLY_FAILED`.
  4. SMS failure **never blocks** the appointment status transition — the appointment proceeds, and the notification is best-effort.

### 17.9 Timezone Handling

- All `start_at`, `end_at`, `hold_expires_at`, `pending_expires_at` fields are stored in **UTC**.
- Display conversion to the clinic's local timezone is done at the view layer.
- The `DoctorAvailability` times (`start_time`, `end_time`) are in the clinic's local timezone since they represent recurring schedules.

### 17.10 Edge Case Summary Table

| Edge Case                          | Detection                          | Resolution                              |
|------------------------------------|------------------------------------|-----------------------------------------|
| Concurrent booking                 | `SELECT FOR UPDATE`                | Second requester sees error             |
| HOLD timeout                       | `hold_expires_at` check            | Auto-expire + patient notified          |
| PENDING timeout                    | `pending_expires_at` check         | Auto-expire + SMS                       |
| Stale slot data                    | Re-validation at HOLD creation     | Reject + refresh UI                     |
| Availability change mid-booking    | Re-check at HOLD time              | Reject + inform patient                 |
| SMS failure                        | `NotificationLog.status`           | Retry queue + no booking blockage       |
| Invalid state transition           | Pre-condition check                | Reject action + log warning             |
| Past date booking                  | Server-side validation             | Reject with "Cannot book past dates"    |
| Overlapping availability (doctor)  | `DoctorAvailability.clean()`       | Reject at configuration time            |

---

> **End of Workflow Specification**
