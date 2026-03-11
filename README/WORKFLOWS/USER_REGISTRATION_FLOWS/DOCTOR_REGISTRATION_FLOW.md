# Doctor Registration Flow

## Actor
Clinic Owner (Main Doctor), Invited Doctor, Platform Administration

## Purpose
This document defines the intended end-to-end registration and onboarding flow for a new Doctor account. 

**Important Note:** A Doctor cannot create a Doctor account directly from the public registration page. They can only enter the system as a Doctor through an invitation sent by a Clinic Owner. Furthermore, Doctor verification is not automatic; it requires document upload and manual review by platform administration.

---

## Entry Point

The flow starts when the Clinic Owner opens the clinic management area and clicks:

- **Add Doctor to Clinic**

---

## Registration Overview

The doctor registration flow consists of the following key stages:

1. Clinic Capacity Pre-Checks
2. Invitation Limit Checks
3. Invitation Data Entry
4. Final Validation & Identity Resolution
5. Invitation Dispatch
6. Doctor Onboarding (Acceptance & Data Review)
7. Email and Phone Verification
8. Document Upload & Account Creation
9. Unverified State & Admin Review

---

## Step 1 — Clinic Capacity Pre-Checks

Before allowing the Clinic Owner to create a new invitation, the system performs pre-checks to ensure the clinic has not exceeded its plan allowance.

The system evaluates:
- the number of active `PENDING` invitations for this clinic
- **plus** the number of active Doctors already linked to this clinic

### Validation Outcomes

#### If the clinic has reached the limit:
- block the process immediately
- show a message instructing the owner to either:
  1. cancel/stop a pending invitation, or
  2. remove/deactivate an existing active Doctor

#### If capacity allows:
- proceed to Step 2

---

## Step 2 — Invitation Limit Checks

The system performs additional limit checks based on the rules defined in `DOCTOR_INVITATION_LIMITS.md`.

*Intended Workflow Dependencies (Note: These limits may not be fully implemented in code yet but are enforced as system policy):*
- **Daily Limit:** Limit sending based on plan capacity (e.g., `(plan limit * 2) + 2`).
- **Doctor Phone Limits:** 
  - Max 4 SMS allowed to the same Doctor phone number in a week.
  - Max 7 SMS allowed to the same Doctor phone number in a month.

### Validation Outcomes

#### If any limit is exceeded:
- stop the process and display the appropriate limit error

#### If all limits checks pass:
- the Clinic Owner is allowed to proceed to the Doctor invitation form

---

## Step 3 — Invitation Data Entry

The Clinic Owner enters the following details for the invited Doctor:

- **First Name:** (Used for invitation/display purposes only)
- **Phone Number:** (Primary identity key)
- **National ID**
- **Email Address**
- **Desired Clinic Specialties:** (The Clinic Owner selects valid specialties for the doctor)

Then the Clinic Owner clicks:
- **Send**

---

## Step 4 — Final Validation & Identity Resolution

Upon submission, the system performs a final round of strict data processing:

1. **Standardization:** Resolves formatting (e.g., phone numbers) as defined in `DOCTOR_INPUT_STANDARDIZATION.md`.
2. **Validation:** Checks formats, empty fields, and required selections as defined in `DOCTOR_INPUT_VALIDATION.md` and `DOCTOR_INVITATION_LIMITS.md`.

### Validation Outcomes
#### If validation fails:
- stop the process and show appropriate validation or limit errors.

#### If validation succeeds:
- Proceed to Identity Resolution to check if the invited person is already in the system, following the principles in `DOCTOR_IDENTITY_RESOLUTION.md`.

### Identity Resolution Outcomes
#### If the invited person is ALREADY a Doctor:
- **Stop this workflow.**
- Instead, the system must trigger the existing doctor flow as documented in `EXISTING_DOCTOR_INVITATION_FLOW.md`.

#### If the invited person is NOT a Doctor:
- **Continue this workflow.**
- *Note: The person may already have an account as a Patient, a Main Doctor, or both. They are considered "not a Doctor" for this flow as long as they lack the regular DOCTOR role.*

---

## Step 5 — Invitation Dispatch

The system creates the pending invitation and sends an SMS invitation to the Doctor's phone number.

