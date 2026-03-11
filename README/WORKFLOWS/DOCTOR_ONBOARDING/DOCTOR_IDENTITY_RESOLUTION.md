# DOCTOR_IDENTITY_RESOLUTION.md

## Purpose

This document defines how the system determines whether a doctor already exists in the platform.

Correct identity resolution is critical to prevent:

- duplicate doctor accounts
- incorrect invitation matching
- data inconsistency across clinics

This document defines the **primary identity key** used for doctor matching and the additional fields required during invitation.

---

# Core Identity Principle

The **primary identity key for doctors is the phone number**.

The system uses the doctor's phone number as the main identifier when determining whether a doctor already exists in the platform.

This means:

- each doctor account is uniquely associated with a phone number
- phone number is the main reference for identity detection
- invitations are matched using the phone number

---

# Identity Fields Used in the System

The system uses the following identity-related fields:

| Field | Role |
|------|------|
| Phone Number | Primary identity key |
| Email Address | Required contact information |
| National ID | Used for verification |

---

# Phone Number (Primary Identity Key)

The phone number is the **main identifier for doctors in the system**.

This means:

- the system checks the phone number first
- if a doctor with the same phone number exists, the system treats them as the same doctor
- the system must prevent duplicate doctor accounts with the same phone number

Phone numbers must always be **standardized before comparison**.

Standardization rules are defined in:

DOCTOR_INPUT_STANDARDIZATION.md

---

# Email Address

The email address is **required during doctor invitation**.

However, email is **not used as the primary identity key**.

Its role includes:

- communication with the doctor
- sending invitation emails
- account notifications

The system must still validate that the email address is correctly formatted.

Validation rules are defined in:

DOCTOR_INPUT_VALIDATION.md

---

## Email Handling During Invitation

The email provided by the clinic owner during invitation is treated as a **delivery destination** for the invitation notification, NOT as a strict identity matching key.

### Core Rules:

1. **Phone Exists — Email Provided Matches Stored Email:**
   Standard case. The invitation proceeds normally to the existing user.

2. **Phone Exists — Email Provided Differs from Stored Email:**
   The invitation is **NOT blocked**. The system must deliver the invitation to the **stored email** on the doctor's existing account (the one the system trusts), NOT the email provided by the clinic owner. The clinic owner receives a message: "Invitation sent. The doctor will receive it at their registered email address."

3. **Phone Does Not Exist — Email Belongs to Another Account:**
   If the Phone Number is entirely new (no account exists), but the provided email is already registered to a completely different user, the system must **reject the invitation** with a clear validation error: "This email is already registered to another account. Please verify the information with the doctor."

4. **Phone Exists as Patient — Upgrade to Doctor:**
   If the Phone Number exists as a `PATIENT`, the invitation proceeds. Upon accepting, the system attaches the `DOCTOR` role to their existing `PATIENT` account. No new account is created.

---

# National ID

The national ID is used as part of **doctor verification and credential validation**.

It is not used as the primary identity key during invitation.

Instead, it is used later during the **doctor verification process**.

This helps ensure that:

- the doctor is a legitimate medical professional
- credentials belong to the correct person

Verification rules are defined in:

DOCTOR_CREDENTIAL_VERIFICATION.md

---

# Identity Detection Flow

When a clinic invites a doctor, the system performs the following checks:

1. Standardize the phone number.
2. Search for an existing doctor account with the same phone number.
3. **If a match is found:**
   - The doctor is treated as an existing doctor.
   - The system follows `EXISTING_DOCTOR_INVITATION_FLOW.md`.
4. **If no match is found, check for a Pending Identity Lock:**
   - **If a Pending Identity Lock exists for this phone:** Another clinic has already initiated onboarding. The system creates the invitation linked to the pending identity. When the doctor completes onboarding, they will see all pending invitations.
   - **If no Pending Identity Lock exists:** The system creates the lock atomically and initiates the NEW_DOCTOR onboarding flow.

---

# Duplicate Prevention

The system must prevent creating multiple doctor accounts with the same phone number.

This ensures:

- consistent doctor identity across clinics
- reliable invitation matching
- simplified account management

---

# Identity Creation Lock (Concurrency Protection)

To prevent race conditions when multiple clinics invite the same unregistered phone number simultaneously, the system must implement an **Identity Creation Lock**.

### Problem
If two clinics invite the same new phone number at the exact same time, both may trigger the NEW_DOCTOR onboarding flow. When the first doctor completes registration, the second flow will fail because the account already exists.

### Solution: Atomic Pending Identity Lock

When the system creates a `NEW_DOCTOR` invitation for a phone number that does not yet exist:

1. The system must atomically create a **Pending Identity Lock** record for that standardized phone number.
2. This lock indicates that a new doctor onboarding is in progress for this phone number.
3. The lock is automatically released when:
   - The doctor completes onboarding (account is created), OR
   - All associated `PENDING` invitations for that phone are resolved (expired, rejected, or cancelled).

### Behavior While Lock Is Active

If another clinic invites the same phone number while the Pending Identity Lock exists:

- The system must **NOT** initiate a second NEW_DOCTOR flow.
- Instead, the system must create the invitation linked to the **pending identity**.
- Once the doctor completes onboarding via any clinic's invitation link, the system converts all other pending invitations for that phone into EXISTING_DOCTOR invitations.
- The newly created doctor is then notified of the remaining pending invitations via email and in-app notifications.

### Implementation Guidance

- The lock should be implemented as a database-level record (e.g., a `PendingDoctorIdentity` table) with a `UNIQUE` constraint on the standardized phone number. This provides atomicity via the database engine.
- Do NOT use Redis or cache for this lock, as it must survive server restarts and cache evictions.

---

# Why Phone Is the Primary Key

Phone numbers are used as the primary identity key because:

- they are unique per user in most cases
- they are commonly used for authentication
- they are easier to validate than names
- they reduce the risk of duplicate accounts

Doctor names are **not used for identity matching** because:

- names may contain spelling variations
- names may be entered incorrectly
- different doctors may share the same name

---

# Relationship With Invitation Flow

Identity resolution directly affects the invitation process.

If the phone number belongs to an existing doctor:

the system follows:

EXISTING_DOCTOR_INVITATION_FLOW.md

If the phone number does not exist in the system:

the system initiates the **new doctor onboarding process**.

---

# Architectural Separation: Platform Identity vs. Clinic Membership

A critical architectural principle in the system is the distinct separation between a doctor's platform-level identity and their clinic-level relationships.

### A) Platform Doctor Identity
This represents the doctor as a user of the entire platform.
- **Attributes:** Phone number, email address, national ID, platform suspension status, and global credential verification status.
- **Scope:** Exists independently of any specific clinic.

### B) Clinic Doctor Membership
This represents the relationship between a doctor and a specific clinic.
- **Attributes:** `clinic_id`, `doctor_id`, invitation status, membership status, `invited_by`, `joined_at`, `revoked_at`, and clinic-specific permissions.
- **Scope:** Strictly bound to the clinic.

**Important Rule:** A doctor's status, permissions, or rejection inside one clinic must **never** affect their relationship or status with other clinics.

---

# Related Documents

DOCTOR_INPUT_STANDARDIZATION.md  
DOCTOR_INPUT_VALIDATION.md  
DOCTOR_INVITATION_RULES.md  
EXISTING_DOCTOR_INVITATION_FLOW.md  
DOCTOR_CREDENTIAL_VERIFICATION.md