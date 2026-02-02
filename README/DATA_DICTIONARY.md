# Data Dictionary

## 1. Core Users & Authentication

### `accounts.User` (Standard Django Custom User)
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `id` | UUID | PK | Global Unique Identifier |
| `username` | Char | Unique | Internal system handle |
| `mobile` | Char | Unique | **Primary Login Identifier** |
| `password` | Hash | | Standard hashed password |
| `email` | Email | Unique, Optional | Email address for notifications |
| `national_id` | Char | Unique | **Identity Verification Only** (Not for login) |
| `user_type` | Enum | `PATIENT`, `DOCTOR`, `STAFF`, `SUPER_ADMIN` | Role categorization |

## 2. Global Profiles

### `patients.PatientProfile`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `user` | OneToOne | FK -> `User` | Links to global user account |
| `date_of_birth` | Date | | Essential for medical calculations |
| `gender` | Enum | `M`, `F`, `O` | Biological sex or gender identity |
| `blood_group` | Char | Optional | A+, O-, etc. |

### `doctors.DoctorProfile`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `user` | OneToOne | FK -> `User` | Links to global user account |
| `medical_license` | Char | Unique | License number |
| `specialization` | Char | | Cardiology, Dermatology, etc. |
| `bio` | Text | | Public bio for booking page |

### `doctors.IntakeQuestion` (New)
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `doctor` | FK | -> `User` (Doctor) | The doctor defining the question |
| `question_text` | Char | | "Reason for visit?", "First time?" |
| `question_type` | Enum | `TEXT`, `SINGLE_CHOICE`, `MULTI_CHOICE`, `BOOLEAN` | Input type |
| `options` | JSON | Nullable | e.g. `["Consultation", "Follow-up"]` |
| `is_required` | Bool | Default=False | Mandatory question? |

## 3. Clinic Structure (Tenant Isolation)

### `clinics.Clinic`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `id` | UUID | PK | **Tenant ID** |
| `name` | Char | | Clinic Name |
| `subdomain` | Char | Unique, Optional | For subdomain routing (e.g., `heartcenter.app.com`) |
| `settings` | JSON | Default={} | Clinic-wide configs (booking rules, open hours) |

### `clinics.ClinicStaff`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `user` | FK | -> `User` | The staff member |
| `clinic` | FK | -> `Clinic` | The clinic they work at |
| `role` | Enum | `RECEPTIONIST`, `ADMIN`, `NURSE` | Access level within this clinic |

### `patients.ClinicPatient`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `clinic` | FK | -> `Clinic` | **Tenant Scope** |
| `patient` | FK | -> `PatientProfile` | Global patient reference |
| `file_number` | Char | Unique per Clinic | Local ID (e.g., "HC-2023-001") |
| `balance` | Decimal | Default=0.00 | Outstanding balance at this specific clinic |

## 4. Scheduling & Appointments

### `doctors.DoctorAvailability`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `doctor` | FK | -> `User` (Doctor) | Who is available? |
| `clinic` | FK | -> `Clinic` | Where are they available? |
| `day_of_week` | Int | 0=Mon, 6=Sun | Recurring weekly pattern |
| `start_time` | Time | | Shift start |
| `end_time` | Time | | Shift end |

### `appointments.Appointment`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `id` | UUID | PK | |
| `clinic` | FK | -> `Clinic` | **Tenant Scope** |
| `doctor` | FK | -> `User` (Doctor) | Provider |
| `patient` | FK | -> `ClinicPatient` | Patient context (local record) |
| `date` | Date | | Appointment Date |
| `start_time` | Time | | Slot start |
| `end_time` | Time | | Slot end |
| `status` | Enum | `PENDING`, `CONFIRMED`, `COMPLETED`, `CANCELLED` | Lifecycle state |
| `intake_responses` | JSON | Default={} | Answers to `IntakeQuestion`s |

### `appointments.AppointmentAttachment` (New)
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `appointment` | FK | -> `Appointment` | Parent Appointment |
| `file` | File | | The uploaded document/image |
| `uploaded_at` | DateTime| AutoNow | Timestamp |
| `description` | Char | Optional | "Lab Report", "X-Ray" |

### `clinics.ClinicHolidays`
| Field | Type | Attributes | Description |
| :--- | :--- | :--- | :--- |
| `clinic` | FK | -> `Clinic` | **Tenant Scope** |
| `date` | Date | | Clinic closed on this date |
| `reason` | Char | | "National Holiday", "Renovation" |