The SMS contains:
- a welcome message
- a secure Doctor onboarding / account creation link
- **Expiration Warning:** The invitation link must expire after a maximum of **2 days**.

---

## Step 6 — Doctor Onboarding (Acceptance & Data Review)

When the invited Doctor opens the secure link within the 2-day validity period, they enter the onboarding portal.

### Intended Resumable Progress
*Intended Workflow Behavior:* The onboarding flow preserves progress at every step. If the Doctor leaves the page and returns before the invitation expires, they will resume from the latest completed step rather than restarting the process.

### Review Information
The Doctor sees the information entered by the Clinic Owner.
- The Doctor **may** edit their allowed personal data if needed (e.g., name, national ID correction).
- The Doctor **must NOT** be allowed to alter the clinic-requested specialties.

The Doctor has two choices:
- **Reject the Invitation:** Marks the invitation as `REJECTED` and the flow stops immediately.
- **Continue:** Proceeds to the verification stage.

---

## Step 7 — Email and Phone Verification

If the Doctor chooses to continue, they must verify their contact details to ensure system security and communication reliability.

1. **Email Verification:** The doctor must verify their email. *(Reference: `EMAIL_VERIFICATION_FLOW.md`)*
2. **Phone Verification:** The doctor must verify their phone number via SMS OTP. *(Reference: `PHONE_VERIFICATION_FLOW.md`)*

---

## Step 8 — Document Upload & Account Creation

After successful email and phone verification, the Doctor proceeds to the Document Upload step.

The Doctor must upload:
- an Identity Document (e.g., ID card or Passport)
- a Medical Practice License / Certificate

### Account Creation & Role Assignment
After successful submission of the required documents, the system updates or creates the account:

- **If the person already has a Patient or Main Doctor account:** the `DOCTOR` role is added/attached to their existing account.
- **If the person does not have Patient context:** the `PATIENT` role and context is automatically created and attached alongside the `DOCTOR` role.
- **If the person has no account at all:** the system creates a new account, ensuring they are granted both the `DOCTOR` and `PATIENT` roles.

---

## Step 9 — Unverified State & Admin Review

After account creation/upgrade, the Doctor enters the **UNVERIFIED Doctor state**.

### Unverified State Constraints
During this state, the Doctor:
- **can:** log into the account
- **can:** view the account dashboard
- **can:** configure schedules and availability for each clinic they are linked to
- **can:** access limited allowed doctor functionality
- **must NOT:** appear publicly to patients
- **must NOT:** be bookable by patients

### Platform Administration Review
Doctor verification is strictly a manual administrative process. Platform admin reviews the uploaded identity and medical documents and makes a decision. *(Reference: `DOCTOR_CREDENTIAL_VERIFICATION.md`)*

#### If Approved:
- the Doctor's status is updated to verified.
- the Doctor becomes visible and bookable by patients according to the clinic's settings and system rules.

#### If Rejected:
- the Doctor remains unverified / rejected.
- the Doctor must remain non-public and non-bookable.
- the Admin may instruct the Doctor to re-upload documents if necessary.

---

## Business Rules & Dependencies

This workflow strictly relies on rules defined in the following supporting documents:
- `DOCTOR_INVITATION_RULES.md`
- `DOCTOR_INVITATION_LIMITS.md`
- `DOCTOR_INPUT_STANDARDIZATION.md`
- `DOCTOR_INPUT_VALIDATION.md`
- `DOCTOR_IDENTITY_RESOLUTION.md`
- `EXISTING_DOCTOR_INVITATION_FLOW.md`
- `DOCTOR_CREDENTIAL_VERIFICATION.md`

## Required Data (Clinic Owner Input)
- Doctor First Name
- Phone Number
- National ID
- Email Address
- Selected Specialties

## Required Data (Doctor Input)
- Corrected personal data (if applicable)
- Email Verification Confirmation
- Phone Verification OTP
- Identity Document (File Upload)
- Medical License (File Upload)

## Postconditions

If registration and admin review succeed:
- The Doctor has an active account containing both `PATIENT` and `DOCTOR` roles.
- The Doctor is linked to the inviting clinic with the accepted specialties.
- The Doctor is fully verified, public-facing, and allows patient bookings.
