# Business Rules

> **Last updated**: 2026-03-17
> Reflects the **current implemented system**. Rules marked **[PLANNED]** describe intended
> behaviour that is not yet enforced in code.

---

## 1. Tenant & Clinic Isolation

### R-01: Strict Data Isolation
Every operation performed by Clinic Staff MUST be scoped to their associated `clinic_id`.
They cannot view, edit, or create records for any other clinic.

**Implementation**: `clinics/middleware.py` sets `request.selected_clinic` from session.
All clinic-scoped views check ownership via `clinic.main_doctor == request.user` or
`ClinicStaff.objects.filter(clinic=clinic, user=request.user)`.

### R-02: User Uniqueness
A User is identified globally by their **Phone Number** (primary) and **National ID**
(secondary, optional).

A user cannot register with the same phone number twice. National ID uniqueness is enforced
at the `IdentityClaim` level — only one `VERIFIED` claim may exist per national ID.

**Implementation**: `CustomUser.phone` has `unique=True`.
`IdentityClaim` has `UniqueConstraint` for `status=VERIFIED` per national ID and per user.

---

## 2. Doctor Availability & Scheduling

### R-03: Global Doctor Conflict Prevention
A Doctor CANNOT have overlapping appointments across ANY clinic.

**Implementation**: `generate_slots_for_date` loads all confirmed appointments for the doctor
across all clinics and marks conflicting slots as unavailable. `DoctorAvailability.clean()`
also validates that new availability windows don't overlap cross-clinic.

### R-04: Schedule Bounded by Clinic Hours
Doctors define working hours **per clinic** via `DoctorAvailability`.

`DoctorAvailability.clean()` validates that the proposed window falls within at least one
`ClinicWorkingHours` range for that day. If the clinic marks a day as closed, no availability
can be set for that day.

### R-24: Clinic Holiday Blocks All Slots
When an active `ClinicHoliday` record covers a date, `generate_slots_for_date` returns
an empty list and `book_appointment` raises `BookingError(code="clinic_holiday")`.

This blocks **all doctors** at the clinic for all dates within `start_date..end_date`
(inclusive) when `is_active=True`.

**Implementation**: checked in both `doctors/services.generate_slots_for_date` AND
`appointments/services/booking_service.book_appointment` (defense in depth).

### R-25: Doctor Availability Exception Blocks Specific Doctor
When an active `DoctorAvailabilityException` covers a date for a specific doctor at a
specific clinic, `generate_slots_for_date` returns an empty list and `book_appointment`
raises `BookingError(code="doctor_exception")`.

**Implementation**: checked in both `doctors/services.generate_slots_for_date` AND
`appointments/services/booking_service.book_appointment` (defense in depth).

---

## 3. Patient Management

### R-05: Global Profile Only (No Per-Clinic Record)
Patients have a **single global profile** (`PatientProfile`) containing demographic data
(name, DOB, gender, blood type, medical history, allergies).

> **Correction to earlier documentation**: There is **no `ClinicPatient` model**.
> A per-clinic patient record was planned but has not been implemented.
> Patient identification within a clinic is done through `Appointment` records scoped
> to the clinic. Clinic-specific notes and local file numbers are **[PLANNED]**.

### R-06: Visit History Privacy
A clinic can only see appointments that occurred at **their facility** (scoped by `clinic_id`
on the `Appointment` model). Cross-clinic history sharing requires explicit patient consent
and is **[PLANNED]**.

---

## 4. Appointment Booking

### R-07: Booking Authority
- **Patients** can self-book directly through the patient portal.
- **Secretaries** can book on behalf of patients via `secretary:create_appointment`.
  Secretary-created appointments start with `status = CONFIRMED`.
  The secretary is recorded as `appointment.created_by`.

### R-08: Appointment Status Lifecycle
Current implemented statuses (7):

| # | Status | Meaning |
|---|---|---|
| 1 | `PENDING` | Created but not yet acted on (used in patient self-booking fallback; not the normal initial state) |
| 2 | `CONFIRMED` | Default state after booking or secretary creation |
| 3 | `CHECKED_IN` | Patient arrived at clinic |
| 4 | `IN_PROGRESS` | Consultation started |
| 5 | `COMPLETED` | Consultation finished |
| 6 | `CANCELLED` | Cancelled by patient, doctor, or clinic |
| 7 | `NO_SHOW` | Patient did not arrive (set by `process_no_shows` management command) |

