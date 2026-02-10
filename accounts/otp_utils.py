import random
import re
import logging

from django.core.cache import cache
from django.conf import settings

from accounts.services.twilio_verify import send_otp as twilio_send_otp
from accounts.services.twilio_verify import verify_otp as twilio_verify_otp

logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_EXPIRY_SECONDS = 5 * 60
OTP_MAX_RESEND_PER_DAY = 3
OTP_RESEND_COOLDOWN_SECONDS = 60


def _otp_key(phone):
    return f"otp:code:{phone}"


def _otp_attempts_key(phone):
    return f"otp:attempts:{phone}"


def _otp_resend_count_key(phone):
    return f"otp:resend_count:{phone}"


def _otp_cooldown_key(phone):
    return f"otp:cooldown:{phone}"


# ============================================
# FORMAT HELPERS
# ============================================
def _normalize_phone_for_twilio(phone: str) -> str:
    """
    Normalize Palestinian phone numbers for Twilio Verify (E.164).
    Handles:
      - 059xxxxxxxx / 056xxxxxxxx  -> +97059xxxxxxxx / +97056xxxxxxxx
      - +97059xxxxxxxx             -> +97059xxxxxxxx
      - 97059xxxxxxxx              -> +97059xxxxxxxx
      - strips spaces, dashes, parentheses
    NOTE: This is a pragmatic normalizer for your project.
    """
    if not phone:
        return phone

    raw = str(phone).strip()

    # Remove spaces, dashes, parentheses
    raw = re.sub(r"[\s\-\(\)]", "", raw)

    # If already E.164 with +
    if raw.startswith("+"):
        return raw

    # If starts with 970... (without +)
    if raw.startswith("970"):
        return f"+{raw}"

    # If local Palestinian mobile starting with 0 (059/056)
    if raw.startswith("0"):
        # Remove exactly ONE leading zero (safer than lstrip("0"))
        raw = raw[1:]

    # After removing one leading 0, it should look like 59xxxxxxxx or 56xxxxxxxx
    # If user entered already 59/56 prefix, this will work too.
    if raw.startswith("59") or raw.startswith("56"):
        return f"+970{raw}"

    # Fallback: if user gave something else, still try +970 + raw
    # (but log it clearly so you notice)
    return f"+970{raw}"


# ============================================
# MOCK FUNCTIONS (for development/testing)
# ============================================
def generate_otp():
    return str(random.randint(100000, 999999))


def store_otp(phone, otp):
    cache.set(_otp_key(phone), otp, timeout=OTP_EXPIRY_SECONDS)


def send_otp_mock(phone, otp):
    logger.info(f"[MOCK SMS] Sending OTP {otp} to {phone}")
    return True


# ============================================
# MAIN FUNCTIONS
# ============================================
def _is_using_twilio():
    """Check if Twilio credentials are configured"""
    return all(
        [
            getattr(settings, "TWILIO_ACCOUNT_SID", None),
            getattr(settings, "TWILIO_AUTH_TOKEN", None),
            getattr(settings, "TWILIO_VERIFY_SID", None),
        ]
    )


