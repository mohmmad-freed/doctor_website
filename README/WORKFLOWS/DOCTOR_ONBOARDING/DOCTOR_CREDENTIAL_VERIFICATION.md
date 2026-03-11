# DOCTOR_CREDENTIAL_VERIFICATION.md

## Purpose

This document defines the comprehensive workflow for verifying a doctor's professional and identity credentials.
Because a doctor interacts with patients and provides medical care, platform access is heavily gated. Successful verification transforms a doctor's account from limited access to a fully active, public-facing state.

This document describes the business workflow, states, and logic, independent of UI implementation.

---

## Dual-Layer Verification Model

Doctor verification is separated into **two independent concepts** to ensure safety in multi-clinic environments.

### A) Identity Verification (Platform Level)

This verifies that the person is a **legitimate doctor identity on the platform**. It is performed once globally and applies to the doctor across all clinics.

**What it confirms:**
- The uploaded government-issued ID matches the registered name and national ID.
- The uploaded medical license / practice certificate is authentic and valid.

**States:**

| State | Description | Next Possible States |
|-------|-------------|----------------------|
| `IDENTITY_UNVERIFIED` | Default state upon account creation. No documents uploaded. | `IDENTITY_PENDING_REVIEW` |
| `IDENTITY_PENDING_REVIEW` | Documents submitted. Awaiting Admin decision. | `IDENTITY_VERIFIED`, `IDENTITY_REJECTED` |
| `IDENTITY_VERIFIED` | Admin approved identity documents. Doctor is a confirmed medical professional. | `IDENTITY_REVOKED` |
| `IDENTITY_REJECTED` | Admin denied identity documents. Doctor must fix issues. | `IDENTITY_PENDING_REVIEW` (after re-upload) |
| `IDENTITY_REVOKED` | Admin manually revoked a previously verified doctor's identity (e.g., fraud, license expiry). | `IDENTITY_PENDING_REVIEW` (if appeal allowed) |

---

### B) Clinical Credential Verification (Clinic / Specialty Level)

This verifies that the doctor is authorized to practice a **specific specialty within a specific clinic**. It is evaluated per clinic-doctor-specialty relationship.

**What it confirms:**
- The doctor holds a valid specialty certification for the specialty requested by the clinic.
- The doctor's credentials are appropriate for the services offered by that clinic.

**States:**

| State | Description | Next Possible States |
|-------|-------------|----------------------|
| `CREDENTIALS_PENDING` | The doctor has joined the clinic but specialty credentials have not been confirmed. | `CREDENTIALS_VERIFIED`, `CREDENTIALS_REJECTED` |
| `CREDENTIALS_VERIFIED` | Admin confirmed the doctor's specialty credentials for this clinic. | `CREDENTIALS_REVOKED` |
| `CREDENTIALS_REJECTED` | Specialty credentials were rejected. Doctor cannot practice this specialty at this clinic. | `CREDENTIALS_PENDING` (after re-submission) |
| `CREDENTIALS_REVOKED` | Previously verified credentials were revoked for this specific clinic. | `CREDENTIALS_PENDING` |

### When Clinical Credential Verification is Required

- When a **new doctor** joins a clinic for the first time and selects specialties.
- When an **existing, identity-verified doctor** joins a **new clinic** with different specialties.

### Important Rule

A doctor who is `IDENTITY_VERIFIED` and `CREDENTIALS_VERIFIED` at Clinic A does **NOT** automatically become `CREDENTIALS_VERIFIED` at Clinic B. Their clinical credentials must be independently reviewed for each clinic-specialty assignment.

---

## 1. Triggering Verification

### Identity Verification Trigger
The Identity Verification workflow begins immediately after a doctor has:
1. Accepted an invitation from a clinic.
2. Verified their contact methods (Phone and/or Email).

### Clinical Credential Verification Trigger
The Clinical Credential Verification is triggered:
1. When identity verification is complete (during first onboarding), OR
2. When an already identity-verified doctor accepts an invitation to a new clinic with new specialties.

---

## 2. Document Upload Process

The doctor is directed to a secure onboarding portal to provide required documentation.

### Required Documents (Identity Verification):
- **Identity Document:** Government-issued ID (e.g., National ID card, Passport).
- **Medical License / Practice Certificate:** Official proof of general entitlement to practice medicine.

### Required Documents (Clinical Credential Verification):
- **Specialty Certification:** Proof of specialty qualification relevant to the clinic's requested specialties (if applicable and not already on file).

### Upload Workflow:
1. The doctor selects and uploads the files.
2. The user interface provides immediate feedback if a file exceeds typical size limits (e.g., 10MB) or uses an unsupported format (e.g., non-PDF/JPEG/PNG).

---

## 3. Document Storage & Validation Rules

