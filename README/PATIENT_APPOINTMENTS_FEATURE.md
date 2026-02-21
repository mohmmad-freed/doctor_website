# Patient Appointments Feature

## Feature Overview

Allows authenticated patients to view their complete appointment history, split into two sections:

- **Upcoming** — appointments with an active status and a future or current date
- **Past** — appointments that have concluded (terminal status or past date)

---

## Endpoint

| Property | Value |
| :--- | :--- |
| **URL** | `GET /patients/appointments/` |
| **Name** | `patients:my_appointments` |
| **View** | `patients.views.my_appointments` |
| **Auth** | `@login_required` + patient role guard |
| **Template** | `patients/templates/patients/my_appointments.html` |

---

## Data Flow

```
Request (patient)
    │
    ▼
patients.views.my_appointments
    │  validates role == "PATIENT"
    │  ensures PatientProfile exists
    │
    ▼
appointments.services.patient_appointments_service.get_patient_appointments(user)
    │  single ORM query with select_related + prefetch_related
    │  splits into upcoming / past
    │
    ▼
Template: patients/my_appointments.html
    │  Section 1 — Upcoming appointments (or empty state)
    │  Section 2 — Past appointments (or empty state)
```

---

## Appointment Classification

| Category | Condition |
| :--- | :--- |
| **Upcoming** | `combined_datetime >= timezone.now()` **AND** `status ∈ {PENDING, CONFIRMED, CHECKED_IN, IN_PROGRESS}` |
| **Past** | `combined_datetime < timezone.now()` **OR** `status ∈ {COMPLETED, CANCELLED, NO_SHOW}` |

> **Note:** `combined_datetime` is computed as `datetime.combine(appointment_date, appointment_time)` compared against `timezone.localtime(timezone.now())`. This correctly handles same-day appointments whose slot time has already passed.

---

## Status Badge Colour Mapping

| Status | UI Colour |
| :--- | :--- |
| `CONFIRMED` | Green |
| `PENDING` | Yellow |
| `CHECKED_IN` / `IN_PROGRESS` | Blue |
| `COMPLETED` | Neutral Gray |
| `CANCELLED` | Red |
| `NO_SHOW` | Orange |

---

## Security Considerations

- `@login_required` — unauthenticated users are redirected to login.
- Patient role check — non-`PATIENT` roles receive `HTTP 403 Forbidden`.
- Data isolation — the ORM query filters strictly by `patient=request.user`, so a patient can never see another patient's appointments.
- No direct ID exposure in the list view (read-only display, no mutations).

---

## Performance Optimisations

The service uses a single database query with:

```python
Appointment.objects.filter(patient=patient_user)
    .select_related(
        "clinic",
        "doctor",
        "doctor__doctor_profile",
        "appointment_type",
    )
    .prefetch_related(
        "doctor__doctor_profile__doctor_specialties__specialty",
    )
```

This fully eliminates N+1 queries for doctor name, specialty, clinic name, and appointment type.

---

## Files

| File | Purpose |
| :--- | :--- |
| `appointments/services/patient_appointments_service.py` | Service function — fetches and categorises appointments |
| `appointments/services/booking_service.py` | Booking service (moved from old flat `services.py`) |
| `appointments/services/__init__.py` | Package init with full backward-compatible re-exports |
| `patients/views.py` | `my_appointments` view (completed from stub) |
| `patients/templates/patients/my_appointments.html` | Page template |
| `patients/urls.py` | Route already existed — no change |

---

## Future Improvements

- Add pagination for patients with many past appointments.
- Add a "Cancel Appointment" action button on upcoming appointments.
- Expose appointment detail page with full intake responses.
- Add filtering/sorting controls (by clinic, status, date range).
- Push real-time status updates via HTMX or WebSocket.

---

## Refinements and Hardening

### timezone.now() Classification Fix

The original implementation compared `appointment_date >= date.today()` (date-only) which caused same-day appointments whose slot time had already passed to be incorrectly classified as upcoming. The hardened version uses:

```python
from django.utils import timezone
from datetime import datetime

local_now = timezone.localtime(timezone.now())
appt_naive_dt = datetime.combine(appointment.appointment_date, appointment.appointment_time)
# appt is past if appt_naive_dt < local_now (tzinfo stripped for naive comparison)
```

This ensures a 09:00 appointment is past by 10:00 on the same day.

### Ordering Strategy

Ordering is applied at the **ORM level**, not in Python:

| Branch | ORM order clause |
| :--- | :--- |
| Upcoming | `.order_by("appointment_date", "appointment_time")` (ascending — soonest first) |
| Past | `.order_by("-appointment_date", "-appointment_time")` (descending — most recent first) |

Two separate querysets (one per branch) allow independent ordering without Python post-sorting.

### Pagination-Ready Architecture

The service signature accepts optional limits with no breaking change:

```python
get_patient_appointments(patient_user, upcoming_limit=None, past_limit=None)
```

The return dict now includes pre-limit counts:

```python
{
    "upcoming": [...],         # respects upcoming_limit
    "past": [...],              # respects past_limit
    "upcoming_count": int,      # total before limit
    "past_count": int,          # total before limit
}
```

The view exposes `upcoming_has_more` and `past_has_more` context booleans. The template renders Load More buttons when these are `True` (no JS required — server-side pagination ready to wire up).

### Backward Compatibility Preservation

The former `appointments/services.py` flat module was refactored into a package. All existing imports continue to work:

```python
# These still work without any change:
from appointments.services import BookingError, book_appointment
from appointments.services import get_patient_appointments
```

This is achieved via explicit named re-exports in `appointments/services/__init__.py`.
