# Implemented Features Reference

> **Last updated**: 2026-03-17
>
> This document describes features that are **fully implemented** in the codebase.

---

## 1. Clinic Working Hours (`ClinicWorkingHours`)

**App**: `clinics`
**Model**: `ClinicWorkingHours`
**Routes**: `clinics:working_hours_list`, `clinics:working_hours_create`,
`clinics:working_hours_update`, `clinics:working_hours_delete`
**URL prefix**: `/clinics/<clinic_id>/settings/working-hours/`

### What it does
Defines the clinic's operating days and hours. Doctors cannot schedule availability
outside these bounds.

### Fields
| Field | Description |
|---|---|
| `clinic` | FK to `Clinic` |
| `weekday` | 0=Monday ... 6=Sunday |
| `start_time` / `end_time` | Working range (null when `is_closed=True`) |
| `is_closed` | If `True`, clinic is closed on this day (no time range allowed) |

### Rules enforced in `ClinicWorkingHours.clean()`
- If `is_closed=True`: `start_time` and `end_time` must be null; no other ranges
  may exist for this day
- If `is_closed=False`: times are required; new range must not overlap existing ranges
- `save()` always calls `full_clean()` to enforce these rules

### Relationship to Doctor Availability
`DoctorAvailability.clean()` calls
`clinics.services.validate_doctor_availability_within_clinic_hours()` to ensure
doctor windows fall within at least one clinic working hours range.
If no working hours are configured for a day, the constraint is not enforced
(backward-compatible for clinics that haven't configured hours yet).

---

## 2. Clinic Holidays (`ClinicHoliday`)

**App**: `clinics`
**Model**: `ClinicHoliday`
**Admin**: `clinics/admin.ClinicHolidayAdmin`

### What it does
Marks date ranges when the entire clinic is closed. No bookings are allowed and
no slots are generated for any doctor on dates within an active holiday range.

### Fields
| Field | Description |
|---|---|
| `clinic` | FK to `Clinic` |
| `title` | Human-readable label (e.g. "عطلة عيد الأضحى") |
| `start_date` / `end_date` | Inclusive date range |
| `is_active` | `True` = holiday is enforced |
| `created_by` | FK to `CustomUser` |

### Enforcement (defense in depth)
Checked in TWO places:
1. `doctors/services.generate_slots_for_date` — returns `[]` immediately if holiday active
2. `appointments/services/booking_service.book_appointment` — raises `BookingError(code="clinic_holiday")`

---

## 3. Doctor Availability Exceptions (`DoctorAvailabilityException`)

**App**: `clinics`
**Model**: `DoctorAvailabilityException`
**Admin**: `clinics/admin.DoctorAvailabilityExceptionAdmin`

### What it does
Marks date ranges when a **specific doctor** is unavailable at a **specific clinic**.
Slots will not be generated for that doctor during the exception range.

### Fields
| Field | Description |
|---|---|
| `doctor` | FK to `CustomUser` (the doctor) |
| `clinic` | FK to `Clinic` |
| `start_date` / `end_date` | Inclusive date range |
| `reason` | Optional text (e.g. "إجازة سنوية") |
| `is_active` | `True` = exception is enforced |
| `created_by` | FK to `CustomUser` |

### Enforcement (defense in depth)
Same two-layer check as `ClinicHoliday`:
1. `doctors/services.generate_slots_for_date` — returns `[]` if exception active for this doctor
2. `appointments/services/booking_service.book_appointment` — raises `BookingError(code="doctor_exception")`

---

## 4. Clinic Subscriptions & Plan Limits (`ClinicSubscription`)

**App**: `clinics`
**Model**: `ClinicSubscription`
**Admin**: `clinics/admin.ClinicSubscriptionAdmin`

### What it does
Tracks the subscription plan, expiry, and capacity limits for each clinic.
Enforces maximum doctor and secretary counts. Controls booking access.

### Fields
| Field | Description |
|---|---|
| `clinic` | OneToOne to `Clinic` |
| `plan_type` | `MONTHLY` or `YEARLY` |
| `plan_name` | `SMALL`, `MEDIUM`, or `ENTERPRISE` |
| `expires_at` | Subscription expiry datetime |
| `max_doctors` | Maximum DOCTOR-role staff; `0 = unlimited` (admin opt-in) |
| `max_secretaries` | Maximum SECRETARY-role staff; `0 = unlimited` (admin opt-in) |
| `status` | `ACTIVE`, `EXPIRED`, or `SUSPENDED` |
| `notes` | Internal admin notes |
| `activated_by` | FK to `CustomUser` — last admin who activated/extended |

### Plan Limits
| Plan | `max_doctors` | `max_secretaries` |
|---|---|---|
| SMALL | 2 | 5 |
| MEDIUM | 4 | 5 |
| ENTERPRISE | admin-defined | admin-defined |

ENTERPRISE plans have no defaults in `PLAN_LIMITS`. Admin sets limits explicitly per clinic.
`0 = unlimited` is a deliberate admin opt-in used for ENTERPRISE.

### `is_effectively_active()`
Returns `True` only when `status == "ACTIVE"` AND `expires_at > timezone.now()`.
This is what `book_appointment()` calls — a status=ACTIVE but expired subscription blocks booking.

### Admin Billing Actions
All actions in `ClinicSubscriptionAdmin` stamp `activated_by = request.user`:
- **Activate** — `status = ACTIVE`
- **Suspend** — `status = SUSPENDED`
- **Extend 30 days** — extends `expires_at` by 30 days, sets `status = ACTIVE`
- **Extend 365 days** — extends `expires_at` by 365 days, sets `status = ACTIVE`

---

## 5. Appointment Notification Service

**App**: `appointments`
**Module**: `appointments/services/appointment_notification_service.py`

### What it does
Central service for creating in-app and email notifications for all appointment events.

### Rules
- In-app `AppointmentNotification` is **always** created first.
- Email failures never block in-app notification creation.
- Email sent only when `user.email` is set AND `user.email_verified = True`.
- `notification.sent_via_email = True` when email succeeds.
- All functions safe to call from `transaction.on_commit()`.
- All failures are caught and logged — never re-raised.

### Public functions
| Function | Audience | Channels |
|---|---|---|
| `notify_appointment_booked` | Patient | In-app + Email |
| `notify_appointment_cancelled_by_staff` | Patient | In-app + Email + SMS (if configured) |
| `notify_appointment_rescheduled_by_staff` | Patient | In-app + Email |
| `notify_staff_patient_cancelled` | Doctor + Secretaries | In-app only |
| `notify_staff_patient_edited` | Doctor + Secretaries | In-app only |
| `notify_appointment_reminder` | Patient | In-app + Email |

---

## 6. Appointment Reminders Management Command

**App**: `appointments`
**Command**: `python manage.py send_appointment_reminders`
**File**: `appointments/management/commands/send_appointment_reminders.py`

### What it does
Sends 24-hour advance reminders for upcoming confirmed appointments. Designed to be
run as a scheduled cron job (e.g., hourly or twice daily).

### Behaviour
1. Finds all `CONFIRMED` appointments with `reminder_sent=False` whose datetime
   falls within the next 24 hours.
2. For each, calls `notify_appointment_reminder(appointment)` — creates in-app + email notification.
3. Sets `appointment.reminder_sent = True`.
4. **Idempotent** — running the command multiple times will not send duplicate reminders
   because `reminder_sent=True` prevents re-processing.

### Key constants
- `REMINDER_HOURS_BEFORE = 24`

---

## 7. Advanced Appointment Workflow (Doctor & Secretary)

### Doctor Status Transitions
The `_TRANSITION_MAP` in `doctors/views.appointment_detail`:

```
PENDING      → CONFIRMED, CANCELLED
CONFIRMED    → CHECKED_IN, CANCELLED, NO_SHOW
CHECKED_IN   → IN_PROGRESS
IN_PROGRESS  → COMPLETED
COMPLETED, CANCELLED, NO_SHOW → [terminal]
```

When the doctor transitions to CANCELLED, `notify_appointment_cancelled_by_staff` is fired
via `transaction.on_commit()`.

### Secretary Appointment Management
- **Create** — books for a patient by phone lookup; validates doctor-clinic membership;
  starts in `CONFIRMED` state
- **Edit** — reschedule date/time, change appointment type, update reason;
  fires `notify_appointment_rescheduled_by_staff` if date/time changed;
  blocked for CHECKED_IN, IN_PROGRESS, COMPLETED, CANCELLED, NO_SHOW
- **Cancel** — delegates to `cancel_appointment_by_staff()`; notifies patient

---

## 8. Invitation Audit Log (`InvitationAuditLog`)

**App**: `clinics`
**Model**: `InvitationAuditLog`

### What it does
Records every lifecycle event for every `ClinicInvitation`. Provides a tamper-evident
audit trail of invitation actions.

### Fields
| Field | Description |
|---|---|
| `clinic` | FK to `Clinic` |
| `invitation` | FK to `ClinicInvitation` |
| `action` | One of: `CREATED`, `CANCELLED`, `ACCEPTED`, `REJECTED`, `EXPIRED` |
| `performed_by` | FK to `CustomUser` (null for system actions) |
| `timestamp` | Auto-set on creation |

### When it is written
`clinics/services._log_invitation_action()` is called automatically by:
- `create_invitation()` → action: `CREATED`
- `accept_invitation()` → action: `ACCEPTED`
- `reject_invitation()` → action: `REJECTED`
- `cancel_invitation()` → action: `CANCELLED`
- Expiry handling in `create_invitation()` → action: `EXPIRED`

---

## 9. Clinic Switching (Multi-Clinic Owner)

**App**: `clinics`
**View**: `clinics:switch_clinic`
**URL**: `/clinics/switch/<clinic_id>/`

### What it does
Allows a clinic owner who manages multiple clinics to switch the "active" clinic context.
The selected clinic is stored in `request.session["selected_clinic_id"]`.

---

## 10. Secretary Invitation Flow

**App**: `secretary`
**Routes**: `secretary:secretary_invitations_inbox`, `secretary:accept_invitation`,
`secretary:reject_invitation`, `secretary:guest_accept_invitation`

### What it does
Allows a user with the SECRETARY role to view pending clinic invitations and accept or
reject them. Mirrors the doctor invitation flow but scoped to role=SECRETARY.

---

## 11. Reports Dashboard

**App**: `clinics`
**View**: `clinics:reports`
**URL**: `/clinics/reports/`

Provides the clinic owner with analytics about their clinic.

---

## 12. Multi-Role Users

**App**: `accounts`
**Model field**: `CustomUser.roles` (ArrayField of role strings)

A single user can hold multiple roles simultaneously. When a user accepts an invitation:
- The new role is added to `user.roles`
- `user.role` is promoted if the new role has higher privilege
  (rank order: `PATIENT=0, SECRETARY=1, DOCTOR=2, MAIN_DOCTOR=3`)

---

## 13. PendingDoctorIdentity Lock

**App**: `clinics`
**Model**: `PendingDoctorIdentity`

Prevents race conditions when multiple clinics simultaneously invite the same unregistered
phone number. Creates an atomic lock so only one onboarding flow proceeds at a time.

---

## 14. Clinic Subscription (Extended: was Section 8)

Now documented in full in Section 4 above.

---

## 15. Clinic Verification Wizard

**App**: `clinics`
**Model**: `ClinicVerification`
**Routes**: `clinics:verify_owner_phone`, `clinics:verify_owner_email`,
`clinics:verify_clinic_phone`, `clinics:verify_clinic_email`

After clinic registration, the clinic owner completes a 4-step verification process:
1. Verify owner's phone (OTP)
2. Verify owner's email (OTP)
3. Verify clinic's phone (optional)
4. Verify clinic's email (optional)

Steps 1-2 are required for the clinic to become `ACTIVE`. Steps 3-4 are optional.

---

## 16. Doctor Credential Review (Clinic Owner)

**App**: `clinics`
**Routes**: `clinics:credentials_list`, `clinics:credential_approve`, `clinics:credential_reject`

Allows the clinic owner (MAIN_DOCTOR) to review per-clinic specialty certificates
uploaded by their doctors. Layer B of the dual-layer verification system.

---

## Clinical Note Templates

**App**: `doctors`
**Models**: `ClinicalNoteTemplate`, `ClinicalNoteTemplateElement`, `DoctorClinicalNoteSettings`
**Routes**: `doctors:clinical_note_templates`, `doctors:clinical_note_template_create`,
`doctors:clinical_note_template_edit`, `doctors:clinical_note_template_activate`,
`doctors:clinical_note_template_delete`
**URL prefix**: `/doctors/clinical-note-templates/`
**Sidebar**: Setup → Note Templates

### What it does
Doctors can configure the layout/sections of their Clinical Notes editor.

### Template types
| Type | Description |
|---|---|
| `SYSTEM` + `is_system_default=True` | The platform fallback template used when no override is set |
| `SYSTEM` + `is_system_default=False` | Ready-made specialty templates (Orthopedic, Dental, General) |
| `CUSTOM` | Doctor-created templates, each owned by a single doctor |

### Element types
`SUBJECTIVE`, `OBJECTIVE`, `ASSESSMENT`, `PLAN`, `FREE_TEXT`, `VITALS`, `BODY_DIAGRAM`, `DENTAL`, `CUSTOM`

### Activation rules
- Only one template is active per doctor at a time.
- Activating the system default clears the override (sets `active_template=None`).
- Doctors may only activate system templates or their own custom templates.
- When a doctor deletes their active custom template, the active override is cleared (falls back to system default).

### Backward compatibility
- Existing `ClinicalNote` records are never modified — all stored fields remain intact.
- The note viewer always shows only non-empty SOAP fields (unchanged behavior).
- When `active_note_elements` is `None` or empty the workspace form falls back to rendering all sections.

### Seed data
Migration `0004_seed_system_templates` creates four system templates:
Default, General, Orthopedic (includes Body Diagram), Dental (includes Dental Chart).

### Section reordering UX (2026-04-17)
Sections within the template builder are reordered via **drag-and-drop** (HTML5 native
Drag and Drop API, no external library).

- Each row shows a `fa-grip-vertical` drag handle on the leading edge.
- Dragging a row shows a blue 3 px indicator line at the insertion point.
- On drop, the row is inserted into the exact DOM position the indicator occupied.
- On form submit, Django reads `section_type` / `section_label` via `getlist()` in DOM
  order — so the final visual order is always the saved order. No index mapping needed.
- Newly added rows (via "Add Sections" palette) receive drag handlers immediately via
  `_attachDrag(newRow)` called inside `addStandardRow()` and `addCustomRow()`.
- Works on both Create and Edit pages, for all section types including CUSTOM and
  repeated types with custom labels.
- Up / Down buttons have been removed entirely.

### Note editor integration (bug fix 2026-04-17)
Template sections are rendered in the Clinical Note create/edit form in
**exact template order**, including CUSTOM, VITALS, BODY_DIAGRAM, and DENTAL sections.

#### Storage model
| Section type | How value is saved |
|---|---|
| `SUBJECTIVE`, `OBJECTIVE`, `ASSESSMENT`, `PLAN`, `FREE_TEXT` | Direct `ClinicalNote` model column |
| `VITALS` | `ClinicalNote.extra_sections["vitals"]` |
| `BODY_DIAGRAM` | `ClinicalNote.extra_sections["body_diagram_notes"]` |
| `DENTAL` | `ClinicalNote.extra_sections["dental_notes"]` |
| `CUSTOM` | `ClinicalNote.extra_sections[str(element_id)]` |

#### Key helpers (`doctors/views.py`)
| Helper | Purpose |
|---|---|
| `_get_active_note_sections(doctor, note=None)` | Returns ordered list of section descriptors for the editor form; pre-fills values when `note` is supplied (edit mode) |
| `_collect_extra_sections(post_data)` | Parses POST data for VITALS, BODY_DIAGRAM, DENTAL, and `custom_section_<id>` fields into the `extra_sections` dict |
| `_extract_note_field(note, element_type)` | Returns saved value for a given element type from a `ClinicalNote` instance |
| `_ws_notes_data(...)` | Precomputes `note._labeled_extras` (list of `{label, value}`) for every note in the list, enabling display of all extra_sections in the note body |

#### Root causes fixed
- Custom sections were **silently omitted** from the note form — old architecture used
  three separate context variables and only appended custom sections after all standard
  sections, ignoring their defined position.
- `VITALS`, `BODY_DIAGRAM`, and `DENTAL` POST values were **not collected** before save.
- Saved `extra_sections` content was **never rendered** in the note body display.
- `ws_note_delete` handler was missing `active_note_sections` in its response context,
  causing the add-note form to render empty after a deletion.

### Historical label preservation (bug fix 2026-04-17)

**Problem:** Deleting a CUSTOM section from a template caused all previously saved notes
that used that section to display "Custom Section" instead of the original label, because
labels were resolved dynamically from the live `ClinicalNoteTemplateElement` record.

**Fix:** `ClinicalNote` now carries a `extra_sections_labels` JSON snapshot — written at
every note create/edit — so saved notes are immune to future template changes.

#### New model field
| Field | Type | Purpose |
|---|---|---|
| `extra_sections_labels` | `JSONField(default=dict)` | `{key: label}` snapshot of display labels at note-save time |

Keys mirror `extra_sections` (e.g. `"123"` for CUSTOM elem id 42, `"vitals"` for VITALS).

#### Label resolution order (rendering)
`_annotate_notes_with_labeled_extras()` resolves each key in this priority:
1. **`extra_sections_labels` snapshot** — historically persisted; immune to element deletion
2. **`_EXTRA_SECTION_DISPLAY_LABELS`** — code constants for VITALS / BODY_DIAGRAM / DENTAL
3. **Live `ClinicalNoteTemplateElement` DB lookup** — backward compat for pre-migration notes
4. **"Custom Section"** — last resort when element was deleted before snapshot was recorded

#### New helpers (`doctors/views.py`)
| Helper | Purpose |
|---|---|
| `_collect_extra_sections_labels(active_sections)` | Builds `{key: label}` snapshot from current template sections at save time |
| `_annotate_notes_with_labeled_extras(notes_list)` | Attaches `.labeled_extras` to each note using snapshot-first resolution; replaces the old per-function label loops |

#### Migration
`patients/migrations/0007_clinicalnote_extra_sections_labels.py` — adds the field and
back-fills labels for existing notes from currently-alive elements (best-effort; elements
already deleted before this migration cannot be recovered).

#### Behavior summary
| Note | Template element deleted after save | Displayed label |
|---|---|---|
| New note (post-fix) | Yes | Snapshot label — **correct** |
| Old note (pre-fix, element still alive) | No | Live label via backward-compat lookup |
| Old note (pre-fix, element already deleted) | Yes | "Custom Section" (unrecoverable) |
| Old note (backfilled by migration) | Yes | Backfilled snapshot label — **correct** |
