import random
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
from django.urls import reverse
from django.core.cache import cache
from django.utils.crypto import get_random_string
import os
import logging

logger = logging.getLogger(__name__)

from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

EMAIL_VERIFICATION_TOKEN_EXPIRY = 15 * 60  # 15 minutes


def _get_brevo_api():
    """Initialize and return Brevo API instance"""
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = os.environ.get("BREVO_API_KEY")
    api_client = sib_api_v3_sdk.ApiClient(configuration)
    return sib_api_v3_sdk.TransactionalEmailsApi(api_client)


def _send_email(to_email, subject, html_content, text_content):
    """Send a single transactional email via Brevo"""
    api_instance = _get_brevo_api()

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        sender={"name": "Clinic Website", "email": "msamalq306@gmail.com"},
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )

    api_instance.send_transac_email(send_smtp_email)


def generate_email_verification_token(user, email):
    """Generate a stateless signed token containing user ID and email"""
    signer = TimestampSigner()
    # Sign a complex value to ensure we verify the right user and email
    data = {"user_id": user.id, "email": email.lower().strip()}
    return signer.sign_object(data)


def verify_email_token(token):
    """Verify the token and return the embedded email if valid and within 15 mins"""
    signer = TimestampSigner()
    try:
        data = signer.unsign_object(token, max_age=EMAIL_VERIFICATION_TOKEN_EXPIRY)
        return True, data, "Email verified successfully!"
    except SignatureExpired:
        return False, None, "The verification link has expired (valid for 15 minutes)."
    except BadSignature:
        return False, None, "Invalid verification link."
    except Exception as e:
        return False, None, "Invalid verification link."


def send_verification_email(email, request):
    try:
        user = request.user
        token = generate_email_verification_token(user, email)
        verification_url = request.build_absolute_uri(
            reverse("accounts:verify_email", kwargs={"token": token})
        )

        subject = "Verify Your Email - Clinic Website"
        text_content = (
            f"Hello {user.name},\n\n"
            f"Thank you for registering with Clinic Website!\n\n"
            f"Please verify your email address by clicking the link below:\n\n"
            f"{verification_url}\n\n"
            f"This link will expire in 15 minutes.\n\n"
            f"If you didn't request this, please ignore this email.\n\n"
            f"Best regards,\nClinic Website Team"
        )
        html_content = (
            f"<h2>Welcome {user.name}!</h2>"
            f"<p>Thank you for registering with Clinic Website!</p>"
            f"<p>Please verify your email address by clicking the link below:</p>"
            f"<p><a href='{verification_url}'>Verify My Email</a></p>"
            f"<p>This link will expire in 15 minutes.</p>"
            f"<p>If you didn't request this, please ignore this email.</p>"
            f"<br><p>Best regards,<br>Clinic Website Team</p>"
        )

        _send_email(email, subject, html_content, text_content)

        logger.info(f"[EMAIL] Verification email sent to {email}")
        return True, "Verification email sent! Please check your inbox."

    except ApiException as e:
        logger.error(f"[EMAIL] Brevo API error sending to {email}: {e}")
        return False, "Failed to send verification email. Please try again."
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send verification email to {email}: {e}")
        return False, "Failed to send verification email. Please try again."


def send_change_email_verification(email, request):
    try:
        user = request.user
        token = generate_email_verification_token(user, email)
        verification_url = request.build_absolute_uri(
            reverse("accounts:verify_change_email", kwargs={"token": token})
        )

        subject = "Confirm Your New Email Address - Clinic Website"
        text_content = (
            f"Hello {user.name},\n\n"
            f"You have requested to change your email address on Clinic Website.\n\n"
            f"Please confirm your new email address by clicking the link below:\n\n"
            f"{verification_url}\n\n"
            f"This link will expire in 15 minutes.\n\n"
            f"If you didn't request this change, please ignore this email.\n\n"
            f"Best regards,\nClinic Website Team"
        )
        html_content = (
            f"<h2>Email Change Request</h2>"
            f"<p>You have requested to change your email address on Clinic Website.</p>"
            f"<p>Please confirm your new email address by clicking the link below:</p>"
            f"<p><a href='{verification_url}'>Confirm New Email</a></p>"
            f"<p>This link will expire in 15 minutes.</p>"
            f"<p>If you didn't request this change, please ignore this email.</p>"
            f"<br><p>Best regards,<br>Clinic Website Team</p>"
        )

        _send_email(email, subject, html_content, text_content)

        logger.info(f"[EMAIL] Change email verification sent to {email}")
        return True, "Verification email sent. Please check your inbox."

    except ApiException as e:
        logger.error(f"[EMAIL] Brevo API error sending to {email}: {e}")
        return False, "Failed to send verification email. Please try again."
    except Exception as e:
        logger.error(
            f"[EMAIL] Failed to send change email verification to {email}: {e}"
        )
        return False, "Failed to send verification email. Please try again."


# ============================================
# EMAIL OTP (6-digit code for clinic verification)
# ============================================
EMAIL_OTP_EXPIRY_SECONDS = 10 * 60  # 10 minutes
_EMAIL_OTP_MAX_ATTEMPTS = 3
_EMAIL_OTP_COOLDOWN_SECONDS = 60


def _email_otp_key(email):
    return f"email_otp:code:{email.lower()}"


def _email_otp_attempts_key(email):
    return f"email_otp:attempts:{email.lower()}"


