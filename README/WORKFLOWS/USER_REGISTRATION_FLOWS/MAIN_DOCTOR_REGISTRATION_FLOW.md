# Main Doctor Registration Flow

## Actor
Main Doctor (Clinic Owner)

## Purpose
This document defines the complete registration flow for a Main Doctor (Clinic Owner) account, starting from the Clinic Registration entry point.

The Main Doctor is not allowed to register independently without a valid activation code provided by the administration.

---

## Entry Point

The flow starts when the user opens the registration page and selects:

- Clinic Owner Account

The system starts the 3-Stage Clinic Owner Registration flow.

---

## Registration Overview

The main doctor registration flow consists of the following stages:

1. Enter activation code and identity details
2. Enter personal information (conditional based on user state)
3. Enter clinic information
4. Verify phone number via SMS OTP
5. Verify email address via Email OTP
6. Finalize Main Doctor registration

---

## Step 1 — Enter Activation Code and Identity

The user enters:

- activation code
- phone number
- national ID

Then clicks:

- Continue

The system validates the submitted data.

### Validation Rules

The system must validate that:

- phone number format is valid
- phone number is standardized into the system format
- national ID is exactly 9 digits
- activation code is valid, not used, and not expired
- if the activation code is assigned to a specific phone or national ID, the entered values must match
- if the national ID is already linked to a different phone number in the system, the provided data is rejected (without revealing why to prevent data leakage)

### Validation Outcomes

#### If validation fails:
- show validation error message
- do not continue

#### If validation passes:
- save data to the registration session
- check if the standardized phone number already belongs to an existing user

### Stage 2 Routing Rules

#### If the user does not exist:
- direct user to the New User Personal Information form

#### If the user exists but has no email:
- direct user to the Existing User Email form

#### If the user exists and already has an email:
- mark Stage 2 as done automatically
- skip to the Clinic Information form (Step 3)

---

## Concurrent Registration Protection

After Step 1 succeeds, the system must check whether there is already an active registration attempt associated with the same activation code.

### Rule
Main Doctor registration must allow only **ONE** active registration attempt per activation code at any given time. This concurrency protection is based solely on the activation code, not on browser session, device, or IP address.

Multiple devices or browsers may exist, but only one active registration attempt per activation code is allowed at the system level.

### Protection Logic

#### If an active attempt already exists:
- the system must prevent starting another registration flow using the same activation code
- the user must see a clear message such as: "A registration process is already in progress for this activation code. Please continue from the original session or wait until it expires."

#### If no active attempt exists:
- the system marks the activation code as having an ACTIVE registration attempt
- the user may continue with the registration wizard

### Lock Release Conditions
The ACTIVE registration lock must be released in one of the following cases:
1. Registration completes successfully (activation code becomes USED)
2. The registration session expires due to inactivity
3. The registration flow is cancelled

---

## Step 2 — Enter Personal Information

Depending on the routing rules from Step 1, the user enters personal information.

### Case A: New User

The user enters:
- first name
- last name
- email address
- password
- confirm password

#### Validation Rules (New User)
- first and last names must be at least 2 characters and contain letters
- email must not be registered to another user
- password must meet policy (minimum 8 chars, 1 uppercase, 1 lowercase, 1 number, 1 special character)
- password and confirm password must match

### Case B: Existing User without Email

The user enters:
- email address

