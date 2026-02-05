from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django.core.cache import cache
from django.utils.crypto import get_random_string
import logging

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_TOKEN_EXPIRY = 15 * 60  # 15 minutes


def _email_verification_key(token):
    """Cache key for email verification token"""
    return f"email_verification:{token}"


def generate_email_verification_token(email):
    """
    Generate a unique verification token and store the email in cache.
    Returns the token.
    """
    token = get_random_string(32)
    cache.set(
        _email_verification_key(token),
        email.lower().strip(),
        timeout=EMAIL_VERIFICATION_TOKEN_EXPIRY
    )
    return token


def verify_email_token(token):
    """
    Verify the email token and return the email if valid.
    Returns (success: bool, email: str or None, message: str)
    """
    email = cache.get(_email_verification_key(token))
    
    if email is None:
        return False, None, "Invalid or expired verification link."
    
    # Don't delete yet - we'll delete when form is submitted
    # This allows the user to click the link multiple times
    return True, email, "Email verified successfully!"


def invalidate_email_token(token):
    """Delete the verification token from cache"""
    cache.delete(_email_verification_key(token))


def send_verification_email(email, request):
    """
    Send verification email with a link.
    Returns (success: bool, message: str)
    """
    try:
        # Generate token
        token = generate_email_verification_token(email)
        
        # Build verification URL
        verification_url = request.build_absolute_uri(
            reverse('accounts:verify_email', kwargs={'token': token})
        )
        
        # Email subject and body
        subject = "Verify Your Email - Clinic Website"
        message = f"""
Hello,

Thank you for registering with Clinic Website!

Please verify your email address by clicking the link below:

{verification_url}

This link will expire in 15 minutes.

If you didn't request this, please ignore this email.

Best regards,
Clinic Website Team
        """
        
        # Send email
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        
        logger.info(f"[EMAIL] Verification email sent to {email}")
        return True, "Verification email sent! Please check your inbox."
        
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send verification email to {email}: {str(e)}")
        return False, "Failed to send verification email. Please try again."