def request_otp(phone):
    """
    Main function to request an OTP.
    Automatically uses Twilio if configured, otherwise falls back to mock.
    """

    # 1. Check cooldown
    if cache.get(_otp_cooldown_key(phone)):
        logger.warning("[OTP] Cooldown active for phone=%s", phone)
        return False, "Please wait before requesting a new OTP."

    # 2. Check daily resend limit
    resend_count = cache.get(_otp_resend_count_key(phone)) or 0

    if getattr(settings, "ENFORCE_OTP_LIMITS", False):
        if resend_count >= OTP_MAX_RESEND_PER_DAY:
            logger.warning(
                "[OTP] Daily resend limit reached for phone=%s resend_count=%s",
                phone,
                resend_count,
            )
            return (
                False,
                "You have reached the maximum OTP requests for today. Try again tomorrow.",
            )

    # 3. Send OTP
    otp_sent_via_twilio = False

    using_twilio = _is_using_twilio()
    logger.info("[OTP] using_twilio=%s phone=%s", using_twilio, phone)

    if using_twilio:
        twilio_phone = _normalize_phone_for_twilio(phone)

        try:
            twilio_send_otp(twilio_phone)  # ALWAYS creates a new Verify session
            otp_sent_via_twilio = True

            logger.info("[TWILIO] send_otp OK for phone=%s as=%s", phone, twilio_phone)

            # Clean up any lingering mock OTPs so verification doesn't get confused
            cache.delete(_otp_key(phone))

        except Exception as e:
            # Print + log stacktrace for debugging
            logger.exception(
                "[TWILIO] Failed to send OTP to %s (as %s). Error=%r",
                phone,
                twilio_phone,
                e,
            )

            return (
                False,
                "Failed to send OTP via SMS. Please check your phone number or contact support.",
            )

    if not otp_sent_via_twilio:
        # Check if we are in DEBUG mode to allow Mock fallback
        if not getattr(settings, "DEBUG", False):
            logger.error("[OTP] Twilio not configured in production. Cannot send OTP.")
            return False, "SMS service is not configured."

        # Mock: we generate and store OTP ourselves
        logger.warning("[OTP] Falling back to MOCK mode for phone=%s", phone)

        otp = generate_otp()
        store_otp(phone, otp)
        send_otp_mock(phone, otp)

    # 4. Update resend count
    if resend_count == 0:
        cache.set(_otp_resend_count_key(phone), 1, timeout=24 * 60 * 60)
    else:
        cache.incr(_otp_resend_count_key(phone))

    # 5. Set cooldown
    cache.set(_otp_cooldown_key(phone), True, timeout=OTP_RESEND_COOLDOWN_SECONDS)

    # 6. Reset failed attempts
    cache.delete(_otp_attempts_key(phone))

    # Return different message if we used Mock
    if not otp_sent_via_twilio:
        return True, "OTP generated (Dev Mode). Check console."

    return True, "OTP sent successfully."


def verify_otp(phone, entered_otp):
    """
    Main function to verify OTP.
    Prioritizes Mock verification if a Mock OTP exists in cache (from fallback or dev mode).
    Otherwise uses Twilio if configured.
    """

    # 1. Check if we have a Mock OTP stored (implies we used Mock/Fallback to send)
    if cache.get(_otp_key(phone)):
        logger.info("[OTP] Verifying via MOCK for phone=%s", phone)
        return verify_otp_mock(phone, entered_otp)

    # 2. If not, and Twilio is configured, try Twilio
    using_twilio = _is_using_twilio()

    if using_twilio:
        twilio_phone = _normalize_phone_for_twilio(phone)

        try:
            if twilio_verify_otp(twilio_phone, entered_otp):
                logger.info(
                    "[TWILIO] verify_otp OK phone=%s as=%s", phone, twilio_phone
                )
                return True, "Phone number verified successfully."

            logger.warning(
                "[TWILIO] Invalid/expired OTP phone=%s as=%s", phone, twilio_phone
            )
            return False, "Invalid or expired OTP."

        except Exception as e:
            logger.exception(
                "[TWILIO] OTP verification failed for %s (as %s). Error=%r",
                phone,
                twilio_phone,
                e,
            )
            return False, "Verification failed. Please try again."

    # 3. Fallback/Default to Mock (though likely caught by step 1 if it existed)
    logger.warning("[OTP] Fallback MOCK verify for phone=%s", phone)
    return verify_otp_mock(phone, entered_otp)


def verify_otp_mock(phone, entered_otp):
    """Mock OTP verification using Redis cache"""
    stored_otp = cache.get(_otp_key(phone))

    if stored_otp is None:
        return (
            False,
            "OTP has expired or was never requested. Please request a new one.",
        )

    if str(entered_otp).strip() == str(stored_otp).strip():
        cache.delete(_otp_key(phone))
        cache.delete(_otp_attempts_key(phone))
        return True, "Phone number verified successfully."

    attempts = cache.get(_otp_attempts_key(phone)) or 0
    attempts += 1
    cache.set(_otp_attempts_key(phone), attempts, timeout=OTP_EXPIRY_SECONDS)

    remaining = 3 - attempts

    if remaining <= 0:
        cache.delete(_otp_key(phone))
        cache.delete(_otp_attempts_key(phone))
        return False, "Too many incorrect attempts. Please request a new OTP."

    return False, f"Incorrect OTP. You have {remaining} attempt(s) left."


# ============================================
# HELPERS
# ============================================
def is_in_cooldown(phone):
    return cache.get(_otp_cooldown_key(phone)) is not None


def get_remaining_resends(phone):
    if not getattr(settings, "ENFORCE_OTP_LIMITS", False):
        return 999  # unlimited

    resend_count = cache.get(_otp_resend_count_key(phone)) or 0
    return OTP_MAX_RESEND_PER_DAY - resend_count
