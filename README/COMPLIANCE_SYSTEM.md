# Patient Compliance System

## Overview
The Patient Compliance System is designed to track patient behavior regarding "No-Show" appointments. It helps clinics identify and manage patients who repeatedly miss appointments without cancellation.

The system is fully tenant-isolated. A patient's score in Clinic A does not affect their score in Clinic B. However, if a patient is blocked in Clinic A, a global warning is visible to other clinics to help them make informed decisions.

## Architecture & Integration

### App Location
- `compliance/`

### Key Models

#### `PatientClinicCompliance`
- **Purpose:** Tracks the current score and status for a specific patient at a specific clinic.
- **Fields:** `clinic`, `patient`, `bad_score`, `status` (OK, WARNED, BLOCKED), `last_violation_at`, `blocked_at`, `last_forgiven_at`.
- **Constraint:** `unique_together` on `(clinic, patient)`.

#### `ComplianceEvent`
- **Purpose:** Audit log of all score changes.
- **Fields:** `clinic`, `patient`, `event_type` (NO_SHOW, MANUAL_WAIVER, AUTO_FORGIVENESS), `score_change`, `appointment`.

#### `ClinicComplianceSettings`
- **Purpose:** Allows each clinic to configure their own strictness levels.
- **Fields:** 
  - `score_increment_per_no_show` (default: 1)
  - `score_threshold_block` (default: 3)
  - `max_score` (default: 5)
  - `auto_forgive_enabled` (default: False)
  - `auto_forgive_after_days` (nullable)

### Service Layer

Core logic is implemented in `compliance/services/compliance_service.py` ensuring transactions and consistency:

- `record_no_show(clinic, patient, appointment)`: Increments score, handles state transitions to WARNED/BLOCKED, logs the event.
- `apply_manual_waiver(clinic, patient)`: Resets score to 0 and clears blocked status.
- `run_auto_forgiveness()`: Background task that lowers scores when `auto_forgive_after_days` is reached without incident.

## Integration Points

### 1. Booking Validation
In `appointments/services/booking_service.py`, before an appointment is booked, `is_patient_blocked` is called. If True, a `BookingError` is raised preventing the booking.

### 2. Auto-Creation
Via Django signals (`compliance/signals.py`), a `ClinicComplianceSettings` record is created automatically whenever a new `Clinic` is created.

## Admin Workflow
- The `compliance` models are registered in the Django admin interface.
- Super Admins or Clinic Admins (via appropriate views in the future) can view `PatientClinicCompliance` and execute "Manual Waivers" to restore a patient's privileges.