def _email_otp_cooldown_key(email):
    return f"email_otp:cooldown:{email.lower()}"


def send_email_otp(email, recipient_name):
    """
    Generate a 6-digit OTP, store in Redis, and deliver via Brevo.
    Returns (True, message) or (False, error_message).
    """
    if cache.get(_email_otp_cooldown_key(email)):
        return False, "يرجى الانتظار قبل طلب رمز جديد."

    otp = str(random.randint(100000, 999999))
    cache.set(_email_otp_key(email), otp, timeout=EMAIL_OTP_EXPIRY_SECONDS)

    subject = "رمز التحقق — Clinic"
    text_content = (
        f"مرحباً {recipient_name}،\n\n"
        f"رمز التحقق الخاص بك هو: {otp}\n\n"
        f"الرمز صالح لمدة 10 دقائق.\n\n"
        f"إذا لم تطلب هذا الرمز، يرجى تجاهل هذه الرسالة.\n\n"
        f"مع تحيات،\nفريق كلينك"
    )
    html_content = (
        f"<p>مرحباً <strong>{recipient_name}</strong>،</p>"
        f"<p>رمز التحقق الخاص بك هو:</p>"
        f"<h2 style='letter-spacing:8px;font-size:2rem;font-family:monospace;'>{otp}</h2>"
        f"<p>الرمز صالح لمدة 10 دقائق.</p>"
        f"<p>إذا لم تطلب هذا الرمز، يرجى تجاهل هذه الرسالة.</p>"
        f"<br><p>مع تحيات،<br>فريق كلينك</p>"
    )

    try:
        _send_email(email, subject, html_content, text_content)
    except Exception as e:
        logger.error("[EMAIL OTP] Failed to send to %s: %r", email, e)
        cache.delete(_email_otp_key(email))
        return False, "فشل إرسال رمز التحقق. يرجى المحاولة مرة أخرى."

    cache.set(_email_otp_cooldown_key(email), True, timeout=_EMAIL_OTP_COOLDOWN_SECONDS)
    cache.delete(_email_otp_attempts_key(email))
    logger.info("[EMAIL OTP] sent to %s", email)
    return True, "تم إرسال رمز التحقق إلى بريدك الإلكتروني."


def verify_email_otp(email, entered_otp):
    """
    Verify a 6-digit OTP sent to an email address.
    Returns (True, message) or (False, error_message).
    """
    stored = cache.get(_email_otp_key(email))

    if stored is None:
        return False, "انتهت صلاحية رمز التحقق أو لم يُطلب. يرجى طلب رمز جديد."

    if str(entered_otp).strip() == str(stored).strip():
        cache.delete(_email_otp_key(email))
        cache.delete(_email_otp_attempts_key(email))
        return True, "تم التحقق من البريد الإلكتروني بنجاح."

    attempts = (cache.get(_email_otp_attempts_key(email)) or 0) + 1
    cache.set(_email_otp_attempts_key(email), attempts, timeout=EMAIL_OTP_EXPIRY_SECONDS)
    remaining = _EMAIL_OTP_MAX_ATTEMPTS - attempts

    if remaining <= 0:
        cache.delete(_email_otp_key(email))
        cache.delete(_email_otp_attempts_key(email))
        return False, "عدد كبير من المحاولات الخاطئة. يرجى طلب رمز جديد."

    return False, f"رمز التحقق غير صحيح. لديك {remaining} محاولة متبقية."


def is_email_otp_in_cooldown(email):
    return cache.get(_email_otp_cooldown_key(email)) is not None


def send_appointment_cancellation_email(user, appointment):
    """
    Send an appointment cancellation notification email to the patient.

    Email is only sent when ALL of the following are true:
    - user.email is present (not None, not empty string)
    - user.email_verified is True
      (i.e. the email in the MAIN email field has been verified;
       pending_email is NEVER used for sending)

    If conditions are not met, returns silently without error.

    Args:
        user:        The patient User instance.
        appointment: The cancelled Appointment instance.
    """
    # Guard: only send to verified, confirmed email addresses
    if not user.email or not getattr(user, "email_verified", False):
        logger.info(
            "[EMAIL] Skipping cancellation email for user_id=%s — "
            "no verified email on record.",
            user.id,
        )
        return

    try:
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")

        subject = "إلغاء موعدك — Clinic"

        text_content = (
            f"عزيزي {user.name}،\n\n"
            f"نأسف لإعلامك بأنه تم إلغاء موعدك مع {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str}.\n\n"
            f"يرجى التواصل مع العيادة لإعادة الجدولة.\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<p>عزيزي <strong>{user.name}</strong>،</p>"
            f"<p>نأسف لإعلامك بأنه تم إلغاء موعدك مع "
            f"<strong>{doctor_name}</strong> "
            f"بتاريخ <strong>{date_str}</strong> الساعة <strong>{time_str}</strong>.</p>"
            f"<p>يرجى التواصل مع العيادة لإعادة الجدولة.</p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        )

        _send_email(user.email, subject, html_content, text_content)
        logger.info(
            "[EMAIL] Cancellation email sent to user_id=%s email=%s",
            user.id,
            user.email,
        )

    except Exception as e:
        # Non-fatal: log and continue. Notification was already persisted in-app.
        logger.error(
            "[EMAIL] Failed to send cancellation email to user_id=%s: %r",
            user.id,
            e,
        )

