# Appointment Cancellation Notification

When a **ClinicStaff** member cancels a patient's appointment the system notifies the patient across three channels.

---

## Flow

```
cancel_appointment_by_staff(appointment_id, clinic_staff)
    │
    ├── Guard: clinic_staff.clinic == appointment.clinic   (tenant R-01)
    ├── Guard: status NOT in {COMPLETED, CANCELLED, NO_SHOW}
    │
    ▼
appointment.status = CANCELLED  ·  appointment.save()
    │
    └── transaction.on_commit → _notify_patient_cancellation(appointment, clinic_staff)
            │
            ├── [1] In-app  AppointmentNotification.create(...)   ALWAYS (mandatory first)
            ├── [2] Email   send_appointment_cancellation_email() IF verified email
            └── [3] SMS     TweetsMS send_sms()                   IF configured
```

---

## `AppointmentNotification` Model

**Location:** `appointments/models.AppointmentNotification`

| Field | Type | Notes |
|---|---|---|
| `patient` | FK → `AUTH_USER_MODEL` | Mirrors `Appointment.patient` (same FK type) |
| `appointment` | FK → `Appointment` | SET_NULL — records survive appointment deletion |
| `notification_type` | TextChoices | `APPOINTMENT_CANCELLED` (extensible) |
| `title` | CharField | Short Arabic heading |
| `message` | TextField | Full Arabic message |
| `cancelled_by_staff` | FK → `ClinicStaff` (nullable, SET_NULL) | **Audit field** — who cancelled |
| `is_read` | bool | Default False |
| `is_delivered` | bool | Default True (always for in-app) |
| `created_at` | datetime | Auto |

### Why `patient → AUTH_USER_MODEL`?
`Appointment.patient` is a FK to `AUTH_USER_MODEL` (not `PatientProfile`), so `AppointmentNotification.patient` mirrors the same type for consistent relationship traversal.

### Duplicate Prevention (UniqueConstraint)
A DB-level `UniqueConstraint(fields=["appointment", "notification_type"], name="unique_notification_per_appointment_type")` ensures at most one notification per appointment per type. The service also guards terminal-status re-cancellation before the notification fires — two lines of defence.

---

## Notification Message

```
تم إلغاء موعدك مع الدكتور {doctor_name} بتاريخ {date} الساعة {time} في {clinic_name}.
```

---

## Email Rule (STRICT)

Email is sent **only when ALL are true:**

| Condition | Check |
|---|---|
| Email exists | `user.email` is not None / empty |
| Email is verified | `user.email_verified == True` |

`pending_email` is **never** used. Email failure is caught and logged — it **cannot block** in-app notification creation.

**Function:** `accounts/email_utils.send_appointment_cancellation_email(user, appointment)`

---

## SMS Gate (FIX 4)

SMS is only attempted when:

```
settings.SMS_PROVIDER == "TWEETSMS"
AND settings.TWEETSMS_API_KEY is set
AND settings.TWEETSMS_SENDER is set
```

If any condition is false → SMS is silently skipped (logged at INFO level). Network/provider errors are caught and logged at ERROR level — never propagated.

**Gate function:** `_is_sms_configured()` in `patient_appointments_service.py` (mirrors `otp_utils._is_using_tweetsms()` pattern).

---

## Audit: Who Cancelled

The `cancelled_by_staff` FK stores the `ClinicStaff` who performed the cancellation. This field is SET_NULL on delete — notification records survive staff removal.

---

## Tests

**Class:** `StaffCancellationNotificationTests` — `TransactionTestCase` (so `on_commit` fires in tests)

| Test | Fix |
|---|---|
| `test_notification_created_on_cancellation` | Core + FIX 2 (cancelled_by_staff check) |
| `test_pending_appointment_also_notified` | |
| `test_email_sent_to_verified_email` | FIX 5 |
| `test_no_email_when_not_verified` | FIX 5 |
| `test_no_email_when_email_is_none` | FIX 5 |
| `test_duplicate_cancellation_raises_error` | FIX 3 (service guard) |
| `test_unauthorized_staff_cannot_cancel` | |
| `test_cancelled_by_staff_is_stored` | FIX 2 |
| `test_unique_constraint_prevents_db_duplicate` | FIX 3 (DB constraint) |
| `test_sms_skipped_when_not_configured` | FIX 4 |
| `test_email_failure_does_not_block_in_app_notification` | FIX 5 |

```bash
python manage.py test appointments --verbosity=2
```

---

## Extending for Other Notification Types

1. Add choice to `AppointmentNotification.Type`
2. Create `_notify_patient_<event>(appointment, actor)` in `patient_appointments_service.py`
3. Call via `transaction.on_commit()`
4. Add `send_appointment_<event>_email()` in `accounts/email_utils.py`
5. Write tests in `appointments/tests.py`
