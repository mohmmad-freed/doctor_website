# Key Modules & Architecture Responsibilities

## 1. `accounts` App
**Core Responsibility**: Identity & Authentication (Global Scope)
-   **Models**: `User` (Custom User with Mobile/NID), `OneTimePassword`
-   **Key Logic**:
    -   Registration (Doctor/Patient/Staff)
    -   Login (Mobile + Password)
    -   Password Reset / OTP Verification
    -   Profile Management (Global Name/Avatar)

## 2. `clinics` App
**Core Responsibility**: Tenant Management (Clinic Scope)
-   **Models**: `Clinic`, `ClinicStaff`, `ClinicSettings`, `ClinicHolidays`
-   **Key Logic**:
    -   Creating and configuring new Clinics.
    -   Managing clinic-wide rules (working hours, holidays).
    -   Role-based access control (RBAC) for staff within a clinic.
    -   **Middleware**: Ensuring request context (`request.clinic`) is correctly set.

## 3. `doctors` App
**Core Responsibility**: Provider Management
-   **Models**: `DoctorProfile`, `DoctorAvailability`, `Specialization`, `IntakeQuestion`
-   **Key Logic**:
    -   Managing doctor credentials and bio.
    -   **Availability Engine**: Calculating free slots based on `DoctorAvailability` vs `Appointment`.
    -   Conflict Detection: Ensuring global non-overlap of doctor schedules.
    -   **Intake Configuration**: Managing doctor-defined questions (Pre-visit questionnaires).

## 4. `patients` App
**Core Responsibility**: Patient Records
-   **Models**: `PatientProfile` (Global Medical Info), `ClinicPatient` (Local Record)
-   **Key Logic**:
    -   Linking global users to specific clinics.
    -   Managing local file numbers.
    -   Storing clinic-specific notes, balance, and history.

## 5. `appointments` App
**Core Responsibility**: Booking Engine
-   **Models**: `Appointment`, `TimeSlot` (Virtual/Transient), `AppointmentAttachment`
-   **Key Logic**:
    -   **Booking**: Validating availability, creating appointments.
    -   **Attachment Handling**: Secure upload and storage of appointment-related files.
    -   **Status Workflow**: Handling transitions (Confirm, Check-In, Complete).
    -   Calendar Views: Generating schedule grids for doctors/receptionists.
    -   Notifications: Triggering SMS/Email on booking events.

## 6. `secretary` App
**Core Responsibility**: Receptionist Dashboard (Frontend Logic)
-   **Views**: Dashboard, Waiting List, Rapid Booking Form.
-   **Logic**:
    -   UI-intensive views for daily operations.
    -   HTMX endpoints for interactive scheduling updates.

## 7. `clinic_website` App
**Core Responsibility**: Public Facing Pages / Patient Portal
-   **Views**: Clinic Landing Page, Doctor Directory, Online Booking Wizard.
-   **Logic**:
    -   Publicly accessible routes for patients to find info.
    -   Self-service booking interface (if enabled).
