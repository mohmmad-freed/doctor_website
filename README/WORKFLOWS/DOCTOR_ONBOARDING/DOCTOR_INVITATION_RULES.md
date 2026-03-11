# DOCTOR_INVITATION_RULES.md

## Purpose

This document defines the business rules governing doctor invitations within the clinic platform.

It establishes the authoritative rules that control how doctor invitations behave, including:

- who is allowed to send invitations
- when invitations are allowed or rejected
- invitation lifecycle states
- expiration policies
- duplicate invitation prevention
- multi-clinic doctor relationships

This document focuses strictly on **business rules**, not UI implementation.

---

# Scope

These rules apply to all doctor invitation actions including:

- inviting a doctor who already exists in the platform
- inviting a doctor who does not yet have an account
- resending invitations
- cancelling invitations
- determining invitation eligibility
- determining invitation lifecycle states

This document does NOT define:

- invitation rate limits
- quotas
- SMS throttling
- input formatting
- input validation formats

Those are documented separately in:

- `DOCTOR_INVITATION_LIMITS.md`
- `DOCTOR_INPUT_STANDARDIZATION.md`
- `DOCTOR_INPUT_VALIDATION.md`

---

# Core Principle

A doctor invitation represents a **controlled request from a clinic to allow a doctor to join that clinic**.

An invitation must only be created when:

1. The inviter has permission.
2. The clinic is active.
3. The doctor identity is valid.
4. The doctor is not already active in the clinic.
5. No conflicting pending invitation exists.

---

# Authorized Inviter

Doctor invitations can only be created by the **Main Doctor of the clinic**.

In the system this role is defined as:

`MAIN_DOCTOR`

The `MAIN_DOCTOR` represents the clinic owner and holds administrative control over the clinic.

### Requirements

The inviter must satisfy all of the following:

- role = `MAIN_DOCTOR`
- belongs to the clinic
- account is active
- account is not suspended or blocked

No other role in the system is allowed to send doctor invitations.

---

# Disallowed Inviters

The following roles must NOT send doctor invitations:

- receptionists
- patients
- regular doctors
- clinic staff without the `MAIN_DOCTOR` role
- users outside the clinic
- suspended users

If a non-authorized user attempts to invite a doctor, the system must reject the request.

---

# Doctor Identity

A doctor invitation must target a doctor using reliable identifiers.

Supported identifiers may include:

- phone number
- email address
- national ID (if used)

The system must **never rely on the doctor's name alone** for identity matching.

---

# Multi-Clinic Membership

Doctors are allowed to belong to multiple clinics simultaneously.

This reflects real-world situations where doctors work across multiple clinics.

### Rule

Being associated with another clinic must NOT prevent a doctor from being invited to a new clinic.

A doctor may:

- accept invitations from multiple clinics
- work across multiple clinics
- maintain separate schedules for each clinic

### Conflict Prevention

Although multi-clinic membership is allowed, the appointment system must prevent scheduling conflicts.

The system must prevent:

- overlapping appointments across clinics
- double-booking a doctor at the same time

This rule is enforced by the **appointment scheduling system**, not the invitation system.

---

# When Invitation Is Allowed

An invitation may be created only if all conditions are satisfied:

- inviter has permission
- clinic is active
- doctor identity is valid
- doctor is not already active in the clinic
- no existing pending invitation exists
- invitation limits are not exceeded

---

# When Invitation Must Be Rejected

The system must reject invitation creation if any of the following conditions occur.

### Permission Issues

- inviter lacks permission
- inviter does not belong to the clinic
- clinic is inactive or suspended

### Existing Relationship Conflict

- doctor is already active in the clinic

### Pending Invitation Conflict

- another `PENDING` invitation already exists for the same doctor and clinic

### Identity Conflict

- phone/email/ID data is invalid
- identity information conflicts with an existing doctor record

### Limit Violations

- invitation limits are exceeded

---

# Pending Invitation Rule

For a given combination of:

- clinic
- doctor identity
- role

there must be **only one active `PENDING` invitation**.

### If Pending Exists

If a pending invitation already exists:

- the system must reject creating a new invitation.

However, the system may allow:

**resend / re-notify of the same invitation**.

This must reuse the existing invitation record.

No new invitation record should be created.

Resend operations must follow rules defined in:

`DOCTOR_INVITATION_LIMITS.md`.

---

# Already Active Doctor Rule

A clinic must not send an invitation to a doctor who is already an active member of that clinic.

If the doctor is already associated with the clinic:

- invitation creation must be rejected.

The system should inform the inviter that the doctor already belongs to the clinic.

---

# Invitation Lifecycle States

Doctor invitations move through defined lifecycle states.

Minimum required states include:

- `PENDING`
- `ACCEPTED`
- `REJECTED`
- `CANCELLED`
- `EXPIRED`

### State Definitions

**PENDING**

The invitation has been created and is awaiting a response.

**ACCEPTED**

The doctor accepted the invitation and the clinic relationship was established.

**REJECTED**

The doctor rejected the invitation.

**CANCELLED**

The clinic cancelled the invitation before it was accepted.

**EXPIRED**

The invitation expired without a response.

---

# Invitation Expiration Policy

The expiration period depends on whether the doctor already exists in the system.

### New Doctor (Not Registered)

Invitation expires after:

**2 days**

Reason:

- onboarding invitations should not remain active for long
- prevents stale invitations

---

### Existing Doctor

Invitation expires after:

**7 days**

Reason:

- existing doctors may review invitations later
- doctors may already be active in other clinics

---

### Expiration Behavior

When an invitation expires:

- status becomes `EXPIRED`
- the invitation cannot be accepted
- invitation links or tokens become invalid
- a new invitation may be created later

---

# Cancellation Rules

An invitation may be cancelled only when it is still pending.

Allowed only if:

status = `PENDING`

Cancellation must be rejected if status is:

- ACCEPTED
- REJECTED
- CANCELLED
- EXPIRED

After cancellation:

- the invitation becomes inactive
- invitation links must stop working
- the clinic may send a new invitation later

---

# Resend Rules

Resending invitations is allowed but controlled.

Resend may be allowed when:

- invitation status is `PENDING`
- resend cooldown rules are satisfied
- rate limits are not exceeded

Resend must NOT create a new invitation record.

Instead the system must:

- reuse the existing invitation
- send a new notification (SMS or email)

---

# Identity Conflict Handling

If identity data conflicts with existing records:

the system must reject the invitation.

Example:

- phone number belongs to one doctor
- email belongs to another

In such cases the system must fail safely and request corrected information.

---

# Doctor Verification Requirement

Accepting a doctor invitation does not automatically mean the doctor is verified.

Doctors must complete credential verification before they are considered verified in the system.

This process may include uploading required documents such as:

- medical license
- identity documents

Verification rules are documented in:

`DOCTOR_CREDENTIAL_VERIFICATION.md`

---

# Auditability

The system should track invitation actions including:

- who created the invitation
- which clinic sent it
- which doctor identity was targeted
- invitation creation time
- resend events
- cancellation events
- acceptance or rejection

---

# Related Documents

- `DOCTOR_INVITATION_LIMITS.md`
- `DOCTOR_INPUT_STANDARDIZATION.md`
- `DOCTOR_INPUT_VALIDATION.md`
- `EXISTING_DOCTOR_INVITATION_FLOW.md`
- `DOCTOR_CREDENTIAL_VERIFICATION.md`

---

# Future Extensions

Possible future improvements include:

- configurable invitation expiration by plan
- invitation reminder notifications
- invitation analytics
- stronger doctor identity matching
- abuse detection mechanisms