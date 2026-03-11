# Phone Verification Flow

## Overview
The Phone Verification flow is a critical security and identity validation mechanism in the Clink system. Due to the platform's nature, **the phone number acts as the primary unique identifier constraint and the main method of authentication (`USERNAME_FIELD = "phone"`)**. The verification relies on an SMS-delivered One-Time Password (OTP) backed by Redis.

## Purpose
- Verify the identity and possession of a phone line by the user.
- Serve as the primary security gate for account creation (both Patients and Clinic Owners).
- Facilitate secure phone number updates for existing accounts.
- Provide a recovery mechanism via "Forgot Password".

## Scope
- Patient Registration (Step 1 & 2).
- Clinic Owner Registration (Stage 3 Verification for the owner's phone).
- Profile Settings (Change Phone Request).
- Password Recovery (Forgot Password Flow).

## Real Components Involved
- **Models:** `accounts.models.CustomUser` (Flags: `is_verified` to denote a verified phone number). **No specific database model exists for OTPs.**
- **Views:** `accounts.views` (`register_patient_phone`, `register_patient_verify`, `register_clinic_verify_phone`, `change_phone_request`, `change_phone_verify`, `forgot_password_phone`, `forgot_password_verify`).
- **Services/Utilities:** `accounts.otp_utils` (`request_otp`, `verify_otp`, `_is_using_tweetsms`, etc.).
- **External Provider:** TweetsMS (via `accounts.services.tweetsms`).
- **State/Cache:** Redis cache (for OTP generation, storage, and rate limiting).

## Entry Points
1. **Patient Registration:** `/register/patient/phone/` and `/register/patient/verify/`.
2. **Clinic Owner Registration:** `/register/clinic/verify-phone/`.
3. **Change Phone Number:** `/profile/change-phone/` and `/profile/change-phone/verify/`.
4. **Forgot Password:** `/forgot-password/` and `/forgot-password/verify/`.

## Preconditions
- The user must provide a valid Palestinian phone number format (starts with `059` or `056`, 10 digits).
- For new registrations or phone changes, the number must not already exist in the system (unique constraint on `CustomUser.phone`).
- For password reset, the number *must* exist.

---

## Step-by-Step Flow

### 1. Request OTP
1. **Submission:** The user submits their phone number in a form (e.g., `register_patient_phone`).
2. **Validation:** 
   - `PhoneNumberAuthBackend.normalize_phone_number` normalizes the input.
   - `PhoneNumberAuthBackend.is_valid_phone_number` validates the format.
   - Uniqueness check against `CustomUser.phone` (if applicable).
3. **Trigger:** The view calls `accounts.otp_utils.request_otp(phone)`.
4. **Rate Limiting Check (Redis):**
   - **Cooldown:** Checks `otp:cooldown:{phone}`. If active, rejects request.
   - **Daily Limit:** Checks `otp:resend_count:{phone}`. If `>= 3` (and `ENFORCE_OTP_LIMITS` is True), rejects request.
5. **Generation & Storage Framework:**
   - Generates a 6-digit random string.
   - Saves it to Redis at `otp:code:{phone}` with a 5-minute TTL (`OTP_EXPIRY_SECONDS = 5 * 60`).
6. **Dispatch:**
   - Validates if TweetsMS is configured (`_is_using_tweetsms`).
   - Normalizes the phone further for the provider (`_normalize_phone` to prepend `970` and strip `0`).
   - Sends the SMS via `accounts.services.tweetsms.send_sms`.
   - **Fallback/Mock Mode:** If TweetsMS is not configured and `DEBUG` is True, it logs the OTP to the console instead (`send_otp_mock`). *Note: Fails securely if `DEBUG` is False and no SMS provider exists.*
7. **Post-Dispatch State:**
   - Increments daily resend count (`otp:resend_count:{phone}`).
   - Sets a 60-second cooldown (`otp:cooldown:{phone}`).
   - Deletes any previous failed attempt counts (`otp:attempts:{phone}`).
   - Saves `registration_phone` or similar context to `request.session`.

### 2. Verify OTP
1. **Submission:** User enters the 6-digit code on the respective verification view.
2. **Trigger:** The view calls `accounts.otp_utils.verify_otp(phone, entered_otp)`.
3. **Validation (Redis):**
   - Retrieves `otp:code:{phone}` from cache. If `None`, returns expired/invalid.
   - Compares strings.
4. **Success:** 
   - Deletes `otp:code:{phone}` and `otp:attempts:{phone}` from Redis.
   - Updates session state (e.g., `request.session["phone_verified"] = True` or updates `user.is_verified = True` immediately if authenticated).
5. **Failure:**
   - Increments `otp:attempts:{phone}`.
   - If attempts exceed 3, deletes the OTP entirely, forcing the user to request a new one.

---

## State/Session Handling
- **Redis Cache (Authoritative):** Manages the authoritative state of the OTP, preventing DB bloat.
  - `otp:code:{phone}` (Value: 6-digit code, TTL: 5 min)
  - `otp:attempts:{phone}` (Value: int, TTL: 5 min)
  - `otp:cooldown:{phone}` (Value: bool, TTL: 60 sec)
  - `otp:resend_count:{phone}` (Value: int, TTL: 24 hours)
- **Django Session (Contextual):** Tracks the user's progress through multi-step flows (e.g., `registration_phone`, `change_phone_new`, `reset_phone`, `clinic_reg` dict).

## Validation and Constraints
- **Expiration:** 5 minutes strictly.
- **Max Attempts:** 3 failed guesses invalidate the current OTP.
- **Resend Limits:** Maximum of 3 requests per 24-hour period (if `ENFORCE_OTP_LIMITS` is configured).
- **Phone Formatting:** Complex normalization rules handle variations (`059...`, `+97059...`, `97059...`) to ensure a canonical representation in the database, while securely mapping to the `970` prefix required by TweetsMS.

## Failure Cases and Edge Cases
- **SMS Provider Outage:** Handled via broad `Exception` catching in `request_otp`. Falls back gracefully, notifying the user.
- **Cache Server Unavailability:** Implicitly causes failures; high dependency on Redis uptime.
- **Session Timeout/Loss:** If the user loses their Django session cookie before submitting the OTP, they are redirected back to the beginning of the flow (e.g., "انتهت الجلسة. يرجى البدء من جديد.").
- **Rate Limit Hit:** Returns a secure user-facing message preventing SMS spam.

## Redirects and User Outcomes
- **Patient Reg:** Proceeds to `/register/patient/details/`.
- **Change Phone:** Immediately updates `user.phone` and redirects to `patients:profile`.
- **Forgot Password:** Sets `reset_verified` session flag and redirects to `/forgot-password/reset/`.
- **Clinic Reg:** Proceeds to `/register/clinic/verify-email/`.

## Security and Abuse-Prevention
- Purely cache-based implementation prevents disk I/O bottlenecks and database enumeration scanning.
- Strict 60-second cooldown blocks immediate SMS blasting.
- The 3-attempt limit completely mitigates offline brute-forcing of the 6-digit keyspace.

## Architectural Observations
- **Stateless DB:** The decision to not retain OTP records in PostgreSQL is excellent for performance and data hygiene.
- **Centralized Logic:** `otp_utils.py` contains almost all raw logic, keeping views relatively clean and standardized.
- **Mock Fallback:** The built-in mock fallback for local development (`DEBUG` mode) is a well-implemented developer experience pattern.

## Reuse/Refactor Opportunities
- **Merge Email/Phone OTP Utilities:** `otp_utils.py` (Phone) and `_email_otp_*` functions in `email_utils.py` perform identical Redis operations (generate, store, increment attempts, cooldowns). These could be extracted into a generic `cache_otp_service.py` that takes a `channel` identifier.
- **Normalization Consistency:** Normalization occurs in views, forms, and utils. Encapsulating phone string cleaning centrally in a custom Model Field or strict form mixin could reduce code duplication.

## Phone Number Recycling Security Protection

Because telecom providers may reassign phone numbers to new individuals, SMS OTP alone is insufficient for securing high-privilege accounts (Doctors and Clinic Owners).

### Risk
If a verified doctor's phone number is reassigned by their carrier, any person who acquires that SIM card could potentially gain access to the doctor's verified medical account by simply completing the SMS OTP flow.

### Required Security Rule

Access to an existing verified Doctor or Clinic Owner account must **never** rely solely on SMS OTP.

For high-privilege account access, at least **one additional verification factor** must be present:

| Factor | Description |
|--------|-------------|
| **Email Verification** | The user must also verify access to the email address registered on the account. |
| **Password Authentication** | Standard password login alongside SMS serves as the second factor. |
| **Admin-Assisted Recovery** | If the phone number has changed, the account owner must contact Platform Administration to verify identity and update their phone number manually. |

### Enforcement Scope

This rule applies to:
- Doctor account login after phone number change
- Clinic Owner account login after phone number change
- Password recovery for Doctor and Clinic Owner accounts
- Any phone number update request on a verified account

This rule does NOT apply to:
- Patient accounts (lower privilege, standard SMS OTP is acceptable)
- New account registration (no existing account to protect)

---

## Related Files/Modules
- `accounts/otp_utils.py` (Core logic)
- `accounts/services/tweetsms.py` (External provider integration)
- `accounts/views.py` (View endpoints containing session logic)
- `accounts/backends.py` (Phone normalization functions)

## Future Extension Notes
- Implementing Celery/async tasks for SMS dispatch could improve HTTP response times for the end user during OTP requests.
