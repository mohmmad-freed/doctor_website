# TODOs & Known Issues

## 1. Planned Phase 2 Features

### Medical Records (EMR)
-   [ ] **Prescriptions**: Digital prescription generation (PDF/Email).
-   [ ] **Lab Results**: Integration with lab systems or manual upload.
-   [ ] **Diagnosis Codes**: ICD-10 integration.

### Billing & Finance
-   [ ] **Invoicing**: PDF invoice generation.
-   [ ] **Insurance Claims**: Basic insurance provider management.
-   [ ] **Expenses**: Track clinic expenses (staff salary, rent).

### Patient Engagement
-   [ ] **SMS Reminders**: Automated appointment reminders via Twilio/local gateway.
-   [ ] **Mobile App**: Flutter/React Native app for patients to book directly.

## 2. Technical Debt / Limitations

### Scalability (Database)
-   **Current**: Single PostgreSQL database with shared schema.
-   **Risk**: As `Appointment` table grows (>10M rows), the global conflict check query (`SELECT * FROM appointments WHERE doctor_id=X AND time=Y`) might slow down.
-   **Mitigation**: Needs aggressive indexing on `(doctor_id, date, start_time)` and potentially partitioning by `date` (Year/Month).

### Authentication
-   **Current**: Session (Web) + JWT (API).
-   **Risk**: Ensuring consistent logout across devices.
-   **API Logout**: Server-side invalidation uses DB persistence. If `rest_framework_simplejwt.token_blacklist` app is not installed/migrated, the API Logout endpoint returns success but is Client-Side only (browser deletes tokens).

### Phone Verification Enforcement
-   **Feature Flag**: `ENFORCE_PHONE_VERIFICATION` (env var).
-   **Default**: `True` (secure-by-default).
-   **Purpose**: Controls whether phone verification is strictly enforced during login (Web & API).
-   **Temporary**: This switch is intended to be used only until full OTP verification is implemented. Once OTP is live, this should be removed and verification should be mandatory.

### File Attachments & Storage
-   **Risk**: User uploads (Documents/Images) can be large or malicious.
-   **Mitigation**:
    -   Implement Strict MIME-type checking (Allow only PDF, JPG, PNG).
    -   Set Max File Size limit (e.g., 5MB per file).
    -   Scan for malware using an external service/library (e.g., ClamAV).

### Timezones
-   **Assumption**: Currently assumes all clinics operate in the same timezone or handle local time conversion on the client side.
-   **Risk**: Cross-timezone appointments (telehealth) might need explicit timezone handling in `Availability` logic.

## 3. Assumptions Made

-   **Patient Uniqueness**: We assume Mobile + National ID is sufficient for unique global identification.
-   **Role Separation**: A user can be a DOCTOR in one clinic and a PATIENT in another (supported by model design but UI flow needs verification).
-   **Connectivity**: System requires constant internet connection (no offline mode planned).
