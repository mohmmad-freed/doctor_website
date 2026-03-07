# Patient Registration Flow

## Actor
Patient

## Purpose
This document defines the complete self-registration flow for a Patient account, starting from the Create New Account page.

The Patient is allowed to register independently without invitation.

---

## Entry Point

The flow starts when the user opens the registration page and selects:

- Patient Account

---

## Registration Overview

The patient registration flow consists of the following stages:

1. Select Patient Account
2. Enter phone number
3. Validate and standardize phone number
4. Send OTP
5. Verify OTP
6. Enter personal information
7. Validate registration data
8. Create account
9. Optional email linking
10. Optional email verification

---

## Step 1 — Select Patient Account

The user clicks:

- Patient Account

The system starts the Patient registration flow.

---

## Step 2 — Enter Phone Number

The user enters:

- phone number

Then clicks:

- Send Verification Code

---

## Step 3 — Validate and Standardize Phone Number

Before sending OTP, the system validates the entered phone number.

The system must:

- validate phone number format (must start with `059` or `056` and be exactly 10 digits long)
- standardize the phone number into the system format
- check whether the number is already linked to an existing account

### Phone Standardization Rules

Examples:

- `+97256XXXXXXX` → `056XXXXXXX`
- any other accepted Palestinian mobile format should be normalized into one unified local format

The system must always store and compare phone numbers in one standardized format.

### Validation Outcomes

#### If the phone number format is invalid:
- show validation error message
- do not continue

#### If the phone number is already linked to an existing account:
- show message that this phone number is already registered
- redirect or guide the user to login instead of registration

#### If the phone number is valid and not linked to any existing account:
- continue to OTP sending

---

## Step 4 — Send OTP Verification Code

The system sends an OTP code by SMS to the standardized phone number.

### OTP Rules

- OTP validity duration: 5 minutes
- resend is allowed once every 1 minute
- maximum combined OTP requests allowed per day is 3
- only the latest valid OTP should be accepted
- OTP is used only to verify phone ownership, not to create the account

### Resend Rules

If the user requests resend before 1 minute passes:
- show message telling the user to wait until resend becomes available

If the daily maximum limit of 3 OTP requests is reached:
- block the request and show a limit reached message

If resend is allowed:
- send a new OTP
- invalidate the previous OTP if the system uses latest-code-only logic

---

## Step 5 — Verify OTP

The user enters:

- verification code

Then clicks:

- Continue

The system validates the OTP.

### Verification Outcomes

#### If OTP is incorrect:
- log the failed attempt
- if the user reaches 3 failed attempts, invalidate the OTP completely and require a new code
- otherwise, show error message with the remaining allowed attempts
- allow retry while the OTP is still valid and failed attempts < 3

#### If OTP is expired:
- show expiration message
- require sending a new OTP

#### If OTP is correct:
- mark the registration session as phone verified
- redirect the user to the personal information form

---

## Step 6 — Enter Personal Information

After successful OTP verification, the user is redirected to the personal information form.

In this screen:

- the verified phone number is shown as fixed
- the phone number cannot be changed directly in this step

The user enters the following data:

### Required Fields
- full name
- national ID
- password
- confirm password

### Optional Fields
- city

Then the user clicks:

- Create Account

---

## Step 7 — Validate Registration Data

Before account creation, the system validates all submitted data.

### Validation Rules

The system must validate that:

- all required fields are filled
- full name is at least 3 characters long and contains at least one letter
- password and confirm password match
- password satisfies the system password policy (minimum 8 characters)
- national ID format is valid (exactly 9 digits)
- national ID is not already used by another Patient
- the verified registration session is still valid
- the phone number is still not linked to any existing account

### Validation Outcomes

#### If a required field is missing:
- show a clear validation message for the missing field

#### If password and confirm password do not match:
- show validation error

#### If password does not meet password policy:
- show validation error

#### If national ID format is invalid:
- show validation error

#### If national ID already exists for another Patient:
- show message that this ID is already registered for a patient

#### If the phone number became registered by another completed session before this submit:
- stop registration
- show message that this phone number is already registered
- direct the user to login

---

## Step 8 — Create Account

If all validations pass, the system creates the account.

### Data Created

The system creates:

#### User
- phone number
- password
- primary authentication identity

#### Patient Profile
- full name
- national ID
- city

### Account Creation Rules

