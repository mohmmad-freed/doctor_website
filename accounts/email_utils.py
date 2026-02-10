import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
from django.urls import reverse
from django.core.cache import cache
from django.utils.crypto import get_random_string
import os
import logging

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_TOKEN_EXPIRY = 15 * 60  # 15 minutes


def _get_brevo_api():
    """Initialize and return Brevo API instance"""
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = os.environ.get('BREVO_API_KEY')
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


def _email_verification_key(token):
    return f"email_verification:{token}"


def generate_email_verification_token(email):
    token = get_random_string(32)
    cache.set(
        _email_verification_key(token),
        email.lower().strip(),
        timeout=EMAIL_VERIFICATION_TOKEN_EXPIRY,
    )
    return token


def verify_email_token(token):
    email = cache.get(_email_verification_key(token))
    if email is None:
        return False, None, "Invalid or expired verification link."
    return True, email, "Email verified successfully!"


def invalidate_email_token(token):
    cache.delete(_email_verification_key(token))


def send_verification_email(email, request):
    try:
        token = generate_email_verification_token(email)
        verification_url = request.build_absolute_uri(
            reverse("accounts:verify_email", kwargs={"token": token})
        )

        subject = "Verify Your Email - Clinic Website"
        text_content = (
            f"Hello,\n\n"
            f"Thank you for registering with Clinic Website!\n\n"
            f"Please verify your email address by clicking the link below:\n\n"
            f"{verification_url}\n\n"
            f"This link will expire in 15 minutes.\n\n"
            f"If you didn't request this, please ignore this email.\n\n"
            f"Best regards,\nClinic Website Team"
        )
        html_content = (
            f"<h2>Welcome!</h2>"
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
        token = generate_email_verification_token(email)
        verification_url = request.build_absolute_uri(
            reverse("accounts:verify_change_email", kwargs={"token": token})
        )

        subject = "Confirm Your New Email Address - Clinic Website"
        text_content = (
            f"Hello,\n\n"
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
        logger.error(f"[EMAIL] Failed to send change email verification to {email}: {e}")
        return False, "Failed to send verification email. Please try again."