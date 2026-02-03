import random
from django.core.cache import cache
from django.conf import settings
import logging

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
# TWILIO VERIFY
# ============================================
def _get_twilio_client():
    from twilio.rest import Client
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _format_phone_for_twilio(phone):
    """
    Twilio requires E.164 format: +970XXXXXXXXX
    Converts 059XXXXXXX → +970 59XXXXXXX
    Converts 056XXXXXXX → +970 56XXXXXXX
    """
    if phone.startswith("0"):
        return "+970" + phone[1:]
    return phone


def send_otp_twilio(phone):
    """Send OTP via Twilio Verify API"""
    try:
        client = _get_twilio_client()
        twilio_phone = _format_phone_for_twilio(phone)

        client.verify.v2.services(settings.TWILIO_VERIFY_SID).rate_limits
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SID
        ).verifications.create(
            to=twilio_phone,
            channel="sms"
        )

        logger.info(f"[TWILIO] OTP sent to {phone}, SID: {verification.sid}")
        return True, "OTP sent successfully."

    except Exception as e:
        logger.error(f"[TWILIO] Failed to send OTP to {phone}: {str(e)}")
        return False, f"Failed to send OTP. Please try again."


def verify_otp_twilio(phone, entered_otp):
    """Verify OTP via Twilio Verify API"""
    try:
        client = _get_twilio_client()
        twilio_phone = _format_phone_for_twilio(phone)

        verification_check = client.verify.v2.services(
            settings.TWILIO_VERIFY_SID
        ).verification_checks.create(
            to=twilio_phone,
            code=str(entered_otp)
        )

        if verification_check.status == "approved":
            logger.info(f"[TWILIO] OTP verified for {phone}")
            return True, "Phone number verified successfully."
        else:
            return False, "Incorrect OTP."

    except Exception as e:
        logger.error(f"[TWILIO] OTP verification failed for {phone}: {str(e)}")
        return False, "Verification failed. Please try again."


# ============================================
# MOCK FUNCTIONS (for development/testing)
# ============================================
def generate_otp():
    return str(random.randint(100000, 999999))


def store_otp(phone, otp):
    cache.set(_otp_key(phone), otp, timeout=OTP_EXPIRY_SECONDS)


def send_otp_mock(phone, otp):
    logger.info(f"[MOCK SMS] Sending OTP {otp} to {phone}")
    print(f"\n{'='*40}")
    print(f"[MOCK SMS] OTP for {phone}: {otp}")
    print(f"[MOCK SMS] Valid for {OTP_EXPIRY_SECONDS // 60} minutes")
    print(f"{'='*40}\n")
    return True


# ============================================
# MAIN FUNCTIONS
# ============================================
def _is_using_twilio():
    """Check if Twilio credentials are configured"""
    return all([
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
        settings.TWILIO_VERIFY_SID,
    ])


def request_otp(phone):
    """
    Main function to request an OTP.
    Automatically uses Twilio if configured, otherwise falls back to mock.
    """
    # 1. Check cooldown
    if cache.get(_otp_cooldown_key(phone)):
        return False, "Please wait before requesting a new OTP."

    # 2. Check daily resend limit
    resend_count = cache.get(_otp_resend_count_key(phone)) or 0
    if resend_count >= OTP_MAX_RESEND_PER_DAY:
        return False, "You have reached the maximum OTP requests for today. Try again tomorrow."

    # 3. Send OTP
    if _is_using_twilio():
        # Twilio handles OTP generation and storage internally
        success, message = send_otp_twilio(phone)
        if not success:
            return False, message
    else:
        # Mock: we generate and store OTP ourselves
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

    return True, "OTP sent successfully."


def verify_otp(phone, entered_otp):
    """
    Main function to verify OTP.
    Automatically uses Twilio if configured, otherwise falls back to mock verification.
    """
    if _is_using_twilio():
        return verify_otp_twilio(phone, entered_otp)
    else:
        return verify_otp_mock(phone, entered_otp)


def verify_otp_mock(phone, entered_otp):
    """Mock OTP verification using Redis cache"""
    stored_otp = cache.get(_otp_key(phone))

    if stored_otp is None:
        return False, "OTP has expired or was never requested. Please request a new one."

    if str(entered_otp).strip() == str(stored_otp).strip():
        cache.delete(_otp_key(phone))
        cache.delete(_otp_attempts_key(phone))
        return True, "Phone number verified successfully."
    else:
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
    resend_count = cache.get(_otp_resend_count_key(phone)) or 0
    return OTP_MAX_RESEND_PER_DAY - resend_count