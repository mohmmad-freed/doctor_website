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
