# Business Rules

## 1. Tenant & Clinic Isolation

### R-01: Strict Data Isolation
-   Every operation performed by **Clinic Staff** MUST be scoped to their associated `clinic_id`. They cannot view, edit, or create records for any other clinic.
-   **Exceptions**: Super Admins may oversee multiple clinics (if architected as a platform-level role).

### R-02: User Uniqueness
-   A **User** (Patient/Doctor/Staff) is identified globally by their **Mobile Number** and **National ID**.
-   **Rule**: A user cannot register twice with the same Mobile Number or National ID, even if registering for a different clinic. The system must prompt them to link their existing account instead.

## 2. Doctor Availability & Scheduling

### R-03: Global Doctor Availability
-   A **Doctor** can be associated with multiple clinics (Many-to-Many).
-   **Constraint**: A Doctor CANNOT have overlapping appointments across ANY clinic.
-   **Validation**: Before confirming an appointment at Clinic A, the system must check for any confirmed appointments at Clinic B, C, etc., for that same time slot.

### R-04: Schedule Definition
-   Doctors (or their admins) define working hours **per clinic**.
-   Examples:
    -   Mon/Wed: Clinic A (09:00 - 14:00)
    -   Tue/Thu: Clinic B (10:00 - 18:00)
-   The system enforces that defined shifts do not overlap.

## 3. Patient Management

### R-05: Global Profile vs. Local Record
-   **Global Profile**: Contains demographic data (Name, DOB, Gender, National ID, Mobile). Changes here reflect across all clinics.
-   **Local Record (`ClinicPatient`)**: Contains clinic-specific data (Patient ID within that clinic, Balance, Notes, Tag, First Visit Date). Private to that clinic.

### R-06: Visit History Privacy
-   A clinic can ONLY see the appointment and medical history for visits that occurred at **their facility**. They cannot see visits to other clinics unless explicit consent is granted (future feature).

## 4. Appointment Booking

### R-07: Booking Authority
-   **Default**: Only **Receptionists/Admins** can create appointments.
-   **Optional Override**: A Clinic can enable "Direct Patient Booking" in their settings. If enabled, patients can book their own slots via the patient portal/app.

### R-08: Appointment Status Lifecycle
The standard lifecycle is:
1.  **Pending/Scheduled**: Initial state.
2.  **Confirmed**: (Optional explicit confirmation step).
3.  **Checked-In**: Patient arrived at clinic.
4.  **In-Progress**: Consultation started.
5.  **Completed**: Consultation finished.
6.  **Cancelled**: Cancelled by patient or clinic.
7.  **No-Show**: Patient did not arrive.

### R-09: Modification Rules
-   **Past Appointments**: Cannot be modified (except for adding medical notes or billing).
-   **Cancellations**: Must be done >X hours before the slot (configurable per clinic).

### R-11: Intake Questionnaires
-   Doctors can define pre-appointment intake options (e.g., "Reason for Visit", "Service Type", "Past Surgery History").
-   These questions can be **Mandatory** or **Optional**.
-   Answers must be collected before the appointment is confirmed (during the booking flow).

### R-12: Appointment Attachments
-   Patients (or receptionists on their behalf) can upload files/images (e.g., previous lab results, X-rays) to an appointment.
-   Attachments must be linked exclusively to the specific appointment and clinic.

## 5. Billing & Invoicing

### R-10: Invoice Generation
-   Invoices are generated PER APPOINTMENT or PER SERVICE.
-   Invoices belong strictly to the clinic that issued them.

## 6. Authentication Rules

### R-13: Login Credentials
-   **Primary Login**: Users authenticate using **Mobile Number** + **Password**.
-   **National ID Usage**: strictly restricted to **Identity Verification** and ensuring account uniqueness during registration. It is NOT used for daily login.
