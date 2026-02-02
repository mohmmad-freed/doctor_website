# Naming & Constants

## 1. Naming Conventions

### Models
-   **Classes**: `PascalCase` (e.g., `ClinicPatient`, `DoctorAvailability`)
-   **Fields**: `snake_case` (e.g., `first_name`, `date_of_birth`, `clinic_id`)
-   **Tenant Field**: Always name the foreign key to the Clinic model `clinic`.
-   **User Field**: Always name the foreign key to the User model `user`.

### URLs
-   **Endpoint**: `kebab-case` (e.g., `/api/v1/clinic-patients/`, `/dashboard/new-booking/`)
-   **Query Parameters**: `snake_case` (e.g., `?date=2023-10-01&doctor_id=123`)

## 2. Status Constants (Enums)

### User Role (`accounts.models.User.Role`)
| Key | Value | Description |
| :--- | :--- | :--- |
| `SUPER_ADMIN` | `SA` | System Owner |
| `CLINIC_ADMIN` | `CA` | Clinic Manager |
| `DOCTOR` | `DR` | Medical Provider |
| `RECEPTIONIST` | `RC` | Front Desk Staff |
| `PATIENT` | `PT` | End User |

### Appointment Status (`appointments.models.Status`)
| Key | Value | Description |
| :--- | :--- | :--- |
| `PENDING` | `PE` | Requested by Patient, not yet confirmed |
| `CONFIRMED` | `CF` | Confirmed by Clinic |
| `CHECKED_IN` | `CI` | Patient arrived at Clinic |
| `IN_PROGRESS` | `IP` | Doctor is seeing Patient |
| `COMPLETED` | `CP` | Visited ended successfully |
| `CANCELLED` | `CN` | Cancelled by either party |
| `NO_SHOW` | `NS` | Patient did not attend |

### Intake Question Type (`doctors.models.IntakeQuestionType`)
| Key | Value | Description |
| :--- | :--- | :--- |
| `TEXT` | `TXT` | Free text answer |
| `SINGLE_CHOICE` | `SCH` | Dropdown/Radio (select one) |
| `MULTI_CHOICE` | `MCH` | Checkbox (select multiple) |
| `BOOLEAN` | `BOL` | Yes/No toggle |

### Gender (`accounts.models.Gender`)
| Key | Value | Description |
| :--- | :--- | :--- |
| `MALE` | `M` | Male |
| `FEMALE` | `F` | Female |
| `OTHER` | `O` | Non-Binary / Prefer not to say |

## 3. Identifiers

### Slug Rules
-   **Clinic Usernames**: Must be alphanumeric + underscores only.
-   **File Numbers**: Format: `YYYY-XXXX` or `[ClinicCode]-XXXX`.
-   **Mobile**: E.164 format strictly (`+201234567890`).

### Time & Date
-   **Timezone**: `UTC` in database. Display in local clinic time.
-   **Date Format**: `YYYY-MM-DD` (ISO 8601) for API.
