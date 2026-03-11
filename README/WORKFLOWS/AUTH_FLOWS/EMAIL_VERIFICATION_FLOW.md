# Email Verification Flow

## Overview
The Email Verification flow in the Clink system validates user email addresses to ensure communication reliability and security. Notably, the system employs **two distinct verification mechanisms** depending on the context:
1. **Link-Based Verification (Stateless):** Used for standard patients and profile email updates.
2. **OTP-Based Verification (Stateful/Redis):** Used exclusively within the 3-stage Clinic Owner registration wizard.

## Purpose
- Confirm ownership of an email address.
- Prevent spam or unauthorized account creation.
- Ensure that system notifications (e.g., appointment cancellations) reach the correct address.

## Scope
- Patient Registration (Optional Step 4).
- Clinic Owner Registration (Stage 3 Verification).
- Profile Settings (Change Email Request).

## Real Components Involved
- **Models:** `accounts.models.CustomUser` (Flags: `email_verified`, fields: `email`, `pending_email`). **No specific database model exists for Email Tokens or OTPs.**
- **Views:** `accounts.views` (`send_email_verification`, `verify_email`, `register_clinic_verify_email`, `change_email_request`, `verify_change_email`).
- **Services/Utilities:** `accounts.email_utils` (`send_verification_email`, `verify_email_token`, `send_email_otp`, `verify_email_otp`, `_send_email`).
- **External Provider:** Brevo API (`sib_api_v3_sdk`).
- **State/Cache:** Redis cache (for OTPs) and Django's `TimestampSigner` (for stateless tokens).

## Entry Points
1. **Patient Registration (Optional):** `/register/patient/email/` (`register_patient_email` view).
2. **Clinic Owner Registration:** `/register/clinic/verify-email/` (`register_clinic_verify_email` view).
3. **Profile Edit:** `/profile/change-email/` (`change_email_request` view).
4. **Resend Email API:** `/send-email-verification/` (AJAX).

## Preconditions
- The user must provide a syntactically valid email format.
- The email must not be already registered to another user (uniqueness constraint).
- For Clinic Owners, phone verification must be completed first (`phone_verified` in session).

---

## Mechanism 1: Link-Based Verification (Stateless)
*Used for Patient Registration and Change Email Requests.*

### Step-by-Step Flow
1. **Request:** The user submits their email via the `register_patient_email` or `change_email_request` view.
2. **Validation:** The view checks email format and uniqueness against the `CustomUser` model. The email is **not** saved to the user record yet.
3. **Token Generation:** `accounts.email_utils.generate_email_verification_token` uses Django's `TimestampSigner` to generate a stateless, signed token containing `{"user_id": user.id, "email": email}`.
4. **Dispatch:** `send_verification_email` constructs a verification URL (`/verify-email/<token>/`) and sends the email via the Brevo API.
5. **Verification Click:** The user clicks the link, hitting the `verify_email` view.
6. **Token Unsigning:** `verify_email_token` unsigns the token with a `max_age` of 15 minutes.
7. **Confirmation:**
   - If valid and the logged-in user matches the `user_id` in the token, a POST request confirms the action.
   - The system updates `user.email = new_email` and `user.email_verified = True`.

### State/Session Handling
- **No DB persistence:** The token payload (`user_id`, `email`) and expiration are securely embedded in the token itself via cryptographic signing.
- **Session flags:** Temporary flags like `verification_email_sent` or `change_email_pending` are set in `request.session` to track UI state.

### Validation and Constraints
- **Expiration:** Tokens strictly expire after 15 minutes (`EMAIL_VERIFICATION_TOKEN_EXPIRY = 15 * 60`).
- **User Mismatch:** If User A clicks a verification link meant for User B, the system detects the `user_id` mismatch and renders a `verification_wrong_account.html` template.
- **Authentication:** The verification view requires the user to be authenticated. If they are not, they are redirected to login with a `next` parameter pointing back to the verification URL.

---

## Mechanism 2: OTP-Based Verification (Stateful/Redis)
*Used for Clinic Owner Registration.*

