# Post-Debug Cleanup Checklist

This document lists files and configurations that were used for development, debugging, and testing. **Before going to production or finishing the current development phase**, review this list to delete temporary files and secure the configuration.

## 🔴 High Risk (Delete or Secure Immediately)

These files contain sensitive operations or insecurity that should not exist in a production environment.

| File / Config | Status | Reason |
| :--- | :--- | :--- |
| `reset_db.py` | ✅ **Deleted** | ⚠️ Destructive script! Drops the entire database `clinic_db`. |
| `reset_and_migrate.bat` | ✅ **Deleted** | ⚠️ Batch script that runs `reset_db.py`. Accidental execution wipes data. |
| `.env` (Secrets) | **SECURE** | Ensure `DEBUG=0` in production. Change `SECRET_KEY`. Use strong passwords for `DB_PASSWORD` and `BREVO_SMTP_PASS`. |
| `.env` (Flags) | **UPDATE** | Set `ENFORCE_PHONE_VERIFICATION=1` and `ENFORCE_OTP_LIMITS=1` to enable security features. |

## 🟡 Medium Risk (Debug Utilities)

These are utility scripts created to help with development and manual verification. They are generally safe but clutter the repository.

| File | Status | Description |
| :--- | :--- | :--- |
| `create_demo_user.py` | ✅ **Deleted** | Creates a demo user/patient. Useful for local dev, but not needed in production source. |
| `check_active_otps.py` | ✅ **Deleted** | CONNECTS TO REDIS to peek at OTP codes. Security risk if left accessible. |
| `test_twilio.py` | ✅ **Deleted** | Simple script to test Twilio connectivity. No longer needed if SMS works. |
| `verify_login_scenarios.py` | ✅ **Deleted** | Ad-hoc script to test login API flows manually. |
| `verify_patient_profile.py` | ✅ **Deleted** | Ad-hoc script to test profile API flows manually. |
| `accounts/force_fix_final.py` | ✅ **Deleted** | One-time template fix script. |
| `accounts/reproduce_500.py` | ✅ **Deleted** | One-time 500 error reproduction script. |

## ⚙️ Configuration Flags to Review

Check your `.env` file for these specific flags:

1.  **`ENFORCE_PHONE_VERIFICATION`**
    *   `0`: Development mode. Any phone number works, OTPs are mocked or skipped.
    *   `1`: Production mode. **(Required for Launch)**. Enforces real SMS verification.

2.  **`ENFORCE_OTP_LIMITS`**
    *   `0`: Unlimited OTP requests (for testing UI/Network).
    *   `1`: Strict rate limiting (e.g., 3 OTPs per hour). **(Required for Launch)**.

3.  **`DEBUG`**
    *   `1`: Django Debug mode (Detailed error pages).
    *   `0`: Production mode (Standard 404/500 pages).

## 🟢 Safe to Keep (Standard Tests)

The following files are part of the standard test suite and **should be kept**:

*   `accounts/tests.py`
*   `accounts/tests_*.py` (e.g., `tests_logout.py`, `tests_phone_change.py` - unless they were purely temporary local files, but they look structured).
*   `patients/tests.py`
*   `clinics/tests.py`
