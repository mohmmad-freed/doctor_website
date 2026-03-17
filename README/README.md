# SaaS Multi-Clinic Appointment Management System

> **Last updated**: 2026-03-17
> Reflects the **current implemented system**. Planned/future items are explicitly labelled.

---

## Project Overview

This platform is a **Django multi-tenant SaaS** solution designed to serve multiple medical clinics simultaneously while maintaining strict data isolation and operational independence for each clinic.

The system gives patients a **single global identity** across the ecosystem while maintaining clinic-specific appointment records and compliance scores.

---

## Key Problems Solved

1. **Siloed Patient Data** — patients visiting multiple clinics share one account with clinic-scoped appointment history.
2. **Doctor Overbooking** — doctors working at multiple clinics cannot be double-booked; slot generation checks availability globally across all clinics.
3. **Tenant Leakage** — strict `clinic_id` scoping at every service and view layer prevents cross-tenant data exposure.

---

## High-Level System Description

- **Multi-Tenant Architecture** — each clinic is an isolated tenant identified by a unique `clinic_id`.
- **Global Identity** — patients and doctors have a single `CustomUser` account.
- **Hybrid Frontend** — Django Templates enhanced with **HTMX** for interactive booking wizard, slot loading, and intake forms. No SPA framework.
- **API Layer** — Django REST Framework (DRF) + SimpleJWT for mobile / external integration endpoints.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.x, Django |
| Database | PostgreSQL (required for `ArrayField`) |
| Frontend | Django Templates + HTMX + Bootstrap (Arabic RTL) |
| Web Auth | Session-based (views) |
| API Auth | JWT via SimpleJWT (`/api/`) |
| Email | Brevo (Sendinblue) via `sib_api_v3_sdk` |
| SMS | TweetsMS (configured; deferred for appointment events) |
| Deployment | Render.com (see `DEPLOY_RENDER.md`) |
| Static files | WhiteNoise |

---

## Current Feature Set (Implemented)

### Clinic Management
- Clinic creation via admin-issued **activation codes** (seeded with plan tier, capacity limits, expiry)
- Post-signup 4-step verification wizard (owner phone OTP, owner email OTP, clinic phone, clinic email)
- Multi-clinic switching for owners managing multiple clinics
- Working hours (`ClinicWorkingHours`) — per-clinic recurring schedule
- **Clinic holidays** (`ClinicHoliday`) — blocks all booking on covered dates
- Reports dashboard

### Subscription & Plan Limits
- `ClinicSubscription` — each clinic has a subscription bound at creation
- Three plan tiers: **SMALL** (2 doctors, 5 secretaries), **MEDIUM** (4 doctors, 5 secretaries), **ENTERPRISE** (admin-defined limits; `0 = unlimited`)
- `is_effectively_active()` — both `status=ACTIVE` AND `expires_at > now()` required
- Admin billing actions: Activate, Suspend, Extend 30 days, Extend 365 days (all stamp `activated_by`)
- Subscription checked on every booking attempt

### Doctor Management
- Doctor invitation and onboarding (invitation → accept → `ClinicStaff` created)
- Two-layer verification: platform identity (`DoctorVerification`) + per-clinic credentials (`ClinicDoctorCredential`)
- Doctor availability engine — stateless slot generation from `DoctorAvailability` (weekly schedule)
- **Doctor availability exceptions** (`DoctorAvailabilityException`) — blocks specific doctor's slots on covered dates
- Doctor dashboard, appointment list, appointment detail, patients list
- Intake form builder (`DoctorIntakeFormTemplate`, `DoctorIntakeQuestion`, `DoctorIntakeRule`)

### Secretary Management
- Secretary invitation flow (mirroring doctor flow; role=SECRETARY)
- Secretary dashboard, appointment list, create appointment, edit appointment, cancel appointment
- Secretary-created appointments start as `CONFIRMED`

### Patient Booking
- Self-service HTMX booking wizard: browse clinics → select doctor → appointment type → date/slot → intake form → confirm
- Appointments created in `CONFIRMED` state
- Patient cancel (up to 2 hours before appointment)
- Patient edit (up to `MAX_PATIENT_EDITS = 2` times; PENDING or CONFIRMED only)
- Compliance block check (blocked patients cannot book)
- Holiday and doctor exception checks (defense in depth: both slot generation and booking service)
- Subscription active check using `is_effectively_active()`
- Race condition protection via `select_for_update()`

### Appointment Workflow
- **7 statuses**: `PENDING`, `CONFIRMED`, `CHECKED_IN`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `NO_SHOW`
- Doctor status transitions via `appointment_detail` view (full `_TRANSITION_MAP`)
- Secretary can cancel any non-terminal appointment
- No-show processing management command (`process_no_shows`)

### Notification System
- `AppointmentNotification` model — in-app notifications for all appointment events
- **6 notification types**: `APPOINTMENT_BOOKED`, `APPOINTMENT_CANCELLED`, `APPOINTMENT_EDITED`, `APPOINTMENT_REMINDER`, `APPOINTMENT_RESCHEDULED`, `APPOINTMENT_STATUS_CHANGED`
- Central service: `appointments/services/appointment_notification_service.py`
- In-app notification always created first; email is non-blocking
- Email sent only if `user.email` is present AND `user.email_verified = True`
- `sent_via_email` field on `AppointmentNotification` records whether email succeeded
- Email functions: `send_appointment_booking_email`, `send_appointment_cancellation_email`, `send_appointment_reminder_email`, `send_appointment_rescheduled_email`

### Appointment Reminders
- Management command `send_appointment_reminders` — finds CONFIRMED appointments within next 24 hours with `reminder_sent=False`
- Creates `APPOINTMENT_REMINDER` notification + email
- Sets `appointment.reminder_sent = True` (idempotent — safe to run multiple times)

### Compliance System
- Per-patient per-clinic no-show scores (`PatientClinicCompliance`)
- Auto-blocking at configurable threshold
- Auto-forgiveness management command

### File Uploads
- Intake form file attachments (`AppointmentAttachment`)
- MIME signature validation, size limits — `core/validators/file_validators.py`

---

## Test Coverage

**313 tests passing** as of this writing.

Test structure:
- `appointments/tests/` — package with `test_main.py` + `test_appointment_types.py`
- `clinics/tests/test_plan_limits.py` — plan limits enforcement tests

---

## Documentation Index

| Document | Description |
|---|---|
| `README/ARCHITECTURE.md` | System architecture, app structure, service layer |
| `README/BUSINESS_RULES.md` | All enforced business rules with rule IDs |
| `README/KEY_MODULES.md` | Per-app responsibilities, models, key logic |
| `README/IMPLEMENTED_FEATURES.md` | Detailed feature reference |
| `README/TODO_AND_KNOWN_ISSUES.md` | Deferred items and known issues |
| `README/DOCTOR_STATES.md` | Doctor verification state machine |
| `README/CHANGELOG_IMPLEMENTATION_PROGRESS.md` | Development phase history |
| `README/WORKFLOWS/APPOINTMENT_BOOKING_WORKFLOW.md` | Full booking workflow spec |
| `README/DATA_MODEL/APPOINTMENT_BOOKING_DATA_MODEL.md` | Data model spec |
| `DEPLOY_RENDER.md` | Deployment configuration |

---

## Target Audience

- **Backend Developers** — data models, service layer, business logic
- **Frontend Developers** — Django Templates, HTMX interactions, Arabic RTL UI
- **System Architects** — multi-tenant security, scalability design
