# DOCTOR_INPUT_VALIDATION.md

## Purpose

This document defines the validation rules applied to doctor-related inputs during the invitation process.

Validation ensures that the data provided by the clinic is correct, complete, and usable before the system proceeds with invitation creation.

Validation is performed **after input standardization** and before any invitation business logic is executed.

These rules help prevent:

- invalid phone numbers
- malformed email addresses
- missing required fields
- incorrect specialty selection
- incomplete doctor data

This document works together with:

DOCTOR_INPUT_STANDARDIZATION.md

---

# Language Policy

All **user-facing validation messages must be displayed in Arabic**.

The platform primarily targets Arabic-speaking users.

Therefore:

- validation error messages shown in the UI must be Arabic
- internal logs and developer messages may remain in English

---

# Core Principle

All doctor invitation inputs must pass validation before the system:

- attempts identity detection
- applies invitation rules
- creates invitation records
- sends notifications

If validation fails, the invitation process must stop immediately and an appropriate Arabic error message must be returned.

---

# Fields Subject to Validation

The following fields are validated during doctor invitation:

- doctor phone number
- doctor email address
- doctor display name
- selected specialties

---

# Phone Number Validation

## Required

The phone number field is **mandatory**.

An invitation cannot be created without a phone number.

---

## Format Requirements

After standardization, the phone number must:

- contain only digits
- optionally start with `+` for international format
- have a valid phone length
- match a supported phone number format

The system accepts both **local Palestinian format** and **international format**.

During the **standardization stage**, the system converts the phone number into a unified format before using it for identity matching or storage.

---

## Example Valid Inputs

0599123456  
0569876541  
+970599123456  
+970569876541  

---

## Example Invalid Inputs

abc123  
123  
+970-ABC-123  

---

## Validation Error Message


يرجى إدخال رقم هاتف صحيح.


---

# Email Address Validation

Email is a **required field** for doctor invitations.

---

## Requirements

The email must:

- follow a valid email structure
- contain a valid domain
- not include invalid characters

---

## Example Valid Emails

doctor@example.com  
dr.ahmad@clinic.org  

---

## Example Invalid Emails

doctor@  
doctor.com  
doctor@@example.com  

---

## Validation Error Message


يرجى إدخال بريد إلكتروني صحيح.


---

# Doctor Name Validation

Doctor name is used **for display purposes only**, not for identity matching.

However, the name must still pass minimal validation.

---

## Requirements

The name must:

- not be empty
- not consist only of whitespace
- contain at least one visible character

The system should allow flexible naming to support different languages.

---

## Example Valid Names

Dr Ahmad Khaled  
Ahmad Khaled  
Dr Lina Nasser  

---

## Example Invalid Names

(empty)  
spaces only  

---

## Validation Error Message


اسم الطبيب لا يمكن أن يكون فارغًا.


---

# Specialty Selection Validation

When inviting a doctor, the clinic must assign **at least one specialty**.

---

## Requirements

- at least **one specialty must be selected**
- the specialty must belong to the clinic's available specialties

---

## Invalid Cases

The system must reject invitations if:

- no specialty is selected
- an invalid specialty ID is provided
- the specialty does not belong to the clinic

---

## Validation Error Message


يرجى اختيار تخصص واحد على الأقل.


---

# Required Fields Summary

| Field | Required |
|------|------|
| Phone Number | Yes |
| Email Address | Yes |
| Doctor Name | Yes (display purpose) |
| Specialty Selection | At least one |

---

# Validation Timing

Validation must occur **after input standardization** and before invitation business logic.

Recommended processing order:

1. receive raw input
2. standardize input
3. validate input
4. detect existing doctor (Strictly governed by `DOCTOR_IDENTITY_RESOLUTION.md`)
5. apply invitation rules
6. create invitation
7. send notifications

---

# Notification Policy

After validation succeeds and the invitation is created, the system may send notifications.

Supported notification channels:

## Email Notification

The **primary and default notification channel** used for doctor invitations.

An email is sent to the doctor's registered email address.

---

## In-App Notifications

Notifications displayed inside the platform interface such as:

- notification bell
- notifications page
- dashboard alerts

These are used mainly for important in-system events such as a **new doctor invitation**.

---

## SMS Notifications

SMS is strictly an **optional fallback that is not active by default**. While technically supported by the system, it should only be enabled if explicitly required later.

---

# Security Considerations

Input validation helps prevent:

- malformed data injection
- invalid invitation records
- identity confusion
- corrupted system data

Validation must always occur **on the server side**, even if frontend validation exists.

---

# Error Handling

If validation fails:

- the invitation process must stop immediately
- no database record should be created
- a clear Arabic error message must be returned

Error messages must not expose internal system logic.

---

# Related Documents

DOCTOR_INPUT_STANDARDIZATION.md  
DOCTOR_INVITATION_RULES.md  
DOCTOR_INVITATION_LIMITS.md  
EXISTING_DOCTOR_INVITATION_FLOW.md  
DOCTOR_CREDENTIAL_VERIFICATION.md