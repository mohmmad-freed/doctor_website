import random
import re
import logging
import time

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
      - 05xxxxxxxx                 -> 9705xxxxxxxx
      - +9705xxxxxxxx              -> 9705xxxxxxxx
      - +9725xxxxxxxx              -> 9725xxxxxxxx
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

    # If local Palestinian/Regional mobile starting with 0 (05...)
    if raw.startswith("0"):
        # Remove exactly ONE leading zero (safer than lstrip("0"))
        raw = raw[1:]

    # After removing one leading 0, it should look like 5xxxxxxxx
    # If user entered already 5... prefix, this will work too.
    if raw.startswith("5"):
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
        return False, "ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط·آ§ط¸â€‍ط·آ§ط¸â€ ط·ع¾ط·آ¸ط·آ§ط·آ± ط¸â€ڑط·آ¨ط¸â€‍ ط·آ·ط¸â€‍ط·آ¨ ط·آ±ط¸â€¦ط·آ² ط·آ¬ط·آ¯ط¸ظ¹ط·آ¯."

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
                "ط¸â€‍ط¸â€ڑط·آ¯ ط·ع¾ط·آ¬ط·آ§ط¸ث†ط·آ²ط·ع¾ ط·آ§ط¸â€‍ط·آ­ط·آ¯ ط·آ§ط¸â€‍ط¸ظ¹ط¸ث†ط¸â€¦ط¸ظ¹ ط¸â€‍ط·آ·ط¸â€‍ط·آ¨ط·آ§ط·ع¾ ط·آ±ط¸â€¦ط·آ² ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ. ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط·آ§ط¸â€‍ط¸â€¦ط·آ­ط·آ§ط¸ث†ط¸â€‍ط·آ© ط·ط›ط·آ¯ط·آ§ط¸â€¹.",
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
                "ط¸ظ¾ط·آ´ط¸â€‍ ط·آ¥ط·آ±ط·آ³ط·آ§ط¸â€‍ ط·آ±ط¸â€¦ط·آ² ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط·آ¹ط·آ¨ط·آ± ط·آ§ط¸â€‍ط·آ±ط·آ³ط·آ§ط·آ¦ط¸â€‍ ط·آ§ط¸â€‍ط¸â€ ط·آµط¸ظ¹ط·آ©. ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط¸â€¦ط¸â€  ط·آ±ط¸â€ڑط¸â€¦ ط¸â€،ط·آ§ط·ع¾ط¸ظ¾ط¸ئ’ ط·آ£ط¸ث† ط·آ§ط¸â€‍ط·ع¾ط¸ث†ط·آ§ط·آµط¸â€‍ ط¸â€¦ط·آ¹ ط·آ§ط¸â€‍ط·آ¯ط·آ¹ط¸â€¦.",
            )

    if not otp_sent_via_sms:
        # Check if we are in DEBUG mode to allow Mock fallback
        if not getattr(settings, "DEBUG", False):
            logger.error(
                "[OTP] SMS provider not configured in production. Cannot send OTP."
            )
            cache.delete(_otp_key(phone))
            return False, "ط·آ®ط·آ¯ط¸â€¦ط·آ© ط·آ§ط¸â€‍ط·آ±ط·آ³ط·آ§ط·آ¦ط¸â€‍ ط·آ§ط¸â€‍ط¸â€ ط·آµط¸ظ¹ط·آ© ط·ط›ط¸ظ¹ط·آ± ط¸â€¦ط¸â€،ط¸ظ¹ط·آ£ط·آ©."

        # Mock: OTP already generated and stored, just log it
        logger.warning("[OTP] Falling back to MOCK mode for phone=%s", phone)
        send_otp_mock(phone, otp)

    # 5. Update resend count
    if resend_count == 0:
        cache.set(_otp_resend_count_key(phone), 1, timeout=24 * 60 * 60)
    else:
        cache.incr(_otp_resend_count_key(phone))

    # 6. Set cooldown (store expiry timestamp so callers can compute remaining seconds)
    cache.set(_otp_cooldown_key(phone), time.time() + OTP_RESEND_COOLDOWN_SECONDS, timeout=OTP_RESEND_COOLDOWN_SECONDS)

    # 7. Reset failed attempts
    cache.delete(_otp_attempts_key(phone))

    # Return different message if we used Mock
    if not otp_sent_via_sms:
        return True, "ط·ع¾ط¸â€¦ ط·آ¥ط¸â€ ط·آ´ط·آ§ط·طŒ ط·آ±ط¸â€¦ط·آ² ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ (ط¸ث†ط·آ¶ط·آ¹ ط·آ§ط¸â€‍ط·ع¾ط·آ·ط¸ث†ط¸ظ¹ط·آ±). ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط¸â€¦ط¸â€  ط¸ث†ط·آ­ط·آ¯ط·آ© ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸ئ’ط¸â€¦."

    return True, "ط·ع¾ط¸â€¦ ط·آ¥ط·آ±ط·آ³ط·آ§ط¸â€‍ ط·آ±ط¸â€¦ط·آ² ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط·آ¨ط¸â€ ط·آ¬ط·آ§ط·آ­."


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
            "ط·آ§ط¸â€ ط·ع¾ط¸â€،ط·ع¾ ط·آµط¸â€‍ط·آ§ط·آ­ط¸ظ¹ط·آ© ط·آ±ط¸â€¦ط·آ² ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط·آ£ط¸ث† ط¸â€‍ط¸â€¦ ط¸ظ¹ط·ع¾ط¸â€¦ ط·آ·ط¸â€‍ط·آ¨ط¸â€،. ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط·آ·ط¸â€‍ط·آ¨ ط·آ±ط¸â€¦ط·آ² ط·آ¬ط·آ¯ط¸ظ¹ط·آ¯.",
        )

    if str(entered_otp).strip() == str(stored_otp).strip():
        cache.delete(_otp_key(phone))
        cache.delete(_otp_attempts_key(phone))
        return True, "ط·ع¾ط¸â€¦ ط·آ§ط¸â€‍ط·ع¾ط·آ­ط¸â€ڑط¸â€ڑ ط¸â€¦ط¸â€  ط·آ±ط¸â€ڑط¸â€¦ ط·آ§ط¸â€‍ط¸â€،ط·آ§ط·ع¾ط¸ظ¾ ط·آ¨ط¸â€ ط·آ¬ط·آ§ط·آ­."

    attempts = cache.get(_otp_attempts_key(phone)) or 0
    attempts += 1
    cache.set(_otp_attempts_key(phone), attempts, timeout=OTP_EXPIRY_SECONDS)

    remaining = 3 - attempts

    if remaining <= 0:
        cache.delete(_otp_key(phone))
        cache.delete(_otp_attempts_key(phone))
        return False, "ط·ع¾ط·آ¬ط·آ§ط¸ث†ط·آ²ط·ع¾ ط·آ§ط¸â€‍ط·آ­ط·آ¯ ط·آ§ط¸â€‍ط¸â€¦ط·آ³ط¸â€¦ط¸ث†ط·آ­ ط¸â€¦ط¸â€  ط·آ§ط¸â€‍ط¸â€¦ط·آ­ط·آ§ط¸ث†ط¸â€‍ط·آ§ط·ع¾. ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط·آ·ط¸â€‍ط·آ¨ ط·آ±ط¸â€¦ط·آ² ط·آ¬ط·آ¯ط¸ظ¹ط·آ¯."

    return False, f"Incorrect OTP. You have {remaining} attempts remaining."


# ============================================
# HELPERS
# ============================================
def is_in_cooldown(phone):
    return cache.get(_otp_cooldown_key(phone)) is not None


def get_cooldown_remaining(phone):
    """Return the number of seconds remaining in the resend cooldown (0 if not in cooldown)."""
    expiry = cache.get(_otp_cooldown_key(phone))
    if expiry is None:
        return 0
    return max(0, int(expiry - time.time()))


def get_remaining_resends(phone):
    if not getattr(settings, "ENFORCE_OTP_LIMITS", False):
        return 999  # unlimited

    resend_count = cache.get(_otp_resend_count_key(phone)) or 0
    return OTP_MAX_RESEND_PER_DAY - resend_count
