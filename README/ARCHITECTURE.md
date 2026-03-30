# System Architecture

> **Last updated**: 2026-03-17
> Reflects the **current implemented system**. Planned/future items are explicitly labelled.

---

## 1. High-Level Architecture

The system is a **Single-Database, Shared-Schema Multi-Tenant SaaS** built on Django.
All tenants (clinics) share the same database tables. Data isolation is enforced via
`clinic_id` filtering at the view and service layer.

### Tenant Isolation Strategy
- **Strict Filtering** — every query for tenant-specific data scopes by `clinic_id`.
- **Global Resources** — `CustomUser`, `DoctorProfile`, `PatientProfile` are global;
  a user can belong to multiple clinics simultaneously.
- **Local Resources** — `Appointment`, `ClinicStaff`, `ClinicWorkingHours`,
  `DoctorAvailability`, `PatientClinicCompliance`, `ClinicHoliday`,
  `DoctorAvailabilityException` are scoped to a specific clinic.

> ~~`ClinicPatient`~~ — does **not** exist. Per-clinic patient records are planned but
> not implemented. Patient identity within a clinic is tracked via `Appointment` records.

---

## 2. Django App Structure

```
clinic_website/          ← Project config (settings, root URLs)
accounts/                ← CustomUser, auth (login/register/OTP/JWT), IdentityClaim
clinics/                 ← Clinic CRUD, ClinicStaff, invitations, working hours,
                            ClinicHoliday, DoctorAvailabilityException,
                            ClinicSubscription, compliance settings, credential review
doctors/                 ← DoctorProfile, availability, verification, intake forms
patients/                ← PatientProfile, patient portal views, booking interface
appointments/            ← Appointment model, booking service, notification service,
                            intake answers, appointment types
secretary/               ← Secretary role (dashboard, appointment management, invitations)
compliance/              ← No-show tracking, compliance scores, auto-forgiveness
core/                    ← Shared validators (file upload security)
```

---

## 3. Authentication & Authorization

### Session (Web)
Standard Django sessions for all web views. `@login_required` is used throughout.

### JWT (API)
SimpleJWT via `accounts/api_views.py`. Token obtain/refresh/blacklist endpoints.
Used by the patient profile API and mobile clients.

### Role System
`CustomUser` has two role fields:
- `role` (CharField) — the user's **primary** (highest-privilege) role
- `roles` (ArrayField) — all roles the user currently holds

Roles: `PATIENT`, `SECRETARY`, `DOCTOR`, `MAIN_DOCTOR`

