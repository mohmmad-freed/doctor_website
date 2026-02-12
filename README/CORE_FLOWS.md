# Core Workflows

## 1. Patient Registration (New Patient Flow)
**Actor:** Receptionist / Clinic Admin

1.  **Search**: Receptionist searches for patient by **Mobile Number** or **National ID**.
2.  **No Match**:
    -   Receptionist inputs: First Name, Last Name, DOB, Gender, Mobile, National ID.
    -   **Validation**: National ID is verified for uniqueness to prevent duplicate global accounts.
    -   System creates:
        -   Global `User` account.
        -   Global `PatientProfile`.
        -   Local `ClinicPatient` record linked to the current Clinic.
    -   System assigns a unique **File Number** for this clinic.
3.  **Match Found (Existing Global Patient)**:
    -   System displays "Patient exists globally but not in this clinic".
    -   Receptionist clicks "Register in Clinic".
    -   System creates `ClinicPatient` record linked to the current Clinic.
    -   Patient history at OTHER clinics remains HIDDEN.

## 2. Appointment Booking
**Actor:** Receptionist (Default) or Patient (if enabled)

1.  **Select Context**:
    -   Receptionist selects **Doctor** and **Date**.
2.  **View Availability**:
    -   System queries `DoctorAvailability` for the selected doctor at THIS clinic.
    -   System filters out **Already Booked** slots (at ANY clinic).
    -   System displays available slots.
3.  **Select Slot**:
    -   Receptionist clicks a time slot (e.g., 10:00 AM).
4.  **Intake & Attachments (New)**:
    -   System displays doctor-defined **Intake Options** (e.g., "Visit Reason", "Type of Service").
    -   Patient/Receptionist answers mandatory questions.
    -   (Optional) Patient/Receptionist uploads relevant files (images/documents) to the appointment.
5.  **Assign Patient**:
    -   Receptionist searches/selects the patient.
6.  **Confirm**:
    -   System creates `Appointment` record with status `PENDING` or `CONFIRMED`.
    -   Intake answers and File Attachments are saved with the appointment.
    -   SMS/Email notification sent to Patient (if configured).

## 3. Doctor Availability Setup
**Actor:** Doctor / Clinic Admin

1.  **Login**: Doctor logs in.
2.  **Dashboard**: Sees list of clinics they work at.
3.  **Manage Schedule**: Selects a specific clinic (e.g., "City Eye Clinic").
4.  **Set Hours**:
    -   Monday: 09:00 - 13:00.
    -   Wednesday: 15:00 - 19:00.
5.  **Validation**:
    -   System checks if these times overlap with hours set at "Downtown Cardio Clinic".
    -   **If Overlap**: Error "Time conflict with another clinic schedule".
    -   **If Valid**: Schedule saved.

## 4. Clinic Onboarding (Admin)
**Actor:** Platform Super Admin

1.  **Create Clinic**: Admin enters Clinic Name, Address, Contact Info.
2.  **Create Admin User**: Admin creates a `User` with role `CLINIC_ADMIN` linked to this clinic.
3.  **Configure Settings**:
    -   Set default appointment duration (e.g., 15 mins).
    -   Enable/Disable Patient Portal booking.
    -   Set operating hours.
4.  **Handover**: Credentials sent to Clinic Admin.

## 5. Consumer Authentication & Registration
**Actor:** Patient (Self-Service)

1.  **Patient Self-Registration**:
    -   **Step 1: Phone Entry**: User enters mobile number. System validates format and checks if number is already in use.
    -   **Step 2: OTP Verification**: System sends SMS OTP. User enters code. (Stateless/Session-based verification).
    -   **Step 3: Profile Creation**: User enters Name, Password, Gender, Date of Birth.
    -   **Step 4: Optional Email**: User can add email now or later. If added, a verification link is sent (Async).
    -   **Result**: Global `User` and `PatientProfile` are created. User is logged in.

2.  **Login Flow**:
    -   User enters **Mobile Number** and **Password**.
    -   System checks credentials.
    -   **Enforcement**: If `ENFORCE_PHONE_VERIFICATION` is on, usage is blocked until phone is verified.
    -   **Redirect**: User is redirected to their role-specific dashboard (Patient/Doctor/Secretary).

3.  **Logout**:
    -   User clicks Logout. System clears session and redirects to Login.

## 6. Account Management
**Actor:** Authenticated User

1.  **Change Phone Number**:
    -   User requests change -> Enters NEW phone number.
    -   System checks uniqueness of NEW number.
    -   System sends OTP to NEW number.
    -   User verifies OTP -> System updates `User.phone`.

2.  **Change Email Address**:
    -   User requests change -> Enters NEW email.
    -   System checks uniqueness.
    -   System sends Verification Link to NEW email.
    -   **State**: Email is stored in `pending_email` until verified.
    -   User clicks link -> System updates `User.email` and clears pending state.

## 7. Provider Onboarding (Self-Service)
**Actor:** Main Doctor (Clinic Owner)

1.  **Main Doctor Registration**:
    -   User enters Personal Info (Name, Phone, Password) + Clinic Info (Name, Specialization, Contact).
    -   **Activation Code**: User must provide a valid `ClinicActivationCode`.
    -   **Result**:
        -   New `User` (Role: MAIN_DOCTOR) created.
        -   New `Clinic` created and linked to this doctor.
        -   User is logged in and redirected to Clinic Dashboard.
