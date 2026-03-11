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

1. standardize the phone number
2. search for an existing doctor account with the same phone number
3. if a match is found:
   - the doctor is treated as an existing doctor
   - the system follows the existing doctor invitation flow
4. if no match is found:
   - a new doctor onboarding process is initiated

---

# Duplicate Prevention

The system must prevent creating multiple doctor accounts with the same phone number.

This ensures:

- consistent doctor identity across clinics
- reliable invitation matching
- simplified account management

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

# Related Documents

DOCTOR_INPUT_STANDARDIZATION.md  
DOCTOR_INPUT_VALIDATION.md  
DOCTOR_INVITATION_RULES.md  
EXISTING_DOCTOR_INVITATION_FLOW.md  
DOCTOR_CREDENTIAL_VERIFICATION.md