`MAIN_DOCTOR` (Clinic Owner) portal includes a real-time dashboard with operational metrics (today's revenue, pending appointments) and a dedicated reports center with filtering by clinic, doctor, and date.

`home_redirect` view routes users to the correct dashboard based on their actual data:
clinic ownership > doctor profile > secretary membership > patient (default).

### Multi-Role Users
A single user can hold multiple roles simultaneously (e.g., a clinic owner who is
also a patient at another clinic). Role expansion happens via invitation acceptance
in `clinics/services.accept_invitation()`.

---

## 4. Clinic Context & Middleware

`clinics/middleware.py` runs on every request:
- Sets `request.selected_clinic` from `request.session["selected_clinic_id"]`
- Used by multi-clinic owners to scope operations to the currently active clinic

`clinics/context_processors.py` injects clinic context into all templates.

Clinic switching: `clinics:switch_clinic` view updates the session variable and
redirects to the switched clinic's dashboard.

---

## 5. Doctor Availability Architecture

Slot generation is **stateless** — no pre-generated time slot table exists.

`doctors/services.generate_slots_for_date(doctor_id, clinic_id, target_date, duration_minutes)`:

1. **Holiday check** — queries `ClinicHoliday` for any active holiday covering `target_date`;
   returns `[]` immediately if found (blocks all slots for all doctors).
2. **Doctor exception check** — queries `DoctorAvailabilityException` for an active exception
   for this specific doctor/clinic/date; returns `[]` if found.
3. Loads `DoctorAvailability` records (weekly schedule) for the doctor/clinic/weekday.
4. Generates candidate time slots at `duration_minutes` intervals within each window.
5. Loads all `CONFIRMED`/`COMPLETED` appointments for the doctor **across all clinics**
   on that date (global conflict check — R-03).
6. Marks slots unavailable if they overlap any existing appointment.
7. Returns the full slot list with `is_available` flags.

`ClinicWorkingHours` defines the outer boundary. `DoctorAvailability.clean()` validates
that a doctor's window falls within at least one clinic working hours range.

### Defense in Depth
Both `generate_slots_for_date` AND `booking_service.book_appointment()` independently
check `ClinicHoliday` and `DoctorAvailabilityException`. This ensures blocking even if
slots are requested via the API directly.

---

## 6. Subscription & Plan Tier Architecture

`ClinicSubscription` is a OneToOne record bound to each clinic at creation time,
seeded from the `ClinicActivationCode`.

### Plan Tiers (`ClinicSubscription.PLAN_LIMITS`)
| Plan | max_doctors | max_secretaries |
|---|---|---|
| SMALL | 2 | 5 |
| MEDIUM | 4 | 5 |
| ENTERPRISE | admin-set | admin-set |

ENTERPRISE plans have no tier defaults in `PLAN_LIMITS`. The admin sets `max_doctors`
and `max_secretaries` explicitly on the `ClinicActivationCode` or `ClinicSubscription`.
**`0 = unlimited`** — this is an explicit admin opt-in, not a default.

### Effective Active Check
`subscription.is_effectively_active()` returns `True` only when:
- `status == "ACTIVE"` AND
- `expires_at > timezone.now()`

This is what `book_appointment()` calls. A subscription with `status=ACTIVE` but
a past `expires_at` is treated as inactive.

### Admin Billing Actions (Django Admin)
All actions in `ClinicSubscriptionAdmin` stamp `activated_by = request.user`:
- **Activate** — sets `status = ACTIVE`
- **Suspend** — sets `status = SUSPENDED`
- **Extend 30 days** — extends `expires_at` by 30 days, sets `status = ACTIVE`
- **Extend 365 days** — extends `expires_at` by 365 days, sets `status = ACTIVE`

---

## 7. Invitation & Onboarding Architecture

### Doctor/Secretary Invitation Flow
1. Clinic owner creates an invitation via `clinics:create_invitation` or
   `clinics:create_secretary_invitation`
2. `clinics/services.create_invitation()` normalizes phone, checks rate limits,
   validates identity, creates `ClinicInvitation` + `PendingDoctorIdentity` (DOCTOR only),
   sends invitation email
3. Email contains a link to `doctors:guest_accept_invitation` (universal public endpoint)
4. Unauthenticated user: stores token in session → redirects to login/register
5. `handle_pending_invitation_redirect()` in `accounts/views.py` consumes the session
   token after login and redirects to the correct inbox:
   - DOCTOR → `doctors:doctor_invitations_inbox`
   - SECRETARY → `secretary:secretary_invitations_inbox`
6. User accepts via the inbox → `clinics/services.accept_invitation()` creates
   `ClinicStaff`, sets up `DoctorProfile`/`DoctorVerification`/`ClinicDoctorCredential`
   (DOCTOR), or adds SECRETARY role (SECRETARY), updates `CustomUser.roles`

### Identity Lock (`PendingDoctorIdentity`)
For unregistered phones being invited as DOCTORs, a `PendingDoctorIdentity` record
is created to prevent race conditions when multiple clinics invite the same unknown phone.
Released (deleted) when the invitation is accepted.

---

## 8. Appointment Notification Architecture

`appointments/services/appointment_notification_service.py` is the **central notification service**.

### Rules
- **Context Isolation**: Every `AppointmentNotification` includes a `context_role` (`PATIENT`, `DOCTOR`, `SECRETARY`, `CLINIC_OWNER`) representing its target portal.
- In-app `AppointmentNotification` is **always** created first and explicitly bound to this `context_role`.
- Notification Centers are split into four strict endpoints (`appointments:patient_notifications`, `appointments:doctor_notifications`, `appointments:secretary_notifications`, `appointments:clinic_owner_notifications`) to prevent cross-context routing bugs for multi-role users.
- The **Clinic Owner** (`MAIN_DOCTOR`) receives `CLINIC_OWNER`-context notifications for operational events (patient cancellations, patient edits) in their owned clinics. The owner bell in the `clinics` navbar routes exclusively to the owner endpoint, never to the doctor endpoint.
- Email is sent only if `user.email` is set AND `user.email_verified = True`.
- Email failures are caught and logged — never raised to callers.
- `notification.sent_via_email = True` is set after successful email delivery.
- All notify_* functions are safe to call from `transaction.on_commit()`.

### Public API functions
| Function | Trigger |
|---|---|
| `notify_appointment_booked(appointment)` | Patient self-books or secretary books |
| `notify_appointment_cancelled_by_staff(appointment, clinic_staff)` | Doctor or secretary cancels |
| `notify_appointment_rescheduled_by_staff(appointment, old_date, old_time)` | Secretary reschedules |
| `notify_staff_patient_cancelled(appointment)` | Patient cancels (notifies doctor + secretaries) |
| `notify_staff_patient_edited(appointment, old_date, old_time, old_type)` | Patient edits (notifies doctor + secretaries) |
| `notify_appointment_reminder(appointment)` | Called by `send_appointment_reminders` command |

### Email functions (`accounts/email_utils.py`)
- `send_appointment_booking_email(patient, appointment)`
- `send_appointment_cancellation_email(user, appointment)`
- `send_appointment_reminder_email(patient, appointment)`
- `send_appointment_rescheduled_email(patient, appointment, old_date, old_time)`

---

## 9. Appointment Reminders Management Command

`appointments/management/commands/send_appointment_reminders.py`

```
python manage.py send_appointment_reminders
```

- Looks for `CONFIRMED` appointments within the next 24 hours with `reminder_sent=False`
- Calls `notify_appointment_reminder(appointment)`
- Sets `appointment.reminder_sent = True`
- Idempotent — safe to run multiple times via cron

---

## 10. Compliance Architecture

`PatientClinicCompliance` tracks per-patient per-clinic no-show scores.

Flow:
1. `process_no_shows` management command marks missed `PENDING`/`CONFIRMED` appointments as `NO_SHOW`
2. Compliance service increments `bad_score` and sets status to `BLOCKED` if threshold exceeded
3. `is_patient_blocked(clinic, patient)` is called in `booking_service.book_appointment()`
   before creating any appointment
4. `run_auto_forgiveness` management command resets scores after dormancy period

---

## 11. File Upload Security

All file uploads go through `core/validators/file_validators.py`:
- `validate_file_extension` — allowed: `.jpg`, `.jpeg`, `.png`, `.pdf`
- `validate_file_signature` — MIME magic bytes check
- `validate_file_size` — configurable max size

Applied to: `IdentityClaim.evidence_file`, `DoctorVerification` documents,
`ClinicDoctorCredential.specialty_certificate`, `AppointmentAttachment` files.

---

## 12. Frontend Stack

- **Server-side rendering** — Django templates (Arabic RTL, Bootstrap-based)
- **Dynamic interactions** — HTMX for booking wizard steps, slot loading, intake form loading,
  appointment type loading
- **No SPA framework** — all interactivity via HTMX partial renders

---

## 13. Deployment

See `DEPLOY_RENDER.md` for deployment configuration details (Render.com).

Key settings:
- PostgreSQL (with `contrib.postgres.fields.ArrayField`)
- WhiteNoise for static files
- `DEBUG = False` in production; `ALLOWED_HOSTS` set from environment
- Media files: configured via `MEDIA_ROOT` / `MEDIA_URL`