- account creation happens only at this final step
- the system automatically logs the patient in immediately after the account is created
- OTP verification alone does not create the account
- OTP verification only proves ownership of the phone number
- the phone number must remain available at the exact moment of account creation
- account creation must be protected against duplicate creation requests

### Technical Protection Requirements

To guarantee that only one account is created:

- standardized phone number must be unique at the database level for the main account identity
- account creation must be executed inside an atomic transaction
- duplicate creation attempts must fail gracefully

---

## Concurrent Registration Attempts

A patient may start registration for the same phone number from more than one device or browser session at the same time.

Example:
- device A enters the phone number
- device B enters the same phone number
- both receive OTP
- both verify OTP successfully
- both continue filling the registration form

This is allowed at the registration-attempt level.

However, the final account must be created only once.

### Rule

OTP verification does not reserve the phone number permanently.

The phone number is only finally reserved when account creation succeeds.

### What Must Happen

If two sessions reach the final Create Account step for the same phone number:

- the first successful account creation wins
- any later attempt must fail gracefully

### Result for the Later Attempt

If another session already created the account first:

- do not create another account
- show message that this phone number is already registered
- redirect or guide the user to login

### Important Principle

The system may allow:
- multiple registration attempts
- multiple OTP submissions
- multiple completed registration forms

But the system must never allow:
- two accounts to be created for the same phone number

---

## Step 9 — Optional Email Linking

After successful account creation, the system gives the patient two options:

- Add Email Address
- Skip

### If the user chooses Skip:
- finish registration successfully
- allow the user to continue into the system

### If the user chooses Add Email Address:
- the user enters an email address
- the system validates the email format
- the system ensures the email is not already used (case-insensitive checking)
- the system sends an email verification link
- the email address is NOT saved to the user profile at this time; it is held in a pending state until verified

---

## Step 10 — Email Verification

If the patient adds an email address, the system sends an email verification link.

### Email Verification Rules

- the verification link must have an expiration time
- the email must only be verified for the same account that requested it
- email verification must not be completed under a different logged-in account

### Verification Outcomes

#### Case 1 — Correct logged-in account and valid link
If the user opens the verification link while logged into the same account, and the link is still valid:
- verify the email
- show success message

#### Case 2 — Logged into a different account
If the user opens the verification link while logged into another account:
- show message that this verification link belongs to a different account

#### Case 3 — Not logged in
If the user opens the verification link while not logged in:
- show message asking the user to log in first

#### Case 4 — Expired link
If the verification link is expired:
- show expiration message
- allow sending a new verification email if supported by the system

---

## Final Result

If all required steps are completed successfully:

- the Patient account is created
- the Patient can log into the system
- the Patient can manage their profile
- the Patient can optionally verify email
- the Patient can use patient features such as appointment booking and appointment management

---

## Business Rules

- Patient registration starts from the Create New Account page
- the user must explicitly select Patient Account
- Patient can register independently without invitation
- phone number must be validated before OTP sending
- phone number must be standardized before storage and comparison
- phone number must not already belong to an existing account at the moment of final account creation
- OTP is required before proceeding to personal data entry
- OTP validity duration is 5 minutes
- OTP resend is allowed every 1 minute
- OTP requests are strictly limited to 3 per day
- OTP entry is locked after 3 failed attempts
- OTP verification does not create the account
- account creation happens only at the final Create Account step
- automatically log in the user upon account creation
- national ID must be unique among Patient accounts
- city is optional
- email is optional and conditionally saved only after verification
- email uniqueness is evaluated case-insensitively
- email verification must only succeed for the same logged-in account that requested it
- duplicate account creation for the same phone number must never happen
- if concurrent registration attempts happen, only the first completed account creation succeeds

---

## Required Data

- phone number
- full name
- national ID
- password
- confirm password

---

## Optional Data

- city
- email

---

## Error Cases

The system must handle at least the following cases:

- invalid phone number format
- already registered phone number
- incorrect OTP
- maximum failed OTP attempts reached
- expired OTP
- resend requested too early
- daily maximum OTP limit reached
- missing required fields
- password mismatch
- weak or invalid password according to policy
- invalid national ID format
- duplicate national ID for Patient
- registration session no longer valid
- phone number became registered during concurrent registration attempt
- expired email verification link
- email verification opened while logged into another account
- email verification opened without login

---

## Postconditions

If registration succeeds:

- a main account identity exists for the phone number
- a Patient profile exists and is linked correctly
- the patient can authenticate using the registered phone number and password
- email remains optional unless the business rules change in the future