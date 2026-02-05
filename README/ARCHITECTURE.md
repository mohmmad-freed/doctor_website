# System Architecture

## 1. High-Level Architecture

The system is designed as a **Single-Database, Shared-Schema Multi-Tenant SaaS**. This means all tenants (clinics) share the same database tables, but data is logically isolated using a `clinic_id` foreign key on every tenant-specific record.

### Tenant Isolation Strategy
-   **Strict Filtering**: Every database query for tenant-specific data must include a `WHERE clinic_id = X` clause. This is enforced at the Django Manager level where possible.
-   **Global Resources**: `User`, `DoctorProfile`, and `PatientProfile` (demographics) are global to allow cross-clinic operability (e.g., a patient visiting Clinic A and Clinic B).
-   **Local Resources**: `ClinicPatient`, `Appointment`, `MedicalRecord`, `Invoice` are strictly scoped to a single `Clinic`.

## 2. Django Project Structure (MVT)

The project follows the standard Django MVT (Model-View-Template) pattern, but structured for scalability:

**Core Apps:**
-   **`accounts`**: Custom User model, Authentication (Session & JWT), Global Profiles.
-   **`clinics`**: Clinic management, Clinic Settings, Clinic Staff roles.
-   **`patients`**: Patient management logic, `ClinicPatient` associations.
-   **`appointments`**: Scheduling logic, Slot management, Calendar views, Attachments.
-   **`medical`**: EMR (Electronic Medical Records), Prescriptions (future phase).

**Frontend Integration:**
-   **Templates**: Server-side rendered HTML for SEO and initial load speed.
-   **HTMX**: Used for dynamic interactions (e.g., booking form validation, slot availability checking) without full page reloads.
-   **Static Files**: Served via WhiteNoise or Nginx.

## 3. Data Isolation & Security

### Tenant Identification
How do we know which clinic is active?
1.  **Staff Context**: Logged-in staff (Receptionist/Admin) are linked to a single `Clinic` via `ClinicIsolationMiddleware`. Their session automatically scopes all operations to their clinic.
2.  **Patient Context**: When a patient logs in, they see a dashboard. If booking, they select a clinic context or browse a global directory (depending on config).

### Query Enforcement
-   **Middleware**: `ClinicIsolationMiddleware` runs on every request. It ensures staff members are linked to a valid clinic and sets `request.clinic` and `request.clinic_id`. It also blocks patients from accessing staff-only areas.
-   **Custom Managers**: Use Django's `Manager` class to override `get_queryset()` and automatically filter by `request.user.clinic_id` where applicable.

## 4. Authentication & Authorization

The system uses a hybrid authentication approach:

### 1. Web Interface (Clinics & Staff)
-   **Mechanism**: Standard Django **Sessions**.
-   **Flow**: User logs in -> Session ID stored in cookie -> Middleware validates session.
-   **Roles**: Managed via Group permissions (e.g., "Clinic Admin", "Receptionist").

### 2. API (Mobile Apps & 3rd Party)
-   **Mechanism**: **JWT (JSON Web Tokens)** via `CheckClinic` middleware.
-   **Flow**: Client exchanges credentials for Access/Refresh tokens -> Token included in `Authorization: Bearer` header.

### 3. Login Logic
-   **Identifier**: Users log in using **Mobile Number** + **Password**.
-   **Verification**: **National ID** is collected during registration to verify identity and enforce uniqueness, but is **NOT** used as a daily login credential.

## 5. Doctor Availability Architecture
To prevent overbooking across clinics, Doctor Availability is treated as a **Global Resource constraint**.
-   **Availability Table**: Links `DoctorUser` to `TimeSlot`.
-   **Conflict Check**: Before confirming ANY appointment, the system checks the `Appointment` table across **ALL** clinics for that `DoctorUser` for overlapping times.
