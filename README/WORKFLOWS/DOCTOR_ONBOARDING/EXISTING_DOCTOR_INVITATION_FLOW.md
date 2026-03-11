# EXISTING_DOCTOR_INVITATION_FLOW.md

## Purpose

This document describes the workflow used when a clinic invites a doctor who **already has an account in the platform**.

This flow differs from the new doctor onboarding flow because:

- the doctor already exists in the system
- no new account should be created
- the invitation must connect the existing doctor account to the inviting clinic

This document focuses on the **workflow and decision logic**, not UI implementation details.

---

# Scope

This workflow applies when:

- a clinic sends an invitation
- the doctor identity (phone/email) matches an existing doctor account in the system

This document explains:

- how the system detects an existing doctor
- how invitations are created
- how acceptance works
- how the doctor becomes linked to the clinic
- what happens in edge cases

---

# Core Principle

If a doctor already exists in the platform, the system must **never create a duplicate doctor account**.

Instead, the invitation must:

- target the existing doctor record
- allow the doctor to accept or reject the invitation
- create a clinic relationship upon acceptance

---

# Step 1 — Invitation Creation

The process begins when the **MAIN_DOCTOR** sends a doctor invitation.

Required input includes:

- doctor phone number
- doctor email (**required**)
- doctor name (**used for display only**)
- at least **one specialty** selected from the clinic's available specialties

### Important Notes

- **Phone number and email** are the primary identity attributes used to detect existing doctors.
- **Doctor name is not used for identity matching** and may contain mistakes without affecting the system logic.
- The inviting `MAIN_DOCTOR` must select **at least one specialty** relevant to the clinic.
- The system must reject the invitation if no specialty is selected.

Before creating the invitation, the system must perform validation checks.

---

# Step 2 — Doctor Identity Detection

The system attempts to determine whether the invited doctor already exists.

Matching may be performed using:

- phone number
- email address

### Possible outcomes

1. The doctor already exists in the system.
2. The doctor does not exist.

If the doctor does not exist, the system must follow the **new doctor onboarding flow**.

If the doctor exists, the system continues with this workflow.

---

# Step 3 — Existing Doctor Validation

Before creating the invitation, the system must verify that:

- the doctor is not already active in the clinic
- there is no active `PENDING` invitation for this doctor and clinic
- invitation limits are not exceeded

If any of these checks fail, the invitation must be rejected.

---

# Step 4 — Invitation Record Creation

If validation succeeds, the system creates a doctor invitation record.

The record typically includes:

- `clinic_id`
- `doctor_id`
- `inviter_user_id`
- `invitation_status = PENDING`
- invitation expiration timestamp
- creation timestamp

Because the doctor already exists in the system:

**Invitation expiration = 7 days**

This rule is defined in:

`DOCTOR_INVITATION_RULES.md`

---

# Step 5 — Notification Delivery

After the invitation is created, the system sends a notification to the doctor.

Notification channels include:

- email notification
- in-app notification within the platform

### Notification Content

The notification typically includes:

- clinic name
- invitation message
- option to **Accept**
- option to **Reject**

Acceptance and rejection may occur directly within the platform interface (for example through the notifications page or invitation page).

External action links are not required.

All notifications must respect the limits defined in:

`DOCTOR_INVITATION_LIMITS.md`
---

# Step 6 — Doctor Receives Invitation

Once the notification is delivered, the doctor may:

- accept the invitation
- reject the invitation
- ignore the invitation until expiration

The invitation remains valid until one of the following occurs:

- accepted
- rejected
- cancelled
- expired

---

# Step 7 — Doctor Accepts Invitation

If the doctor accepts the invitation, the system must perform final validation checks.

The system verifies that:

- invitation status is still `PENDING`
- the invitation has not expired
- the invitation was not cancelled
- the doctor is still eligible to join the clinic

If validation succeeds, the system proceeds to create the clinic relationship.

---

# Step 8 — Clinic Association

After successful acceptance:

- a doctor-clinic relationship record is created
- the doctor becomes an active member of the clinic

This association may include:

- assigned specialty within the clinic
- scheduling permissions
- clinic visibility in the doctor's dashboard

The invitation status is updated to:

`ACCEPTED`

---

# Step 9 — Doctor Verification Requirement

Accepting a clinic invitation **does not automatically mean the doctor is verified**.

If the doctor is not verified, the system may require the doctor to upload verification documents such as:

- medical license
- identity documents
- additional professional information

After the documents are submitted:

the review is performed by **platform administrators (system owners)**, not by the `MAIN_DOCTOR`.

Based on the review outcome:

- the doctor may be approved
- the verification may be rejected
- additional documents may be requested

Verification rules are defined in:

`DOCTOR_CREDENTIAL_VERIFICATION.md`

---

# Step 10 — Invitation Rejection

If the doctor rejects the invitation:

- invitation status becomes `REJECTED`
- no clinic relationship is created

The clinic may send a new invitation later, subject to:

`DOCTOR_INVITATION_LIMITS.md`

---

# Step 11 — Invitation Expiration

If the doctor does not respond before the expiration period:

the invitation status becomes:

`EXPIRED`

For existing doctors:

**Expiration period = 7 days**

After expiration:

- the invitation cannot be accepted
- invitation tokens or actions become invalid
- the clinic may create a new invitation later

---

# Step 12 — Invitation Cancellation

The clinic may cancel the invitation while it is still pending.

Allowed only when:

`invitation_status = PENDING`

After cancellation:

- status becomes `CANCELLED`
- invitation actions become invalid

---

# Edge Cases

### Doctor Already Active in Clinic

If the doctor already belongs to the clinic:

- invitation creation must be rejected.

---

### Duplicate Pending Invitation

If a pending invitation already exists for the same doctor and clinic:

- creating a new invitation must be rejected
- resend may be allowed according to limits.

---

### Identity Conflict

If identity data conflicts with existing records, the system must reject the invitation.

Example:

- phone number matches one doctor
- email matches another doctor

The system must fail safely.

---

# Multi-Clinic Behavior

Doctors may belong to multiple clinics simultaneously.

Accepting a new invitation **must not remove existing clinic memberships**.

However, the scheduling system must prevent:

- overlapping appointments across clinics
- double booking of the same doctor

---

# Logging and Audit

The system should log:

- invitation creation
- resend attempts
- acceptance
- rejection
- cancellation
- expiration

Logs should include:

- clinic_id
- doctor_id
- inviter_user_id
- timestamps

---

# Related Documents

DOCTOR_INVITATION_RULES.md  
DOCTOR_INVITATION_LIMITS.md  
DOCTOR_INPUT_STANDARDIZATION.md  
DOCTOR_INPUT_VALIDATION.md  
DOCTOR_CREDENTIAL_VERIFICATION.md