**[PLANNED]** Additional statuses: `HOLD`, `PENDING_APPROVAL`, `PROPOSED_TIME`,
`EXPIRED`, `REJECTED`. See `README/WORKFLOWS/APPOINTMENT_BOOKING_WORKFLOW.md` Section 12.

### R-08a: Doctor Status Transition Map
The `_TRANSITION_MAP` in `doctors/views.appointment_detail` defines what transitions a doctor
can trigger:

```
PENDING      → CONFIRMED, CANCELLED
CONFIRMED    → CHECKED_IN, CANCELLED, NO_SHOW
CHECKED_IN   → IN_PROGRESS
IN_PROGRESS  → COMPLETED
COMPLETED    → [terminal — no transitions]
CANCELLED    → [terminal — no transitions]
NO_SHOW      → [terminal — no transitions]
```

Backend enforcement: only whitelisted values are accepted from POST. Stale or tampered
POST values are silently ignored.

When a doctor cancels (transitions to CANCELLED), `notify_appointment_cancelled_by_staff`
is called via `transaction.on_commit()`.

### R-08b: Secretary Cancel
Secretaries can cancel any non-terminal appointment (not COMPLETED, CANCELLED, or NO_SHOW)
via `secretary:cancel_appointment`. Delegates to `cancel_appointment_by_staff()` in
`patient_appointments_service.py`, which notifies the patient.

Secretaries cannot edit appointments in `CHECKED_IN` or `IN_PROGRESS` status.

### R-09: Patient Edit Limit
Patients may edit their appointment (reschedule + re-submit intake form) a maximum of
**2 times** (`MAX_PATIENT_EDITS = 2`). After this limit, the patient must cancel and rebook.

**Implementation**: `Appointment.patient_edit_count` is incremented on each edit.
The edit view rejects edits when `patient_edit_count >= MAX_PATIENT_EDITS`.

### R-10: Patient Cancellation Window
Patients can only cancel their own `PENDING` or `CONFIRMED` appointments.
Cancellation is blocked within **2 hours** of the appointment time
(`CANCELLATION_WINDOW_HOURS = 2` in `patient_appointments_service.py`).

The same 2-hour window applies to patient edits.

### R-11: Intake Questionnaires
Doctors can define pre-appointment intake forms (`DoctorIntakeFormTemplate`).
Questions may be mandatory or optional. Conditional show/hide rules are supported.
Answers are collected during booking and stored as `AppointmentAnswer` records.

**Implementation**: Fully implemented. See `README/WORKFLOWS/APPOINTMENT_BOOKING_WORKFLOW.md`
Section 5 for details.

### R-12: Appointment Attachments
Patients can upload files during booking (as intake form responses with `FILE` or
`DATED_FILES` field types). Files are stored as `AppointmentAttachment` records linked
to the appointment.

File uploads are validated for extension, MIME signature, and size by
`core/validators/file_validators.py`.

---

## 5. Subscription & Plan Limits

### R-26: Plan Tier Capacity Limits
Each clinic's subscription defines maximum staff counts. Limits by tier:

| Plan | `max_doctors` | `max_secretaries` |
|---|---|---|
| SMALL | 2 | 5 |
| MEDIUM | 4 | 5 |
| ENTERPRISE | admin-set | admin-set |

**Source**: `ClinicSubscription.PLAN_LIMITS` dict in `clinics/models.py`.

ENTERPRISE plans are intentionally absent from `PLAN_LIMITS`. The admin sets
`max_doctors` and `max_secretaries` directly on the `ClinicActivationCode` or
`ClinicSubscription` for each enterprise clinic.

**`max_doctors = 0` or `max_secretaries = 0` means unlimited** — this is an explicit
admin opt-in, not a default. Used for ENTERPRISE plans where unlimited capacity is granted.

### R-27: Subscription Active Check
For booking to be allowed, the clinic's subscription must be **effectively active**:
`subscription.is_effectively_active()` returns `True` only when both
`status == "ACTIVE"` AND `expires_at > timezone.now()`.

A subscription with `status=ACTIVE` and a past `expires_at` is treated as inactive
and blocks new bookings.

