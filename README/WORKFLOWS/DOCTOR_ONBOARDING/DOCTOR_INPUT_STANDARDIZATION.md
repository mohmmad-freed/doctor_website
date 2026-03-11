# DOCTOR_INPUT_STANDARDIZATION.md

## Purpose

This document defines how doctor input data must be normalized and standardized before being processed by the system.

The goal of input standardization is to ensure that doctor identity data is handled consistently across the platform.

Without proper standardization, small differences in formatting may cause the system to treat the same doctor as multiple different users.

Examples of problems this prevents:

- duplicate doctor records caused by different phone formats
- email mismatches caused by uppercase characters
- identity mismatches due to extra spaces
- inconsistent name formatting
- failed doctor detection during invitation

Input standardization must occur **before validation and before database comparisons**.

---

# Core Principle

All doctor identity inputs must be normalized before they are used for:

- identity matching
- invitation checks
- duplicate detection
- validation rules
- database storage
- database queries

The system must never rely on raw user input for identity matching.

All logic must operate on standardized values.

---

# Fields Subject to Standardization

The following fields must always be standardized before validation or database operations:

- phone number
- email address
- doctor name
- optional identity numbers (if used)

---

# Phone Number Standardization

Phone numbers must follow a consistent canonical format before being compared or stored.

### Standardization Steps

1. Remove all spaces
2. Remove dashes
3. Remove parentheses
4. Remove leading and trailing whitespace
5. Convert the number to a unified international format when possible

### Example Inputs

+970 599 123 456  
0599-123-456  
0599123456  

### Standardized Result

+970599123456

### Rules

- Phone numbers must be stored in a **single consistent format**
- All identity comparisons must use the standardized value
- Phone number uniqueness checks must use the standardized value

---

# Email Standardization

Email addresses must be normalized before comparison or storage.

### Standardization Steps

1. Remove leading spaces
2. Remove trailing spaces
3. Convert all characters to lowercase

### Example Inputs

Doctor@Example.com  
doctor@example.com  
 doctor@example.com  

### Standardized Result

doctor@example.com

### Rules

- Email comparisons must always use lowercase
- Email uniqueness checks must use the standardized value

---

# Doctor Name Standardization

Doctor names are primarily used for display but should still be standardized for consistency.

### Standardization Steps

1. Trim leading spaces
2. Trim trailing spaces
3. Replace multiple spaces with a single space

### Example Inputs

Dr   Ahmad   Khaled  
 Ahmad Khaled  

### Standardized Result

Dr Ahmad Khaled

### Notes

- Doctor names are **not used as primary identity identifiers**
- Name normalization improves UI consistency

---

# Identity Number Standardization (Optional)

If the system uses identity numbers such as national ID or license numbers, they should also be standardized.

### Standardization Steps

1. Remove spaces
2. Remove dashes
3. Trim leading and trailing whitespace

### Example Input

1234-567-890

### Standardized Result

1234567890

---

# Standardization Timing

Standardization must occur **immediately after receiving user input** and before:

- validation checks
- duplicate detection
- doctor identity matching
- invitation logic
- database queries

This ensures that all logic operates on consistent data.

---

# Storage Rules

Whenever possible:

- store standardized values in the database
- use standardized values in queries
- avoid storing inconsistent formatting

This ensures reliable identity matching across the system.

---

# Matching Behavior

When detecting an existing doctor, the system must compare:

- standardized phone number
- standardized email

Matching must always use normalized data.

---

# Error Prevention

Standardization prevents common system errors such as:

- duplicate doctor accounts
- incorrect identity matching
- invitation failures
- inconsistent doctor records
- formatting-related mismatches

---

# Implementation Responsibility

Input standardization should typically be handled in:

- form cleaning logic
- input processing layers
- backend service logic
- API request preprocessing

The goal is to ensure all downstream logic receives standardized data.

---

# Relationship With Validation

Standardization must occur **before validation**.

Recommended processing flow:

1. receive raw user input
2. standardize input
3. validate standardized input
4. perform identity matching
5. process invitation or store data

---

# Related Documents

DOCTOR_INPUT_VALIDATION.md  
DOCTOR_INVITATION_RULES.md  
DOCTOR_INVITATION_LIMITS.md  
EXISTING_DOCTOR_INVITATION_FLOW.md