Once submitted to the backend, the system enforces the following rules before accepting the files for review:

### Validation Rules:
- **File Types:** Must be strictly limited to secure, non-executable image or document formats (PDF, JPEG, PNG, JPG).
- **Size Limits:** Enforced server-side (typically max 10MB per file).
- **Integrity Checks:** The system must actively scan for or reject malformed or corrupted files.

### Storage Rules:
- **Secure Handling:** Documents contain highly sensitive PII (Personally Identifiable Information). They must be stored in a secure, private bucket or directory where direct public web access is disabled.
- **Access Control:** Files can only be retrieved by the owning doctor and authorized Platform Administrators. They are never accessible to patients or even the Clinic Owner who invited the doctor.

---

## 4. Admin Review Workflow

Upon successful document submission, the doctor's relevant verification status transitions to `PENDING_REVIEW` or `CREDENTIALS_PENDING`.

### Platform Administration Responsibility
All verification is strictly a manual administrative process performed by **Platform Administrators** (not Clinic Owners).

### The Review Process:
1. Platform Admin logs into the secure administration portal.
2. The Admin navigates to the "Pending Verifications" queue (which contains both Identity and Clinical Credential reviews).
3. The Admin retrieves and inspects the uploaded documents.
4. For Identity Verification: The Admin cross-references the medical license with national or local registries if required by platform policy, and checks the uploaded ID against the doctor's registered platform details.
5. For Clinical Credential Verification: The Admin confirms the specialty certification is valid for the specialties assigned at the specific clinic.

---

## 5. Decision: Approval or Rejection

The Platform Admin must make a definitive decision on each submission.

### Scenario A: Approval Process
If the documents are deemed authentic and valid:
1. The Admin clicks "Approve".
2. For Identity: The doctor's status is updated to `IDENTITY_VERIFIED`.
3. For Clinical Credentials: The clinic-specialty record status is updated to `CREDENTIALS_VERIFIED`.
4. **Outcome:** The doctor gains the appropriate platform capabilities. They become visible to patients and can be scheduled only for verified specialties in verified clinics.

### Scenario B: Rejection Process
If the documents are invalid, expired, unreadable, or fraudulent:
1. The Admin clicks "Reject".
2. The Admin MUST select or enter a **Rejection Reason** (e.g., "Image too blurry", "License expired", "Name mismatch", "Specialty certificate does not match assigned specialty").
3. The relevant status is updated to `IDENTITY_REJECTED` or `CREDENTIALS_REJECTED`.
4. **Outcome:** The doctor remains locked out of the corresponding functionality.

---

## 6. Doctor Notification

The system must automatically notify the doctor immediately after the Admin makes a decision.
Notification will be sent via the primary notification channel (Email).

### Approval Notification:
- **Subject:** Your Clink Medical Profile is Approved
- **Body:** Welcomes the doctor, informs them that verification is complete, and provides a link to their active dashboard where they can set their clinic schedule.

### Rejection Notification:
- **Subject:** Action Required: Verification Documents Rejected
- **Body:** Informs the doctor that their documents could not be approved, explicitly lists the **Rejection Reason** provided by the Admin, and provides a secure link to the re-upload portal.

---

## 7. Secure Re-Upload Flow

If a doctor's documents are rejected, they do not need a new invitation.

### Re-Upload Process:
1. The doctor clicks the re-upload link in their email or logs into their dashboard.
2. They are presented with the Rejection Reason so they understand what to fix.
3. They upload new or corrected documents.
4. Upon submission, the previous rejected documents are archived or deleted (depending on compliance policies).
5. The doctor's status resets back to the pending review state.
6. The application is returned to the Admin queue for a fresh review.

---

## 8. Visibility Rules

A doctor can appear publicly and accept patient bookings for a specific clinic and specialty ONLY when **both** verification layers are satisfied:

| Identity Verification | Clinical Credential Verification | Doctor Visible to Patients? |
|-----------------------|----------------------------------|---------------------------|
| `IDENTITY_VERIFIED` | `CREDENTIALS_VERIFIED` | ✅ Yes |
| `IDENTITY_VERIFIED` | `CREDENTIALS_PENDING` | ❌ No |
| `IDENTITY_VERIFIED` | `CREDENTIALS_REJECTED` | ❌ No |
| `IDENTITY_PENDING_REVIEW` | Any | ❌ No |
| `IDENTITY_REJECTED` | Any | ❌ No |
| `IDENTITY_REVOKED` | Any | ❌ No |

---

## Related Documents
- `DOCTOR_IDENTITY_RESOLUTION.md`
- `USER_REGISTRATION_FLOWS/DOCTOR_REGISTRATION_FLOW.md`
- `DOCTOR_INVITATION_RULES.md`