**Implementation**: checked in `book_appointment()`. If no `ClinicSubscription` record
exists for the clinic, booking is allowed (backward-compatible fallback).

### R-28: Doctor/Secretary Cap Enforcement
- Checked at **invitation creation** (`create_invitation()`)
- Re-checked at **invitation acceptance** inside `select_for_update()` to prevent races
- `can_add_doctor()` and `can_add_secretary()` methods on `ClinicSubscription`

---

## 6. Billing & Invoicing

### R-10: Invoice Generation
**[PLANNED]** — billing and invoicing are not yet implemented.

---

## 7. Authentication Rules

### R-13: Login Credentials
Users authenticate using **Phone Number** + **Password**.
National ID is used only for identity verification and uniqueness enforcement — not for login.

### R-14: Identity Field Updates Require Verification
Phone number and email changes require OTP/token verification of the **new** value before
the change is committed. Neither can be changed via a plain form update.

**Implementation**: `change_phone_request` + `change_phone_verify` views (OTP-based);
`change_email_request` + `verify_change_email` views (token-based).

### R-15: Registration Atomicity
Registration flows use `@transaction.atomic` to prevent partial/ghost accounts.

---

## 8. Patient Compliance (No-Shows)

### R-16: Clinic-Based Compliance Scores
A patient's no-show score is maintained **per clinic** independently.
A high no-show rate at Clinic A does not block the patient at Clinic B.

When the patient's `bad_score >= score_threshold_block` (configured per clinic),
their `PatientClinicCompliance.status` is set to `BLOCKED`. Blocked patients
cannot book appointments at that clinic.

**Implementation**: `compliance/services/compliance_service.py`:
- `is_patient_blocked(clinic, patient)` — called by booking service before creating an appointment
- `record_no_show(appointment)` — called by `process_no_shows` management command
- Auto-forgiveness: `run_auto_forgiveness()` resets scores after a configurable dormancy period

---

## 9. Invitation Rules

### R-17: Phone as Primary Identity Key
When inviting a doctor or secretary, the **phone number** is the sole identity key.
Email is the delivery destination, not the identity key.

**Implementation**: `clinics/services.create_invitation()` normalizes phone via
`PhoneNumberAuthBackend.normalize_phone_number()`, then checks for existing users
by phone. Email mismatches are resolved by using the stored email for delivery.

### R-18: Invitation Rate Limiting
- Max **3 invitations per phone per hour** (across all clinics)
- Max **10 invitations per clinic per hour**

**Implementation**: `_check_invitation_rate_limits()` in `clinics/services.py`.

### R-19: Staff Subscription Cap
The number of active DOCTOR-role `ClinicStaff` members cannot exceed `ClinicSubscription.max_doctors`.
The number of active SECRETARY-role `ClinicStaff` members cannot exceed `ClinicSubscription.max_secretaries`.
Both are checked at invitation creation and at acceptance.

### R-20: Invitation Immutability After Expiry
Expired invitations (`is_expired == True`) are marked `EXPIRED` and become immutable.
A new invitation must be created to re-invite the same person.

**Implementation**: Enforced in `create_invitation()` before creating a new record.

### R-21: Revoked Memberships Allow Re-Invitation
A `ClinicStaff` record with `revoked_at` set does **not** block a new invitation.
On acceptance, the revoked record is reactivated.

**Implementation**: `accept_invitation()` checks `revoked_at` and reactivates if found.

---

## 10. Clinic Working Hours & Holidays

### R-22: Doctor Availability Within Clinic Hours
Doctor availability windows must fall within at least one `ClinicWorkingHours` range
for the same day. If no working hours are configured for a day, the constraint is
not enforced (backward-compatible).

**Implementation**: `clinics/services.validate_doctor_availability_within_clinic_hours()`
called from `DoctorAvailability.clean()`.

### R-23: No Overlapping Working Hour Ranges
Multiple time ranges per day are allowed, but they must not overlap.
A day can be marked as `is_closed = True`, which prevents any time ranges from being added.

**Implementation**: `ClinicWorkingHours.clean()` enforces all overlap and closure constraints.
`ClinicWorkingHours.save()` calls `full_clean()` to always trigger these checks.

### R-24: See Section 2 (R-24: Clinic Holiday Blocks All Slots)

### R-25: See Section 2 (R-25: Doctor Availability Exception Blocks Specific Doctor)