### Step-by-Step Flow
1. **Trigger:** After step 3 of the clinic registration wizard and verifying their phone, the user lands on `register_clinic_verify_email`.
2. **Generation:** `accounts.email_utils.send_email_otp` generates a 6-digit random integer (`100000-999999`).
3. **Storage:** The OTP is stored in Redis under the key `email_otp:code:{email.lower()}` with a 10-minute TTL.
4. **Dispatch:** The OTP is emailed to the user via Brevo.
5. **Verification Submission:** The user enters the 6-digit code.
6. **Validation:** `verify_email_otp` checks the entered code against the Redis cache.
7. **Finalization:** If successful, the user and clinic records are atomically created/updated in the database.

### State/Session Handling
- **Cache (Redis):** Stores the actual OTP, failed attempt counts (`email_otp:attempts:{email}`), and request cooldown limits (`email_otp:cooldown:{email}`).
- **Session:** Relies heavily on `request.session["clinic_reg"]` (tracking `email`, `phone_verified`, etc.) since the user record does not exist or is not fully updated yet.

### Validation and Constraints
- **Expiration:** OTPs expire after 10 minutes (`EMAIL_OTP_EXPIRY_SECONDS = 10 * 60`).
- **Max Attempts:** 3 failed attempts are allowed (`_EMAIL_OTP_MAX_ATTEMPTS = 3`). Exceeding this invalidates the OTP.
- **Cooldown:** A 60-second cooldown is enforced before a new OTP can be requested (`_EMAIL_OTP_COOLDOWN_SECONDS = 60`).

---

## Failure Cases and Edge Cases
- **Expired/Invalid Link:** Renders `verification_failed.html` prompting the user to request a new link.
- **Token Tampering:** Detected by `TimestampSigner`, failing verification securely.
- **Email Service Outage:** Caught by `ApiException` from Brevo; logs error and shows a user-friendly failure message.
- **Clinic Owner Race Condition:** Handled via `transaction.atomic()` during user and clinic creation after successful OTP validation.
- **Multiple Clicks:** Since link tokens are stateless, clicking twice after successful verification (if token still within 15 mins) just re-applies the email update (idempotent), though the user is usually redirected away immediately.

## Redirects and User Outcomes
- **Success (Patient/Change):** Redirects to `patients:profile` with a success message.
- **Success (Clinic Owner):** Proceeds to atomic record creation and redirects to `clinics:my_clinics`.
- **Not Logged In (Link Verification):** Redirects to `/login/?next=/verify-email/<token>/`.

## Security and Abuse-Prevention
- **Stateless security:** Link verification relies on Django's battle-tested cryptographic signing, avoiding database bloat.
- **Rate Limiting:** OTP generation uses strict Redis-based cooldowns (1 minute) and attempt limits (3 tries) to prevent brute-forcing and email spamming.
- **Safe Evaluation:** `_verify_otp_from_cache` strictly enforces string comparisons for OTP values.

## Architectural Observations
- **Implementation Divergence:** The system utilizes two different paradigms (Stateless Links vs Stateful OTPs) for the exact same domain entity (Email).
- **Separation of Concerns:** `email_utils.py` does a good job of abstracting Brevo API logic and token/OTP generation from the views.

## Reuse/Refactor Opportunities
- **Unified Verification Mechanism:** Consider unifying on either OTPs or Links for email verification across the entire platform. OTPs offer a better UX for mobile and multi-device flows, while links are traditional.
- **Abstract Cache Keys:** The Redis key generators (`_email_otp_key`, etc.) could be abstracted into a generalized OTP service that handles both SMS and Email.
- **Pending Email State:** The system manually manages pending state in sessions or stateless tokens, largely ignoring the `pending_email` field present on the `CustomUser` model. This model field could be utilized for a more robust state tracking.

## Related Files/Modules
- `accounts/email_utils.py` (Core logic)
- `accounts/views.py` (View controllers)
- `accounts/urls.py` (Routing)
- `accounts/models.py` (`CustomUser` definition)

## Future Extension Notes
- Implementing a generalized Notification Service could abstract the Brevo API dependency entirely.
- Adding comprehensive tracking for email bounces/complaints via Brevo webhooks to automatically un-verify problematic addresses.
