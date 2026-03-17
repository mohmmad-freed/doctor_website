# Appointment Booking System — Data Model Specification

> **Document Type:** Architecture Specification — Data Model & Database Design
> **Version:** 1.1
> **Status:** Updated — reflects current implemented models. Planned items are labelled **[PLANNED]**.
> **Last Updated:** 2026-03-17

---

## Table of Contents

1. [Overview](#1-overview)
2. [Existing Models Role Explanation](#2-existing-models-role-explanation)
3. [Proposed New Models](#3-proposed-new-models)
4. [Appointment Model Required Fields](#4-appointment-model-required-fields)
5. [Database Constraints](#5-database-constraints)
6. [Indexing Strategy](#6-indexing-strategy)
7. [Slot Calculation Data Strategy](#7-slot-calculation-data-strategy)
8. [Data Integrity Rules](#8-data-integrity-rules)
9. [Cleanup Strategy](#9-cleanup-strategy)
10. [SMS Notification Data Strategy](#10-sms-notification-data-strategy)
11. [Migration Strategy](#11-migration-strategy)

---

## 1. Overview

This document defines the **complete data model** required to implement the Appointment Booking System. It covers:

- The role of each existing model and how it participates in the booking workflow.
- Six new models required for intake forms, patient answers, file attachments, and notification logging.
- The enhanced `Appointment` model with all fields needed for the HOLD/PENDING/PROPOSED lifecycle.
- Database constraints that prevent double bookings at the PostgreSQL level.
- Indexing strategy for query performance.
- Migration strategy for zero-downtime deployment.

### Entity Relationship Overview

```
┌──────────┐     ┌─────────────┐     ┌────────────────┐
│  Clinic  │────►│ ClinicStaff │     │   Specialty    │
└────┬─────┘     └─────────────┘     └───────┬────────┘
     │                                       │
     │           ┌───────────────┐    ┌──────┴─────────┐
     │           │ DoctorProfile │◄───│DoctorSpecialty  │
     │           └───────┬───────┘    └────────────────┘
     │                   │
     │     ┌─────────────┴──────────────┐
     │     │                            │
     │     ▼                            ▼
     │  ┌──────────────────┐   ┌───────────────────────────┐
     │  │DoctorAvailability│   │ DoctorIntakeFormTemplate   │
     │  └──────────────────┘   └────────────┬──────────────┘
     │                                      │
     │                         ┌────────────┴──────────────┐
     │                         │                           │
     │                         ▼                           ▼
     │                ┌──────────────────┐       ┌─────────────────┐
     │                │DoctorIntakeQuestion│      │ DoctorIntakeRule │
     │                └──────────────────┘       └─────────────────┘
     │
     ▼
┌─────────────────┐     ┌────────────────┐
│ AppointmentType │     │ PatientProfile │
└────────┬────────┘     └───────┬────────┘
         │                      │
         ▼                      ▼
    ┌────────────────────────────────┐
    │          Appointment           │
    └───────┬──────────┬─────────────┘
            │          │
            ▼          ▼
  ┌──────────────┐  ┌────────────────────────┐
  │AppointAnswer │  │ AppointmentAttachment  │
  └──────────────┘  └────────────────────────┘
            │
            ▼
    ┌────────────────┐
    │NotificationLog │
    └────────────────┘
```

---

## 2. Existing Models Role Explanation

### 2.1 Clinic

**App:** `clinics`  
**Purpose:** Represents a physical medical clinic. A clinic is the top-level organizational unit.

| Field            | Role in Booking System                                          |
|------------------|-----------------------------------------------------------------|
| `id`             | Used as `clinic_id` throughout the booking flow                 |
| `name`           | Displayed in clinic browsing and appointment confirmations      |
| `main_doctor`    | FK to the owner doctor; controls clinic settings                |
| `city`           | Used for patient filtering in `clinics_list` and `browse_doctors` |
| `is_active`      | Only active clinics appear in patient-facing views              |

**Booking Role:** Every appointment belongs to exactly one clinic. Clinic determines which doctors and appointment types are available.

---

### 2.2 ClinicStaff

**App:** `clinics`  
**Purpose:** Maps users to clinics with a role (DOCTOR or SECRETARY).

| Field    | Role in Booking System                                           |
|----------|------------------------------------------------------------------|
| `clinic` | FK to `Clinic` — determines which clinic the staff can manage    |
| `user`   | FK to `CustomUser` — the staff member's auth identity            |
| `role`   | `"SECRETARY"` members handle the approval workflow               |
| `is_active` | Only active staff members can access clinic management views  |

**Booking Role:** Staff with `role = "SECRETARY"` form the approval layer. They accept, reject, or propose alternative times for `PENDING_APPROVAL` appointments. The `unique_together = ["clinic", "user"]` constraint ensures a user cannot be duplicated within the same clinic.

---

### 2.3 DoctorProfile

**App:** `doctors`  
**Purpose:** Extended profile data for doctor users. One-to-one relationship with `CustomUser`.

| Field                | Role in Booking System                                    |
|----------------------|-----------------------------------------------------------|
| `user`               | OneToOne to `CustomUser` — provides `doctor_id`           |
| `bio`                | Displayed on doctor browsing pages                        |
| `years_of_experience`| Displayed on doctor browsing pages                        |
| `specialties`        | M2M through `DoctorSpecialty` — used for search filtering |

**Booking Role:** Provides the doctor's identity for the booking flow. Specialties enable patients to find the right doctor. The `user.id` serves as the `doctor_id` in appointments.

---

### 2.4 DoctorAvailability

**App:** `doctors`  
**Purpose:** Defines the doctor's recurring weekly schedule at a specific clinic.

| Field          | Role in Booking System                                            |
|----------------|-------------------------------------------------------------------|
| `doctor`       | FK to `CustomUser` — the doctor whose schedule this defines       |
| `clinic`       | FK to `Clinic` — schedule is clinic-specific                      |
| `day_of_week`  | 0–6 (Mon–Sun) — determines which days slots are generated        |
| `start_time`   | Start of the availability window                                  |
| `end_time`     | End of the availability window                                    |
| `is_active`    | Inactive windows are excluded from slot generation                |

**Booking Role:** This is the **primary input** for the slot calculation algorithm. The engine iterates through active availability windows, generates time slots of `duration_minutes` length, and filters out booked/held slots.

**Existing Constraints:**
- `unique_doctor_clinic_day_start` — prevents duplicate entries.
- Cross-clinic overlap validation in `clean()` — prevents a doctor from being double-booked across clinics.

---

### 2.5 AppointmentType

**App:** `appointments`
**Purpose:** Defines the types of visits offered at a specific clinic.

> **Correction**: `AppointmentType` is scoped to **`Clinic` only** — there is **no `doctor` FK**.
> All doctors at a clinic share the same appointment type catalogue.
> Adding a `doctor` FK for per-doctor-per-clinic scoping is **[PLANNED]**.

| Field              | Role in Booking System                                       |
|--------------------|--------------------------------------------------------------|
| `clinic`           | FK to `Clinic` — scoped to a specific clinic                 |
| `name` / `name_ar` | Displayed to patients in the booking UI                      |
| `duration_minutes` | **Critical** — determines slot size in the calculation engine |
| `price`            | Displayed to patients; may be used for billing               |
| `is_active`        | Inactive types hidden from patients                          |

**Booking Role:** Determines the **duration** of each appointment slot. Different types produce
different slot grids for the same doctor on the same day. The `unique_appointment_type_per_clinic`
constraint prevents naming collisions within a clinic.

---

### 2.6 Appointment

**App:** `appointments`
**Purpose:** The core transactional record representing a booking between a patient and a doctor at a clinic.

**Current implemented fields (as of 2026-03-17):**

| Field               | Type            | Description                                           |
|---------------------|-----------------|-------------------------------------------------------|
| `id`                | AutoField       | Primary key                                           |
| `clinic`            | FK → `Clinic`   | Clinic where the appointment takes place              |
| `doctor`            | FK → `CustomUser` | The treating doctor (SET_NULL, nullable)            |
| `patient`           | FK → `CustomUser` | The patient (CASCADE)                              |
| `appointment_type`  | FK → `AppointmentType` | Type determining slot duration (SET_NULL, nullable) |
| `appointment_date`  | DateField       | Date of the appointment                               |
| `appointment_time`  | TimeField       | Start time of the appointment                         |
| `reason`            | TextField       | Patient-supplied reason for visit                     |
| `notes`             | TextField       | Doctor's notes after the appointment                  |
| `status`            | CharField       | See status enum below                                 |
| `intake_responses`  | JSONField       | Legacy intake answers (replaced by `AppointmentAnswer`) |
| `patient_edit_count`| PositiveIntegerField | Tracks how many times the patient has edited; max = `MAX_PATIENT_EDITS = 2` |
| `reminder_sent`     | BooleanField    | `True` after `send_appointment_reminders` command processes this appointment |
| `created_by`        | FK → `CustomUser` | Who created the appointment (patient or secretary; SET_NULL, nullable) |
| `created_at`        | DateTimeField   | Auto-set on creation                                  |
| `updated_at`        | DateTimeField   | Auto-set on update                                    |

**Current Status Enum (7 values):**
`PENDING`, `CONFIRMED`, `CHECKED_IN`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `NO_SHOW`

New bookings are created with `status = CONFIRMED` directly.
Patient self-booking and secretary booking both result in `CONFIRMED`.
`PENDING` exists in the model and is used as a source state in the doctor's `_TRANSITION_MAP`.

**[PLANNED] Fields not yet added:**
- `hold_expires_at` — required for HOLD state
- `pending_expires_at` — required for PENDING_APPROVAL state
- `proposed_start_at` / `proposed_end_at` — required for PROPOSED_TIME state
- Migration from `appointment_date` + `appointment_time` → `start_at` DateTimeField

**[PLANNED] Status values not yet added:**
`HOLD`, `PENDING_APPROVAL`, `PROPOSED_TIME`, `EXPIRED`, `REJECTED`

**Booking Role:** Central record tracking the lifecycle from `CONFIRMED` through completion or cancellation.

---

### 2.7 PatientProfile

**App:** `patients`  
**Purpose:** Extended profile for patient users.

| Field                   | Role in Booking System                               |
|-------------------------|------------------------------------------------------|
| `user`                  | OneToOne to `CustomUser` — provides `patient_id`     |
| `date_of_birth`         | May be required by certain intake forms              |
| `gender`                | May be required by certain intake forms              |
| `medical_history`       | Reference for doctors reviewing appointment details  |
| `allergies`             | Reference for doctors reviewing appointment details  |
| `emergency_contact_*`   | Displayed in appointment detail for safety           |

**Booking Role:** The `user.id` serves as the `patient_id`. Profile data enhances the doctor's view in `appointment_detail`.

---

### 2.8 DoctorSpecialty

**App:** `doctors`  
**Purpose:** Through table for the `DoctorProfile ↔ Specialty` many-to-many relationship.

| Field            | Role in Booking System                                   |
|------------------|----------------------------------------------------------|
| `doctor_profile` | FK to `DoctorProfile`                                    |
| `specialty`      | FK to `Specialty`                                        |
| `is_primary`     | Highlighted in doctor cards in `browse_doctors`          |

**Booking Role:** Enables specialty-based filtering in `browse_doctors`. The `unique_primary_specialty_per_doctor` constraint ensures data integrity.

---

### 2.9 Specialty

**App:** `doctors`  
**Purpose:** Master list of medical specialties.

| Field      | Role in Booking System                         |
|------------|------------------------------------------------|
| `name`     | English name for internal use                  |
| `name_ar`  | Arabic name displayed in patient-facing views  |

**Booking Role:** Used as a filter in `browse_doctors` — patients can narrow their search by specialty.

---

## 3. Intake & Answer Models (Implemented)

> These models were previously described as "proposed". They are now **fully implemented**
> in the `doctors` and `appointments` apps.

### 3.1 DoctorIntakeFormTemplate

**App:** `doctors` (or new app `intake`)  
**Purpose:** Defines a reusable intake form template that a doctor can attach to their appointment types.

#### Fields

| Field Name            | Type                  | Description                                                     |
|-----------------------|-----------------------|-----------------------------------------------------------------|
| `id`                  | AutoField (PK)        | Primary key                                                     |
| `doctor`              | FK → `CustomUser`     | The doctor who owns this template                               |
| `appointment_type`    | FK → `AppointmentType`| (Nullable) Specific type; NULL = applies to all types           |
| `title`               | CharField(200)        | Template name, e.g. "New Patient Intake"                        |
| `title_ar`            | CharField(200)        | Arabic title                                                    |
| `description`         | TextField (blank)     | Optional instructions displayed before the form                 |
| `is_active`           | BooleanField          | Only active templates are rendered during booking               |
| `created_at`          | DateTimeField         | Auto-set on creation                                            |
| `updated_at`          | DateTimeField         | Auto-set on update                                              |

#### Relationships

- **doctor** → `CustomUser` (FK, CASCADE) — one doctor may have many templates.
- **appointment_type** → `AppointmentType` (FK, SET_NULL, nullable) — if set, this form only appears for this specific appointment type. If NULL, appears for all of the doctor's appointment types.
- Has many `DoctorIntakeQuestion` records (reverse FK).

#### Constraints

- `UniqueConstraint(fields=["doctor", "appointment_type"], condition=Q(is_active=True))` — at most one active template per doctor per appointment type.

#### Example Records

| id | doctor_id | appointment_type_id | title                  | title_ar            | is_active |
|----|-----------|---------------------|------------------------|---------------------|-----------|
| 1  | 5         | NULL                | General Intake Form    | نموذج استقبال عام   | True      |
| 2  | 5         | 12                  | Cardio Pre-Assessment  | تقييم قلب أولي      | True      |
| 3  | 8         | NULL                | Pediatric History Form | نموذج تاريخ أطفال   | True      |

---

### 3.2 DoctorIntakeQuestion

**App:** `doctors` (or `intake`)  
**Purpose:** Individual question within an intake form template.

#### Fields

| Field Name        | Type                       | Description                                                    |
|-------------------|----------------------------|----------------------------------------------------------------|
| `id`              | AutoField (PK)             | Primary key                                                    |
| `template`        | FK → `DoctorIntakeFormTemplate` | Parent template                                           |
| `question_text`   | CharField(500)             | English question text                                          |
| `question_text_ar`| CharField(500)             | Arabic question text                                           |
| `field_type`      | CharField(20, choices)     | One of: `TEXT`, `TEXTAREA`, `SELECT`, `MULTISELECT`, `CHECKBOX`, `DATE`, `FILE`, `DATED_FILES` |
| `choices`         | JSONField (blank, default=list) | Array of choice strings for SELECT/MULTISELECT types       |
| `is_required`     | BooleanField               | Whether the patient must answer                                |
| `order`           | PositiveIntegerField       | Display order (ascending)                                      |
| `placeholder`     | CharField(200, blank)      | Placeholder text for text inputs                               |
| `help_text`       | TextField (blank)          | Additional guidance displayed below the question               |
| `max_file_size_mb`| PositiveIntegerField (null)| Max upload size for FILE type questions                        |
| `allowed_extensions`| JSONField (blank, default=list) | e.g. `["pdf", "jpg", "png"]`                            |

#### Relationships

- **template** → `DoctorIntakeFormTemplate` (FK, CASCADE) — deleting a template deletes all its questions.
- Has many `DoctorIntakeRule` records (as `source_question` or `target_question`).

#### Constraints

- `UniqueConstraint(fields=["template", "order"])` — no duplicate ordering within a template.

#### Example Records

| id | template_id | question_text         | question_text_ar     | field_type | choices                          | is_required | order |
|----|-------------|-----------------------|----------------------|------------|----------------------------------|-------------|-------|
| 1  | 1           | Current medications?  | الأدوية الحالية؟     | TEXTAREA   | []                               | True        | 1     |
| 2  | 1           | Do you smoke?         | هل تدخن؟             | SELECT     | ["Yes", "No", "Former smoker"]   | True        | 2     |
| 3  | 1           | Upload recent lab results | ارفع نتائج تحاليل حديثة | FILE   | []                               | False       | 3     |
| 4  | 2           | Chest pain frequency  | تكرار ألم الصدر      | SELECT     | ["Daily", "Weekly", "Monthly", "Rarely"] | True | 1     |

---

### 3.3 DoctorIntakeRule

**App:** `doctors` (or `intake`)  
**Purpose:** Defines conditional display logic — "Show question X only if question Y has a specific answer."

#### Fields

| Field Name        | Type                          | Description                                                |
|-------------------|-------------------------------|------------------------------------------------------------|
| `id`              | AutoField (PK)                | Primary key                                                |
| `source_question` | FK → `DoctorIntakeQuestion`   | The question whose answer triggers the rule                |
| `expected_value`  | CharField(500)                | The answer value that activates this rule                  |
| `operator`        | CharField(20, choices)        | One of: `EQUALS`, `NOT_EQUALS`, `CONTAINS`, `IN`          |
| `target_question` | FK → `DoctorIntakeQuestion`   | The question to show/hide based on the rule                |
| `action`          | CharField(10, choices)        | One of: `SHOW`, `HIDE`                                    |

#### Relationships

- **source_question** → `DoctorIntakeQuestion` (FK, CASCADE, related_name=`rules_as_source`)
- **target_question** → `DoctorIntakeQuestion` (FK, CASCADE, related_name=`rules_as_target`)
- Both questions must belong to the **same template** (validated in `clean()`).

#### Constraints

- `UniqueConstraint(fields=["source_question", "target_question", "expected_value"])` — no duplicate rules.
- `CheckConstraint` ensuring `source_question != target_question`.

#### Example Records

| id | source_question_id | expected_value | operator | target_question_id | action |
|----|--------------------|----------------|----------|---------------------|--------|
| 1  | 2                  | Yes            | EQUALS   | 5                   | SHOW   |
| 2  | 4                  | Daily          | EQUALS   | 6                   | SHOW   |

*Interpretation: If "Do you smoke?" = "Yes", show question 5 (e.g., "How many cigarettes per day?").*

---

### 3.4 AppointmentAnswer

**App:** `appointments`  
**Purpose:** Stores a patient's answer to a single intake question for a specific appointment.

#### Fields

| Field Name   | Type                         | Description                                          |
|--------------|------------------------------|------------------------------------------------------|
| `id`         | AutoField (PK)               | Primary key                                          |
| `appointment`| FK → `Appointment`           | The appointment this answer belongs to               |
| `question`   | FK → `DoctorIntakeQuestion`  | The question being answered                          |
| `answer_text`| TextField (blank)            | The patient's text/choice answer                     |
| `created_at` | DateTimeField                | Auto-set on creation                                 |

#### Relationships

- **appointment** → `Appointment` (FK, CASCADE) — deleting an appointment deletes all answers.
- **question** → `DoctorIntakeQuestion` (FK, PROTECT) — prevents deleting a question that has answers.

#### Constraints

- `UniqueConstraint(fields=["appointment", "question"])` — one answer per question per appointment.

#### Example Records

| id | appointment_id | question_id | answer_text                        |
|----|----------------|-------------|------------------------------------|
| 1  | 42             | 1           | Aspirin 100mg daily, Metformin     |
| 2  | 42             | 2           | Former smoker                      |
| 3  | 42             | 4           | Weekly                             |

---

### 3.5 AppointmentAttachment

**App:** `appointments`  
**Purpose:** Stores file uploads associated with an appointment (from intake form FILE questions).

#### Fields

| Field Name    | Type                         | Description                                           |
|---------------|------------------------------|-------------------------------------------------------|
| `id`          | AutoField (PK)               | Primary key                                           |
| `appointment` | FK → `Appointment`           | The appointment this file belongs to                  |
| `question`    | FK → `DoctorIntakeQuestion`  | (Nullable) The FILE question this responds to         |
| `file`        | FileField                    | The uploaded file (stored in media/appointments/)     |
| `original_name`| CharField(255)              | Original filename for display                         |
| `file_size`   | PositiveIntegerField         | Size in bytes                                         |
| `mime_type`   | CharField(100)               | MIME type for download headers                        |
| `uploaded_at` | DateTimeField                | Auto-set on creation                                  |
| `uploaded_by` | FK → `CustomUser`            | The user who uploaded (patient or secretary)           |

#### Relationships

- **appointment** → `Appointment` (FK, CASCADE)
- **question** → `DoctorIntakeQuestion` (FK, SET_NULL, nullable) — attachment may exist without a specific question.
- **uploaded_by** → `CustomUser` (FK, SET_NULL, nullable)

#### File Storage Path

```
media/appointments/{appointment_id}/{uuid}_{original_name}
```

UUID prefix prevents filename collisions.

#### Example Records

| id | appointment_id | question_id | original_name      | file_size | mime_type       |
|----|----------------|-------------|---------------------|-----------|-----------------|
| 1  | 42             | 3           | blood_test_jan.pdf  | 245760    | application/pdf |
| 2  | 42             | 3           | xray_chest.jpg      | 1048576   | image/jpeg      |

---

### 3.6 AppointmentNotification

> **IMPLEMENTATION NOTE**: The earlier version of this section described a planned
> `NotificationLog` model for SMS audit trails. That model was **never implemented**.
> The actual implemented model is `AppointmentNotification` in `appointments/models.py`,
> described here. It handles in-app notifications with optional email tracking.
> A pure SMS audit log (`NotificationLog`) remains **[PLANNED / DEFERRED]**.

**App:** `appointments`
**Model:** `AppointmentNotification`
**Purpose:** Stores in-app notifications for patients (and staff) about appointment events.
Tracks whether an email was also sent for each notification.

#### Fields

| Field Name           | Type                      | Description                                                 |
|----------------------|---------------------------|-------------------------------------------------------------|
| `id`                 | AutoField (PK)            | Primary key                                                 |
| `patient`            | FK → `CustomUser`         | The notification recipient (CASCADE)                        |
| `appointment`        | FK → `Appointment`        | Related appointment (SET_NULL, nullable — survives deletion) |
| `notification_type`  | CharField(60, choices)    | Event type — see enum below                                 |
| `title`              | CharField(255)            | Short Arabic notification title                             |
| `message`            | TextField                 | Full Arabic notification message body                       |
| `cancelled_by_staff` | FK → `ClinicStaff`        | Who cancelled (SET_NULL; populated only on CANCELLED events)|
| `is_read`            | BooleanField              | `True` when patient has read this notification (default `False`) |
| `is_delivered`       | BooleanField              | Always `True` for in-app notifications (default `True`)     |
| `sent_via_email`     | BooleanField              | `True` if an email was successfully sent for this notification (default `False`) |
| `created_at`         | DateTimeField             | Auto-set on creation                                        |

#### Notification Type Enum (`AppointmentNotification.Type`)

| Value                      | Trigger / Description                                        |
|----------------------------|--------------------------------------------------------------|
| `APPOINTMENT_BOOKED`       | Patient books (self-service or via secretary) — sent to patient |
| `APPOINTMENT_CANCELLED`    | Doctor/secretary cancels (to patient) OR patient cancels (to staff) |
| `APPOINTMENT_EDITED`       | Patient edits appointment — sent to doctor and secretaries   |
| `APPOINTMENT_REMINDER`     | 24h advance reminder — sent to patient by management command |
| `APPOINTMENT_RESCHEDULED`  | Secretary reschedules appointment — sent to patient          |
| `APPOINTMENT_STATUS_CHANGED` | Reserved for future use                                   |

#### Relationships

- **patient** → `CustomUser` (FK, CASCADE) — notification recipient
- **appointment** → `Appointment` (FK, SET_NULL, nullable) — survives appointment deletion
- **cancelled_by_staff** → `ClinicStaff` (FK, SET_NULL, nullable) — audit field for cancellations

#### Constraints

> **REMOVED**: An earlier design had `UniqueConstraint(fields=["appointment", "notification_type"])`.
> This constraint has been **removed** from the current implementation. Multiple notifications
> of the same type can be created for a single appointment. Duplicate reminder prevention
> is handled via `Appointment.reminder_sent = True` instead.

No `UniqueConstraint` on `(appointment, notification_type)`.

#### Channel Rules
- **In-app**: always created with `is_delivered=True`. Never blocked.
- **Email**: sent after in-app creation only if `user.email` is set AND `user.email_verified=True`.
  `sent_via_email=True` is set on the notification record if email succeeds.
- **SMS**: only for cancellations, only if `SMS_PROVIDER=TWEETSMS` is configured. Not tracked here.

#### Example Records

| id | appointment_id | patient_id | notification_type        | is_read | sent_via_email |
|----|----------------|------------|--------------------------|---------|----------------|
| 1  | 42             | 15         | APPOINTMENT_BOOKED       | False   | True           |
| 2  | 42             | 15         | APPOINTMENT_REMINDER     | True    | True           |
| 3  | 43             | 18         | APPOINTMENT_CANCELLED    | False   | False          |
| 4  | 44             | 20         | APPOINTMENT_RESCHEDULED  | False   | True           |

---

## 4. Appointment Model Required Fields

The existing `Appointment` model must be enhanced with the following fields. This section provides the **complete** proposed field list.

### 4.1 Complete Field List

| Field Name            | Type                      | Default / Notes                         | Purpose                                                        |
|-----------------------|---------------------------|-----------------------------------------|----------------------------------------------------------------|
| `id`                  | AutoField (PK)            | Auto                                    | Primary key, used as `appointment_id`                          |
| `patient`             | FK → `CustomUser`         | CASCADE                                 | The patient who booked                                         |
| `doctor`              | FK → `CustomUser`         | SET_NULL, nullable                      | The assigned doctor                                            |
| `clinic`              | FK → `Clinic`             | CASCADE                                 | The clinic where the appointment takes place                   |
| `appointment_type`    | FK → `AppointmentType`    | SET_NULL, nullable                      | Type of visit (determines duration and price)                  |
| `visit_kind`          | CharField(30, choices)    | `"IN_PERSON"`                           | `IN_PERSON`, `TELECONSULT` — extensible for future telemedicine |
| `status`              | CharField(20, choices)    | `"HOLD"`                                | Current lifecycle state (9-value enum, see Section 16 of Workflow doc) |
| `start_at`            | DateTimeField             | Required                                | Appointment start time (UTC)                                   |
| `end_at`              | DateTimeField             | Required                                | Appointment end time (UTC), computed as `start_at + duration_minutes` |
| `hold_expires_at`     | DateTimeField (nullable)  | Set on HOLD creation                    | When the HOLD expires (now + 10 min). NULL when not in HOLD    |
| `pending_expires_at`  | DateTimeField (nullable)  | Set on PENDING_APPROVAL                 | When the pending/proposed status expires (now + 2h). NULL when not pending |
| `proposed_start_at`   | DateTimeField (nullable)  | Set by secretary                        | Start of the alternative time proposed by the secretary        |
| `proposed_end_at`     | DateTimeField (nullable)  | Set by secretary                        | End of the alternative time proposed by the secretary          |
| `reason`              | TextField (blank)         | —                                       | Patient's reason for visit (free text)                         |
| `notes`               | TextField (blank)         | —                                       | Doctor's clinical notes after the appointment                  |
| `rejection_reason`    | TextField (blank)         | —                                       | Reason provided by secretary when rejecting                    |
| `cancellation_reason` | TextField (blank)         | —                                       | Reason provided when cancelling                                |
| `created_by`          | FK → `CustomUser`         | SET_NULL, nullable                      | Who created the record (patient or secretary)                  |
| `created_at`          | DateTimeField             | auto_now_add                            | Record creation timestamp                                      |
| `updated_at`          | DateTimeField             | auto_now                                | Last modification timestamp                                    |

### 4.2 Field-by-Field Explanation

#### `status`

The appointment's current lifecycle state. One of nine values:

```
HOLD → PENDING_APPROVAL → CONFIRMED → COMPLETED
                        → REJECTED
                        → PROPOSED_TIME → CONFIRMED
                                        → CANCELLED
                                        → EXPIRED
         → EXPIRED
HOLD → EXPIRED
CONFIRMED → NO_SHOW
CONFIRMED → CANCELLED
```

The default is `HOLD` because every appointment begins life as a temporary reservation.

#### `start_at` / `end_at`

**Replaces:** `appointment_date` + `appointment_time` (which lack an end time concept).

- Stored as `DateTimeField` in UTC.
- `end_at = start_at + timedelta(minutes=appointment_type.duration_minutes)`.
- Used for overlap detection in double-booking prevention constraints.
- Using a contiguous range enables PostgreSQL range operators and exclusion constraints.

#### `hold_expires_at`

- Set to `now() + timedelta(minutes=10)` when the appointment enters `HOLD`.
- Cleared (set to `NULL`) when transitioning out of `HOLD`.
- The auto-expire task queries: `WHERE status = 'HOLD' AND hold_expires_at <= now()`.

#### `pending_expires_at`

- Set to `now() + timedelta(hours=2)` when entering `PENDING_APPROVAL` or `PROPOSED_TIME`.
- Cleared when the secretary acts (accept/reject) or when the patient responds to a proposal.
- Reset to `now() + 2h` when a new alternative time is proposed.

#### `proposed_start_at` / `proposed_end_at`

- Populated only when `status = PROPOSED_TIME`.
- Represent the secretary's alternative time offer.
- The **original** `(start_at, end_at)` is preserved so the patient can see what they originally requested.
- When the patient accepts:
  - `start_at = proposed_start_at`
  - `end_at = proposed_end_at`
  - `proposed_start_at = NULL`
  - `proposed_end_at = NULL`
  - `status = CONFIRMED`

#### `visit_kind`

Extensible field for future telemedicine support:

| Value          | Description              |
|----------------|--------------------------|
| `IN_PERSON`    | Physical clinic visit    |
| `TELECONSULT`  | Video/phone consultation |

Default: `IN_PERSON`.

---

## 5. Database Constraints

### 5.1 Double Booking Prevention

**Goal:** It must be **impossible** for two active appointments to occupy overlapping time ranges for the same doctor.

#### Strategy: PostgreSQL Exclusion Constraint

The most robust solution uses PostgreSQL's range types and the `btree_gist` extension:

```
-- Step 1: Enable the extension
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Step 2: Add exclusion constraint
ALTER TABLE appointments_appointment
ADD CONSTRAINT prevent_doctor_double_booking
EXCLUDE USING GIST (
    doctor_id WITH =,
    tstzrange(start_at, end_at) WITH &&
)
WHERE (status IN ('HOLD', 'PENDING_APPROVAL', 'PROPOSED_TIME', 'CONFIRMED'));
```

**Explanation:**
- `doctor_id WITH =` — applies only to the same doctor.
- `tstzrange(start_at, end_at) WITH &&` — the `&&` operator checks for range overlap.
- `WHERE` clause — only active statuses participate. `EXPIRED`, `CANCELLED`, `REJECTED`, `COMPLETED`, `NO_SHOW` records are ignored.

#### Django Implementation

Since Django does not natively support exclusion constraints, this should be added via a migration:

```
class Migration(migrations.Migration):
    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS btree_gist;",
            reverse_sql="DROP EXTENSION IF EXISTS btree_gist;"
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE appointments_appointment
                ADD CONSTRAINT prevent_doctor_double_booking
                EXCLUDE USING GIST (
                    doctor_id WITH =,
                    tstzrange(start_at, end_at) WITH &&
                )
                WHERE (status IN ('HOLD', 'PENDING_APPROVAL', 'PROPOSED_TIME', 'CONFIRMED'));
            """,
            reverse_sql="ALTER TABLE appointments_appointment DROP CONSTRAINT prevent_doctor_double_booking;"
        ),
    ]
```

### 5.2 Overlap Prevention for Proposed Times

When an appointment is in `PROPOSED_TIME` status, the **proposed time range** `(proposed_start_at, proposed_end_at)` must also be protected:

- Application-level check: Before creating a HOLD or confirming an appointment, query for `PROPOSED_TIME` appointments and check overlap against both the original and proposed ranges.
- This cannot easily be expressed as a single DB constraint since the overlap check depends on two different column pairs based on status. It is enforced at the application layer within the `select_for_update()` transaction.

### 5.3 Additional Constraints

| Constraint                                              | Type                | Purpose                                          |
|---------------------------------------------------------|---------------------|--------------------------------------------------|
| `start_at < end_at`                                     | CheckConstraint     | Ensures valid time range                         |
| `proposed_start_at < proposed_end_at` (when not NULL)   | CheckConstraint     | Ensures valid proposed range                     |
| `hold_expires_at IS NOT NULL when status = 'HOLD'`      | CheckConstraint     | Data integrity for HOLD state                    |
| `pending_expires_at IS NOT NULL when status IN ('PENDING_APPROVAL', 'PROPOSED_TIME')` | CheckConstraint | Data integrity for pending states |
| `patient != doctor`                                     | CheckConstraint     | Doctor cannot book themselves                    |
| `unique_together(patient, doctor, start_at)` for active statuses | UniqueConstraint | Prevent patient from double-booking same slot |

### 5.4 Transaction Safety

All booking operations that create or modify appointment status **must** execute within `transaction.atomic()`:

1. **HOLD creation**: `select_for_update()` on overlapping doctor appointments → check → insert.
2. **Status transitions**: `select_for_update()` on the specific appointment → verify current status → update.
3. **Secretary actions**: All accept/reject/propose operations are wrapped in atomic transactions.

This ensures that concurrent requests are serialized at the database level and no race conditions can produce invalid states.

---

## 6. Indexing Strategy

### 6.1 Required Indexes

| Index Name                               | Columns                                    | Type      | Purpose                                                        |
|------------------------------------------|---------------------------------------------|-----------|----------------------------------------------------------------|
| `idx_appointment_doctor_start`           | `(doctor_id, start_at)`                     | B-tree    | Slot calculation: quickly find all appointments for a doctor on a given date |
| `idx_appointment_doctor_status`          | `(doctor_id, status)`                       | B-tree    | Filter by doctor and active status for overlap checks          |
| `idx_appointment_status`                 | `(status)`                                  | B-tree    | Auto-expire task: find all HOLD/PENDING records                |
| `idx_appointment_hold_expires`           | `(hold_expires_at)`                         | B-tree    | Auto-expire task: find expired HOLDs efficiently               |
| `idx_appointment_pending_expires`        | `(pending_expires_at)`                      | B-tree    | Auto-expire task: find expired PENDING/PROPOSED records        |
| `idx_appointment_patient`                | `(patient_id, status)`                      | B-tree    | `my_appointments` view: list patient's appointments by status  |
| `idx_appointment_clinic_date`            | `(clinic_id, start_at)`                     | B-tree    | Secretary dashboard: list clinic's appointments by date        |
| `idx_appointment_doctor_date_status`     | `(doctor_id, start_at, status)`             | Composite | **Primary performance index** — covers slot calculation query  |
| `idx_notification_appointment_type`      | `(appointment_id, notification_type)`       | B-tree    | Reminder dedup: check if REMINDER_24H already sent             |
| `idx_notification_status_retry`          | `(status, last_retry_at)`                   | B-tree    | Retry queue: find failed notifications needing retry           |

### 6.2 Partial Indexes (Recommended)

| Index Name                               | Columns                             | Condition                                         | Purpose                                    |
|------------------------------------------|--------------------------------------|---------------------------------------------------|--------------------------------------------|
| `idx_active_appointments_doctor`         | `(doctor_id, start_at, end_at)`     | `WHERE status IN ('HOLD','PENDING_APPROVAL','PROPOSED_TIME','CONFIRMED')` | Overlap check during HOLD creation — only scans active records |
| `idx_hold_expiring`                      | `(hold_expires_at)`                 | `WHERE status = 'HOLD'`                           | Auto-expire task — only scans HOLD records |
| `idx_pending_expiring`                   | `(pending_expires_at)`              | `WHERE status IN ('PENDING_APPROVAL','PROPOSED_TIME')` | Auto-expire task — only scans pending records |

### 6.3 Rationale

- **`(doctor_id, start_at, status)`** is the most critical index. The slot calculation algorithm queries: "Give me all appointments for doctor X on date Y with an active status." This composite index enables an index-only scan for the most frequent query in the system.
- **`(hold_expires_at)` with partial `WHERE status = 'HOLD'`** ensures the auto-expire task scans only the small subset of HOLD records, not the entire appointment table.
- **`(appointment_id, notification_type)`** supports the `REMINDER_24H` deduplication check — the system excludes appointments that already have a REMINDER_24H log entry.

---

## 7. Slot Calculation Data Strategy

### 7.1 Input Data

The slot calculation engine requires three data sources:

1. **DoctorAvailability** — provides the time windows.
2. **AppointmentType.duration_minutes** — provides the slot length.
3. **Appointment** (active statuses) — provides the blocked ranges.

### 7.2 Data Flow

```
DoctorAvailability                    AppointmentType
(day_of_week, start_time, end_time)   (duration_minutes)
         │                                    │
         ▼                                    ▼
    ┌─────────────────────────────────────────────┐
    │       Generate Candidate Slots              │
    │  For each availability window:              │
    │    slot = window.start_time                  │
    │    while slot + duration <= window.end_time: │
    │      candidates.append(slot, slot+duration)  │
    │      slot += duration                        │
    └────────────────────┬────────────────────────┘
                         │
                         ▼
    Appointment (active statuses for same doctor + date)
    (start_at, end_at) — blocked ranges
         │
         ▼
    ┌─────────────────────────────────────────────┐
    │       Filter Out Blocked Slots              │
    │  For each candidate:                        │
    │    if overlaps(candidate, any blocked):      │
    │      remove from candidates                  │
    └────────────────────┬────────────────────────┘
                         │
                         ▼
              Available Slots (returned to patient)
```

### 7.3 Handling PROPOSED_TIME Appointments

When filtering blocked slots, the engine must account for `PROPOSED_TIME` appointments specially:

- The **original range** `(start_at, end_at)` is **released** (not blocking).
- The **proposed range** `(proposed_start_at, proposed_end_at)` **is blocking**.

Query logic:

```
blocking_ranges = []

# Standard active appointments
for appt in Appointment.objects.filter(
    doctor_id=doctor_id,
    start_at__date=target_date,
    status__in=['HOLD', 'PENDING_APPROVAL', 'CONFIRMED']
):
    blocking_ranges.append((appt.start_at, appt.end_at))

# PROPOSED_TIME — use proposed range, not original
for appt in Appointment.objects.filter(
    doctor_id=doctor_id,
    proposed_start_at__date=target_date,
    status='PROPOSED_TIME'
):
    blocking_ranges.append((appt.proposed_start_at, appt.proposed_end_at))
```

### 7.4 Duration Interaction

- If a doctor offers multiple `AppointmentType` values at the same clinic (e.g., 15-min follow-up, 30-min consultation, 60-min assessment), the slot grid is generated using the **selected** type's `duration_minutes`.
- This means the same availability window produces **different slot grids** depending on the selected appointment type.
- Example:
  - Availability: 09:00 – 12:00
  - 15-min type → 12 slots
  - 30-min type → 6 slots
  - 60-min type → 3 slots

---

## 8. Data Integrity Rules

### 8.1 Status ↔ Field Consistency

| Rule                                                                 | Enforcement        |
|----------------------------------------------------------------------|--------------------|
| `status = HOLD` → `hold_expires_at IS NOT NULL`                      | CheckConstraint    |
| `status = PENDING_APPROVAL` → `pending_expires_at IS NOT NULL`       | CheckConstraint    |
| `status = PROPOSED_TIME` → `proposed_start_at IS NOT NULL AND proposed_end_at IS NOT NULL` | CheckConstraint |
| `status NOT IN (HOLD)` → `hold_expires_at IS NULL`                   | Application logic  |
| `status NOT IN (PENDING_APPROVAL, PROPOSED_TIME)` → `pending_expires_at IS NULL` | Application logic |
| `status NOT IN (PROPOSED_TIME)` → `proposed_start_at IS NULL AND proposed_end_at IS NULL` | Application logic |

### 8.2 Temporal Integrity

| Rule                                         | Enforcement        |
|----------------------------------------------|--------------------|
| `start_at < end_at`                          | CheckConstraint    |
| `start_at >= now() - 5min` (at creation)     | Application logic  |
| `proposed_start_at < proposed_end_at`        | CheckConstraint    |
| `hold_expires_at > start_at`                 | Application logic  |
| `pending_expires_at > created_at`            | Application logic  |

### 8.3 Referential Integrity

| Rule                                                            | Enforcement           |
|-----------------------------------------------------------------|-----------------------|
| `doctor` must be a user with `role IN ('DOCTOR', 'MAIN_DOCTOR')` | Application logic    |
| `patient` must be a user with `role = 'PATIENT'`                | Application logic     |
| `appointment_type.doctor == doctor`                             | Application logic     |
| `appointment_type.clinic == clinic`                             | Application logic     |
| `doctor` must have an active `DoctorAvailability` covering `start_at` | Application logic |

### 8.4 State Transition Integrity

All status transitions must be validated against the allowed transition map (defined in Workflow doc Section 16.3). Attempting an invalid transition (e.g., `EXPIRED → CONFIRMED`) must raise a `ValidationError`.

Implementation:

```
ALLOWED_TRANSITIONS = {
    'HOLD':              ['PENDING_APPROVAL', 'CONFIRMED', 'EXPIRED'],
    'PENDING_APPROVAL':  ['CONFIRMED', 'REJECTED', 'PROPOSED_TIME', 'EXPIRED', 'CANCELLED'],
    'PROPOSED_TIME':     ['CONFIRMED', 'CANCELLED', 'EXPIRED'],
    'CONFIRMED':         ['COMPLETED', 'NO_SHOW', 'CANCELLED'],
    'REJECTED':          [],  # Terminal
    'EXPIRED':           [],  # Terminal
    'CANCELLED':         [],  # Terminal
    'COMPLETED':         [],  # Terminal
    'NO_SHOW':           [],  # Terminal
}
```

This map should be enforced in the `Appointment.save()` method or a dedicated state machine service.

---

## 9. Cleanup Strategy

### 9.1 Overview

Stale records in HOLD and PENDING states must be cleaned up to free blocked slots. This is handled by a periodic background task.

### 9.2 HOLD Cleanup

**Frequency:** Every 2 minutes  
**Query:**

```
Appointment.objects.filter(
    status='HOLD',
    hold_expires_at__lte=now()
)
```

**Action:**
1. Set `status = 'EXPIRED'`.
2. Set `hold_expires_at = NULL`.
3. Save with `update_fields=['status', 'hold_expires_at', 'updated_at']`.
4. No SMS sent (HOLD is a transient, invisible state).

**Volume:** HOLD records are inherently short-lived (10 minutes). Even under high load, the number of expired HOLDs per cleanup cycle should be small (< 100).

### 9.3 PENDING_APPROVAL Cleanup

**Frequency:** Every 2 minutes  
**Query:**

```
Appointment.objects.filter(
    status='PENDING_APPROVAL',
    pending_expires_at__lte=now()
)
```

**Action:**
1. Set `status = 'EXPIRED'`.
2. Set `pending_expires_at = NULL`.
3. Save.
4. Send SMS to patient: "Your appointment request has expired."
5. Create `NotificationLog`.

### 9.4 PROPOSED_TIME Cleanup

**Frequency:** Every 2 minutes  
**Query:**

```
Appointment.objects.filter(
    status='PROPOSED_TIME',
    pending_expires_at__lte=now()
)
```

**Action:**
1. Set `status = 'EXPIRED'`.
2. Set `pending_expires_at = NULL`, `proposed_start_at = NULL`, `proposed_end_at = NULL`.
3. Save.
4. Send SMS to patient: "The proposed appointment time has expired."
5. Create `NotificationLog`.

### 9.5 Bulk Update Optimization

For large-scale cleanup, use `queryset.update()` for the status change, then iterate individually for SMS sending (which is I/O-bound anyway):

```
# Step 1: Bulk status update
expired_ids = list(
    Appointment.objects.filter(
        status='PENDING_APPROVAL',
        pending_expires_at__lte=now()
    ).values_list('id', flat=True)
)

Appointment.objects.filter(id__in=expired_ids).update(
    status='EXPIRED',
    pending_expires_at=None,
    updated_at=now()
)

# Step 2: Send SMS for each (async)
for appt_id in expired_ids:
    send_expiration_sms.delay(appt_id)
```

### 9.6 Historical Record Retention

- `EXPIRED`, `CANCELLED`, `REJECTED` records are **never deleted**. They serve as audit history.
- A data retention policy may archive records older than 12 months to a cold storage table, but this is a future optimization.

---

## 10. SMS Notification Data Strategy

### 10.1 Role of NotificationLog

`NotificationLog` serves three purposes:

1. **Audit Trail:** Complete history of all SMS messages sent, including content, recipient, and delivery status.
2. **Deduplication Gate:** Before sending a notification, check if one of the same type already exists for this appointment. Prevents duplicate reminders or confirmations.
3. **Retry Queue:** Failed notifications are retried based on `status = 'FAILED'` and `retry_count < 3`.

### 10.2 Notification Lifecycle

```
┌─────────┐     Send attempt     ┌──────┐
│ PENDING │ ───────────────────► │ SENT │
└────┬────┘                      └──────┘
     │
     │ Send fails
     ▼
┌────────┐     Retry (up to 3x)     ┌──────┐
│ FAILED │ ────────────────────────► │ SENT │
└────┬───┘                           └──────┘
     │
     │ 3 retries exhausted
     ▼
┌──────────────────┐
│ PERMANENTLY_FAILED│
└──────────────────┘
```

### 10.3 Retry Logic

- **Retry Schedule:** 1 minute, 5 minutes, 15 minutes (exponential backoff).
- **Retry Task Criteria:**
  ```
  NotificationLog.objects.filter(
      status='FAILED',
      retry_count__lt=3,
      last_retry_at__lte=now() - backoff_duration(retry_count)
  )
  ```
- After 3 failures, status set to `PERMANENTLY_FAILED` and an admin alert is generated.

### 10.4 Separation of Concerns

- **Booking logic does NOT wait for SMS.** All SMS sending is asynchronous (Celery task or async call).
- The booking transaction creates the `NotificationLog` with `status = 'PENDING'`, then dispatches the actual SMS send to a background worker.
- This ensures SMS provider outages never block appointment booking.

---

## 11. Migration Strategy

### 11.1 Overview

The `Appointment` model requires significant structural changes:
- New fields: `start_at`, `end_at`, `hold_expires_at`, `pending_expires_at`, `proposed_start_at`, `proposed_end_at`, `visit_kind`, `rejection_reason`, `cancellation_reason`.
- Modified fields: `status` choices updated from 7 to 9 values.
- Deprecated fields: `appointment_date`, `appointment_time` (to be removed after data migration).
- New models: 6 new tables.

### 11.2 Migration Plan (Zero-Downtime)

#### Phase 1: Add New Fields (Non-Breaking)

**Migration 1:** Add all new nullable fields to `Appointment`.

```
Operations:
- AddField('start_at', DateTimeField, null=True)
- AddField('end_at', DateTimeField, null=True)
- AddField('hold_expires_at', DateTimeField, null=True)
- AddField('pending_expires_at', DateTimeField, null=True)
- AddField('proposed_start_at', DateTimeField, null=True)
- AddField('proposed_end_at', DateTimeField, null=True)
- AddField('visit_kind', CharField, default='IN_PERSON')
- AddField('rejection_reason', TextField, blank=True, default='')
- AddField('cancellation_reason', TextField, blank=True, default='')
```

**Why safe:** All new fields are nullable or have defaults. Existing code continues to work.

#### Phase 2: Create New Tables

**Migration 2:** Create all six new models.

```
Operations:
- CreateModel('DoctorIntakeFormTemplate')
- CreateModel('DoctorIntakeQuestion')
- CreateModel('DoctorIntakeRule')
- CreateModel('AppointmentAnswer')
- CreateModel('AppointmentAttachment')
- CreateModel('NotificationLog')
```

**Why safe:** New tables have no effect on existing code.

#### Phase 3: Data Migration

**Migration 3:** Backfill `start_at` and `end_at` from existing data.

```
For each existing Appointment:
    appointment.start_at = combine(appointment_date, appointment_time)
    appointment.end_at = start_at + timedelta(minutes=appointment_type.duration_minutes or 30)
    appointment.save()
```

**Why safe:** Forward-only data transformation. Old fields still exist and readable.

#### Phase 4: Update Status Choices

**Migration 4:** Alter `status` field to include the new choices.

```
Operations:
- AlterField('status', CharField, choices=NEW_STATUS_CHOICES, default='HOLD')
```

**Mapping of old → new statuses:**

| Old Status     | New Status       |
|----------------|------------------|
| `PENDING`      | `PENDING_APPROVAL` (rename) |
| `CONFIRMED`    | `CONFIRMED`      |
| `CHECKED_IN`   | `CONFIRMED` (merge) |
| `IN_PROGRESS`  | `CONFIRMED` (merge) |
| `COMPLETED`    | `COMPLETED`      |
| `CANCELLED`    | `CANCELLED`      |
| `NO_SHOW`      | `NO_SHOW`        |

**Data migration:** Update records with old status values to the new mapping.

**Why safe:** CharField choices are not enforced at the DB level in PostgreSQL. The migration only changes the Python-side choices.

#### Phase 5: Add Constraints and Indexes

**Migration 5:** Add database constraints (exclusion constraint for overlap prevention) and all indexes.

```
Operations:
- RunSQL(enable btree_gist extension)
- RunSQL(add exclusion constraint)
- AddIndex (all indexes from Section 6)
- AddConstraint (all check constraints from Section 5)
```

**Why safe:** Constraints are additive. If existing data violates constraints, Phase 3 should have resolved this. Run a validation script before this migration.

#### Phase 6: Make Fields Non-Nullable

**Migration 6:** After verifying Phase 3 backfilled all records:

```
Operations:
- AlterField('start_at', DateTimeField, null=False)
- AlterField('end_at', DateTimeField, null=False)
```

**Why safe:** Phase 3 guaranteed no NULL values remain.

#### Phase 7: Remove Deprecated Fields (Deferred)

**Migration 7:** Remove `appointment_date` and `appointment_time`.

```
Operations:
- RemoveField('appointment_date')
- RemoveField('appointment_time')
```

> **⚠ WARNING:** This migration should be executed **only after** all views and serializers have been updated to use `start_at`/`end_at`. Schedule this for a future release.

### 11.3 Rollback Safety

- Phases 1–5 are **fully reversible** via Django's `migrate <app> <previous_migration>`.
- Phase 6 is reversible by making fields nullable again.
- Phase 7 is **irreversible** in terms of data (date/time would need to be re-generated from `start_at`). Ensure Phase 7 is only executed after thorough validation.

### 11.4 Pre-Migration Checklist

- [ ] Backup the production database.
- [ ] Run the data validation script to identify records that may violate new constraints.
- [ ] Deploy code changes that support both old and new fields (dual-write period).
- [ ] Execute migrations in a maintenance window or use zero-downtime migration patterns.
- [ ] Monitor error rates for 24 hours after each phase.

---

> **End of Data Model Specification**
