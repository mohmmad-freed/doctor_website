import random
import re
import logging

from django.core.cache import cache
from django.conf import settings

from accounts.services.tweetsms import send_sms as tweetsms_send_sms

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
def _normalize_phone(phone: str) -> str:
    """
    Normalize Palestinian phone numbers for TweetsMS.
    TweetsMS expects numbers WITHOUT the '+' prefix.
    Handles:
      - 059xxxxxxxx / 056xxxxxxxx  -> 97059xxxxxxxx / 97056xxxxxxxx
      - +97059xxxxxxxx             -> 97059xxxxxxxx
      - 97059xxxxxxxx              -> 97059xxxxxxxx
      - strips spaces, dashes, parentheses
    """
    if not phone:
        return phone

    raw = str(phone).strip()

    # Remove spaces, dashes, parentheses
    raw = re.sub(r"[\s\-\(\)]", "", raw)

    # Strip leading '+' if present
    if raw.startswith("+"):
        raw = raw[1:]

    # If already starts with 970, return as-is
    if raw.startswith("970"):
        return raw

    # If local Palestinian mobile starting with 0 (059/056)
    if raw.startswith("0"):
        # Remove exactly ONE leading zero (safer than lstrip("0"))
        raw = raw[1:]

    # After removing one leading 0, it should look like 59xxxxxxxx or 56xxxxxxxx
    # If user entered already 59/56 prefix, this will work too.
    if raw.startswith("59") or raw.startswith("56"):
        return f"970{raw}"

    # Fallback: if user gave something else, still try 970 + raw
    # (but log it clearly so you notice)
    return f"970{raw}"


# ============================================
# OTP GENERATION & STORAGE
# ============================================
def generate_otp():
    return str(random.randint(100000, 999999))


def store_otp(phone, otp):
    cache.set(_otp_key(phone), otp, timeout=OTP_EXPIRY_SECONDS)


def send_otp_mock(phone, otp):
    logger.info(f"[MOCK SMS] Sending OTP {otp} to {phone}")
    return True


# ============================================
# SMS PROVIDER CHECK
# ============================================
def _is_using_tweetsms():
    """Check if TweetsMS is configured as the SMS provider"""
    provider = getattr(settings, "SMS_PROVIDER", "").upper()
    if provider != "TWEETSMS":
        return False
    return all(
        [
            getattr(settings, "TWEETSMS_API_KEY", ""),
            getattr(settings, "TWEETSMS_SENDER", ""),
        ]
    )


# ============================================
# MAIN FUNCTIONS
# ============================================
def request_otp(phone):
    """
    Main function to request an OTP.
    Generates OTP locally and delivers via TweetsMS if configured,
    otherwise falls back to mock (dev mode).
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

    # 3. Generate and store OTP locally (always)
    otp = generate_otp()
    store_otp(phone, otp)

    # 4. Deliver OTP via SMS or mock
    otp_sent_via_sms = False
    using_tweetsms = _is_using_tweetsms()
    logger.info("[OTP] using_tweetsms=%s phone=%s", using_tweetsms, phone)

    if using_tweetsms:
        sms_phone = _normalize_phone(phone)
        message = f"Your verification code is: {otp}"

        try:
            tweetsms_send_sms(sms_phone, message)
            otp_sent_via_sms = True

            logger.info("[OTP] TweetsMS send OK for phone=%s as=%s", phone, sms_phone)

        except Exception as e:
            logger.exception(
                "[TWEETSMS] Failed to send OTP to %s (as %s). Error=%r",
                phone,
                sms_phone,
                e,
            )

            # Clean up stored OTP on send failure
            cache.delete(_otp_key(phone))

            return (
                False,
                "Failed to send OTP via SMS. Please check your phone number or contact support.",
            )

    if not otp_sent_via_sms:
        # Check if we are in DEBUG mode to allow Mock fallback
        if not getattr(settings, "DEBUG", False):
            logger.error(
                "[OTP] SMS provider not configured in production. Cannot send OTP."
            )
            cache.delete(_otp_key(phone))
            return False, "SMS service is not configured."

        # Mock: OTP already generated and stored, just log it
        logger.warning("[OTP] Falling back to MOCK mode for phone=%s", phone)
        send_otp_mock(phone, otp)

    # 5. Update resend count
    if resend_count == 0:
        cache.set(_otp_resend_count_key(phone), 1, timeout=24 * 60 * 60)
    else:
        cache.incr(_otp_resend_count_key(phone))

    # 6. Set cooldown
    cache.set(_otp_cooldown_key(phone), True, timeout=OTP_RESEND_COOLDOWN_SECONDS)

    # 7. Reset failed attempts
    cache.delete(_otp_attempts_key(phone))

    # Return different message if we used Mock
    if not otp_sent_via_sms:
        return True, "OTP generated (Dev Mode). Check console."

    return True, "OTP sent successfully."


def verify_otp(phone, entered_otp):
    """
    Main function to verify OTP.
    Always uses cache-based verification (OTP is generated and stored locally).
    """
    return _verify_otp_from_cache(phone, entered_otp)


def _verify_otp_from_cache(phone, entered_otp):
    """Verify OTP against the value stored in Redis cache"""
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