#### Validation Rules (Existing User)
- email must not be registered to another user (excluding the user's current record)

After successful entry and validation, the user clicks:

- Continue

The system saves the data to the registration session and redirects to the Clinic Information form.

---

## Step 3 — Enter Clinic Information

The user enters:

### Required Fields
- clinic name
- clinic address
- medical specialties (multiple allowed)

### Optional Fields
- city
- clinic description

Then the user clicks:

- Continue

### Validation Rules

The system must validate that:
- required fields are filled
- selected city exists
- selected specialties exist

### Outcomes

If validation passes:
- save clinic data to the registration session
- request an SMS OTP to the verified phone number
- redirect the user to the Verify Phone Number form

---

## Step 4 — Verify Phone Number

The system sends an OTP code by SMS to the standardized phone number.

The user enters:

- SMS verification code

Then clicks:

- Verify

The system validates the OTP.

### OTP Rules

- OTP validity duration check
- resend is allowed based on cooldown rules
- only the latest valid OTP should be accepted
- OTP is used only to verify phone ownership, not to create the account

### Verification Outcomes

#### If OTP is incorrect or expired:
- show error message
- allow retry or resend

#### If OTP is correct:
- mark the phone number as verified in the registration session
- send an Email OTP to the provided email address
- redirect the user to the Verify Email form

---

## Step 5 — Verify Email and Create Account

The system sends an OTP code by email to the user's email address.

The user enters:

- email verification code

Then clicks:

- Create Account

### Verification Outcomes

#### If OTP is incorrect or expired:
- show error message
- allow retry or resend

#### If OTP is correct:
- the system proceeds to actual account and clinic creation

---

## Step 6 — Finalize Main Doctor Registration

If all validations pass, the system creates the clinic and finalizes the Main Doctor registration atomically.

### Data Created / Updated

The system executes the following within a single transaction:

#### Case A: New User Registration
- create a new user account with phone, national ID, name, email, and password
- assign `PATIENT` and `MAIN_DOCTOR` roles to the new user
- mark the user's email as verified and the user as verified (`email_verified=True`, `is_verified=True`)
- ensure a linked Patient Profile exists for the user

#### Case B: Existing User Upgrade
- do not create a new user account
- update the existing user's record to include the `MAIN_DOCTOR` role (preserving any existing roles like `PATIENT`)
- if the existing user was missing an email, update their email address and mark it as verified (`email_verified=True`)
- ensure a linked Patient Profile exists for the user

#### Activation Code
- lock the activation code row in the database
- mark the activation code as used (`is_used=True`)

#### Clinic
- create a new Clinic record linked to the Main Doctor
- populate name, address, city, specialties, description
- mark the clinic as ACTIVE with both verification channels confirmed

### Registration Finalization Rules

- final registration and clinic creation happens only at this final step
- the entire creation process must be enclosed in an atomic database transaction
- the activation code must be locked using `select_for_update()` to prevent double usage from concurrent requests
- duplicate creation attempts must fail securely

---

## Final Result

If all required steps are completed successfully:

- the registration session is cleared
- the Main Doctor is automatically logged into the system
- the Main Doctor is redirected to the dashboard with a success message
- the Main Doctor can manage their new Clinic

---

## Business Rules

- Main Doctor registration requires an administrative activation code
- a user can hold both PATIENT and MAIN_DOCTOR roles simultaneously
- an existing patient upgrading to a Main Doctor does not need to re-enter their name or choose a new password
- email is required for Main Doctors
- only one active registration attempt per activation code is allowed at any given time
- final registration happens only at the final step, and requires successful verification of both phone and email
- navigation backward is supported during Steps 1, 2, and 3, preserving previously entered valid data

---

## Required Data

- activation code
- phone number
- national ID
- first name (if new user)
- last name (if new user)
- password (if new user)
- confirm password (if new user)
- email address
- clinic name
- clinic address
- medical specialties

---

## Optional Data

- city
- clinic description

---

## Error Cases

The system must handle at least the following cases:

- invalid or expired activation code
- activation code mismatch with assigned phone/national ID
- national ID linked to a different phone number
- activation code already locked by another active registration attempt
- invalid phone number format
- email address already registered to another user
- invalid first or last name format
- password does not meet security policy
- password mismatch
- missing required fields
- incorrect SMS OTP
- expired SMS OTP
- incorrect Email OTP
- expired Email OTP
- concurrent usage attacks on the same activation code

---

## TODO — Pending Implementation Tasks

### Concurrent Registration Protection (Implementation Pending)

The system must enforce that only ONE active Main Doctor registration attempt can exist for a given activation code at any time.

Implementation expectations:
- After Step 1 validation (activation code + phone + national ID), the system must check if an active registration attempt already exists for the same activation code.
- If an active attempt exists, block the new attempt and display a user-friendly message such as: "A registration process is already in progress for this activation code. Please continue from the original session or wait until it expires."
- If no active attempt exists, mark the activation code as having an ACTIVE registration attempt and allow the registration flow to continue.
- The active registration lock must be released when:
  - registration completes successfully (activation code becomes USED)
  - the registration session expires due to inactivity
  - the registration flow is cancelled

**Important note:**
This protection must rely only on the activation code, not on browser session, device, or IP address.

This section is intentionally marked as TODO and should be removed once the implementation is completed and verified.

---

## Postconditions

If registration succeeds:

- the activation code becomes invalid for future use
- a Main Doctor account exists with verified phone and email channels
- a Clinic record exists and is designated as ACTIVE
- the Main Doctor can access the clinic dashboard using their credentials
