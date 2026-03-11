# DOCTOR_INVITATION_LIMITS.md

## Purpose

This document defines the quotas, rate limits, and throttling rules governing doctor invitations in the system.

The objectives of these limits are:

- prevent abuse of the invitation system
- prevent SMS/email spam
- protect doctors from repeated invitations
- control system load
- ensure fair usage of messaging services
- enforce plan-based operational limits

This document complements the rules defined in:

DOCTOR_INVITATION_RULES.md

---

# Core Principle

Doctor invitation limits are **derived from the clinic subscription plan capacity**.

The system must balance:

- business flexibility
- fair usage
- anti-spam protection
- messaging cost control

Limits are therefore calculated dynamically using the clinic plan variable:

max_doctors_allowed

However, system-wide safety caps are applied to prevent excessive activity.

---

# Plan Capacity Variable

Most limits depend on the following value:

max_doctors_allowed

This value represents the **maximum number of doctors allowed in the clinic according to its subscription plan**.

Examples:

Small clinic plan → 2 doctors  
Medium clinic plan → 10 doctors  
Enterprise clinic plan → 100 doctors

---

# Daily Invitation Limit

The number of invitations a clinic can send per day is derived from its plan capacity.

Formula:

daily_invitation_limit = min(max_doctors_allowed + 2, daily_invitation_cap)

System cap:

daily_invitation_cap = 25

This ensures clinics can invite slightly more doctors than their plan capacity while preventing large bursts.

### Examples

| Plan Doctors | Daily Invitation Limit |
|--------------|----------------------|
| 2 | 4 |
| 5 | 7 |
| 10 | 12 |
| 20 | 22 |
| 50 | 25 (capped) |
| 100 | 25 (capped) |

---

# SMS Invitation Limit Per Hour

Because invitations may trigger SMS notifications, SMS sending must be limited.

Formula:

sms_invitation_limit_per_hour = min(4 + round(max_doctors_allowed / 3), sms_hourly_cap)

System cap:

sms_hourly_cap = 12

### Examples

| Plan Doctors | SMS Per Hour |
|--------------|--------------|
| 2 | 5 |
| 6 | 6 |
| 10 | 7 |
| 20 | 11 |
| 30 | 12 (capped) |
| 100 | 12 (capped) |

### Rationale

- scales with clinic size
- protects SMS providers
- prevents mass notification abuse

---

# Pending Invitation Limit

For each combination of:

- clinic
- doctor identity

there may be **only one active PENDING invitation**.

If a PENDING invitation already exists:

- the system must reject creating a new invitation.

Instead, the system may allow **resend / re-notify** of the existing invitation.

---

# Resend Cooldown

Resending invitations must not occur too frequently.

Minimum cooldown between resend attempts:

30 minutes

If a resend attempt occurs before the cooldown expires:

the system must reject the resend.

---

# Maximum Resends

Each invitation may be resent only a limited number of times.

Maximum resend attempts per invitation:

3 resends

After reaching this limit:

- no additional resends are allowed
- a new invitation may only be created after the invitation expires

---

# Same Doctor Re-Invitation Window

To prevent harassment or repeated invitations to the same doctor, a time window limit is applied.

Rule:

maximum 3 invitations per doctor per clinic within 30 days

This includes invitations that were:

- expired
- rejected
- cancelled

This rule prevents repeated invitation attempts targeting the same doctor.

---

# Identity-Based Limit Enforcement

Invitation limits must be enforced using the doctor's identity rather than only invitation records.

Identity may include:

- phone number
- email address
- national ID (if supported)

This prevents bypassing limits by slightly modifying invitation input data.

---

# SMS Provider Protection

The system must protect external SMS services from excessive sending.

Safeguards include:

- hourly SMS limits
- resend cooldown
- identity-based throttling
- daily invitation limits

If SMS limits are exceeded:

- the invitation may still exist in the system
- but SMS delivery must be delayed or blocked temporarily

---

# Abuse Detection

The system should detect suspicious invitation behavior such as:

- sending invitations to many different doctors rapidly
- repeated invitations after rejection
- rapid resend attempts
- automated invitation patterns

Possible system responses include:

- temporary invitation blocking
- stricter rate limiting
- logging suspicious activity
- alerting administrators

---

# Clinic Suspension Behavior

If a clinic is detected abusing the invitation system, the platform may temporarily suspend invitation capabilities.

During suspension:

- new invitations are blocked
- resend operations are disabled

---

# Error Handling

When invitation limits are exceeded, the system should return clear error messages.

Examples:

"Daily invitation limit reached."

"This doctor has already been invited recently."

"Please wait before resending this invitation."

"Maximum resend attempts reached."

---

# Logging and Monitoring

The system should log:

- invitation creation attempts
- resend attempts
- limit violations
- identity-based throttling triggers

This data can be used for:

- system monitoring
- security auditing
- abuse detection
- analytics

---

# Future Extensions

Possible future improvements include:

- plan-specific limit tuning
- dynamic limits based on clinic activity
- SMS cost optimization
- automated abuse detection systems
- invitation analytics dashboards