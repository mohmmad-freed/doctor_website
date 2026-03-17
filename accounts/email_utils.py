import random
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
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


def send_verification_email(user, email, verification_url):
    try:
        subject = "تحقق من بريدك الإلكتروني — كلينك"
        text_content = (
            "\u200f"
            f"مرحباً {user.name}،\n\n"
            f"شكراً لتسجيلك في منصة كلينك!\n\n"
            f"يرجى التحقق من بريدك الإلكتروني بالضغط على الرابط أدناه:\n\n"
            f"{verification_url}\n\n"
            f"ينتهي صلاحية هذا الرابط خلال 15 دقيقة.\n\n"
            f"إذا لم تطلب هذا، يرجى تجاهل هذه الرسالة.\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<div dir='rtl'>"
            f"<h2>مرحباً {user.name}!</h2>"
            f"<p>شكراً لتسجيلك في منصة كلينك!</p>"
            f"<p>يرجى التحقق من بريدك الإلكتروني بالضغط على الرابط أدناه:</p>"
            f"<p><a href='{verification_url}'>تحقق من بريدي الإلكتروني</a></p>"
            f"<p>ينتهي صلاحية هذا الرابط خلال 15 دقيقة.</p>"
            f"<p>إذا لم تطلب هذا، يرجى تجاهل هذه الرسالة.</p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
            f"</div>"
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


def send_change_email_verification(user, email, verification_url):
    try:
        subject = "تأكيد عنوان بريدك الإلكتروني الجديد — كلينك"
        text_content = (
            "\u200f"
            f"مرحباً {user.name}،\n\n"
            f"لقد طلبت تغيير بريدك الإلكتروني على منصة كلينك.\n\n"
            f"يرجى تأكيد عنوان بريدك الإلكتروني الجديد بالضغط على الرابط أدناه:\n\n"
            f"{verification_url}\n\n"
            f"ينتهي صلاحية هذا الرابط خلال 15 دقيقة.\n\n"
            f"إذا لم تطلب هذا التغيير، يرجى تجاهل هذه الرسالة.\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<div dir='rtl'>"
            f"<h2>طلب تغيير البريد الإلكتروني</h2>"
            f"<p>لقد طلبت تغيير بريدك الإلكتروني على منصة كلينك.</p>"
            f"<p>يرجى تأكيد عنوان بريدك الإلكتروني الجديد بالضغط على الرابط أدناه:</p>"
            f"<p><a href='{verification_url}'>تأكيد البريد الإلكتروني الجديد</a></p>"
            f"<p>ينتهي صلاحية هذا الرابط خلال 15 دقيقة.</p>"
            f"<p>إذا لم تطلب هذا التغيير، يرجى تجاهل هذه الرسالة.</p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
            f"</div>"
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
        "\u200f"
        f"مرحباً {recipient_name}،\n\n"
        f"رمز التحقق الخاص بك هو: {otp}\n\n"
        f"الرمز صالح لمدة 10 دقائق.\n\n"
        f"إذا لم تطلب هذا الرمز، يرجى تجاهل هذه الرسالة.\n\n"
        f"مع تحيات،\nفريق كلينك"
    )
    html_content = (
        f"<div dir='rtl'>"
        f"<p>مرحباً <strong>{recipient_name}</strong>،</p>"
        f"<p>رمز التحقق الخاص بك هو:</p>"
        f"<h2 style='letter-spacing:8px;font-size:2rem;font-family:monospace;'>{otp}</h2>"
        f"<p>الرمز صالح لمدة 10 دقائق.</p>"
        f"<p>إذا لم تطلب هذا الرمز، يرجى تجاهل هذه الرسالة.</p>"
        f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        f"</div>"
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


# ============================================
# DOCTOR INVITATION EMAILS
# ============================================


def send_doctor_invitation_email(invitation, accept_url):
    """
    Send an invitation email to a doctor inviting them to join a clinic.
    Primary invitation delivery channel (not SMS).
    """
    to_email = invitation.doctor_email
    if not to_email:
        logger.warning("[EMAIL] No email address for invitation %s", invitation.id)
        return

    clinic_name = invitation.clinic.name
    doctor_name = invitation.doctor_name
    is_secretary = invitation.role == "SECRETARY"

    role_label = "سكرتير/ة" if is_secretary else "طبيب"
    greeting = f"مرحباً {doctor_name}" if is_secretary else f"مرحباً د. {doctor_name}"

    subject = f"دعوة للانضمام إلى {clinic_name} — كلينك"
    text_content = (
        "\u200f"
        f"{greeting}،\n\n"
        f"تمت دعوتك للانضمام إلى {clinic_name} كـ {role_label}.\n\n"
        f"للقبول، يرجى الضغط على الرابط أدناه:\n\n"
        f"{accept_url}\n\n"
        f"هذا الرابط صالح لمدة 48 ساعة.\n\n"
        f"إذا لم تكن تتوقع هذه الدعوة، يرجى تجاهل هذه الرسالة.\n\n"
        f"مع تحيات،\nفريق كلينك"
    )
    html_content = (
        f"<div dir='rtl' style='font-family:Cairo,sans-serif;'>"
        f"<h2>{greeting}!</h2>"
        f"<p>تمت دعوتك للانضمام إلى <strong>{clinic_name}</strong> كـ <strong>{role_label}</strong>.</p>"
        f"<p style='margin:25px 0;'>"
        f"<a href='{accept_url}' style='background:#2563EB;color:#fff;padding:12px 30px;border-radius:8px;"
        f"text-decoration:none;font-weight:bold;font-size:1.1em;'>قبول الدعوة</a>"
        f"</p>"
        f"<p>هذا الرابط صالح لمدة <strong>48 ساعة</strong>.</p>"
        f"<p>إذا لم تكن تتوقع هذه الدعوة، يرجى تجاهل هذه الرسالة.</p>"
        f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        f"</div>"
    )

    try:
        _send_email(to_email, subject, html_content, text_content)
        logger.info("[EMAIL] Invitation email sent to %s for invitation %s", to_email, invitation.id)
    except Exception as e:
        logger.error("[EMAIL] Failed to send invitation email to %s: %r", to_email, e)
        raise


def send_verification_approved_email(user, layer="identity"):
    """
    Notify a doctor that their verification has been approved.
    layer: 'identity' or 'credential'
    """
    if not user.email:
        return

    if layer == "identity":
        subject = "تمت الموافقة على التحقق من هويتك — كلينك"
        body = "تم التحقق من هويتك بنجاح على منصة كلينك."
    else:
        subject = "تمت الموافقة على مؤهلاتك الطبية — كلينك"
        body = "تم التحقق من مؤهلاتك الطبية بنجاح."

    text_content = f"\u200fمرحباً د. {user.name}،\n\n{body}\n\nمع تحيات،\nفريق كلينك"
    html_content = (
        f"<div dir='rtl'>"
        f"<h2>مرحباً د. {user.name}!</h2>"
        f"<p>{body}</p>"
        f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        f"</div>"
    )

    try:
        _send_email(user.email, subject, html_content, text_content)
        logger.info("[EMAIL] Verification approved email sent to %s", user.email)
    except Exception as e:
        logger.error("[EMAIL] Failed to send approval email to %s: %r", user.email, e)


def send_appointment_booking_email(patient, appointment):
    """
    Send a booking confirmation email to the patient.

    Only sent when patient.email is present and patient.email_verified is True.
    Returns True if email was sent successfully, False otherwise.
    """
    if not patient.email or not getattr(patient, "email_verified", False):
        logger.info(
            "[EMAIL] Skipping booking email for user_id=%s — no verified email.",
            patient.id,
        )
        return False

    try:
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        subject = "تأكيد حجز موعدك — Clinic"
        text_content = (
            f"عزيزي {patient.name}،\n\n"
            f"تم تأكيد موعدك مع {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}.\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<p>عزيزي <strong>{patient.name}</strong>،</p>"
            f"<p>تم تأكيد موعدك مع <strong>{doctor_name}</strong> "
            f"بتاريخ <strong>{date_str}</strong> الساعة <strong>{time_str}</strong> "
            f"في <strong>{clinic_name}</strong>.</p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        )

        _send_email(patient.email, subject, html_content, text_content)
        logger.info(
            "[EMAIL] Booking confirmation email sent to user_id=%s email=%s",
            patient.id, patient.email,
        )
        return True

    except Exception as e:
        logger.error(
            "[EMAIL] Failed to send booking email to user_id=%s: %r",
            patient.id, e,
        )
        return False


def send_appointment_reminder_email(patient, appointment):
    """
    Send a 24-hour reminder email to the patient.

    Only sent when patient.email is present and patient.email_verified is True.
    Returns True if email was sent successfully, False otherwise.
    """
    if not patient.email or not getattr(patient, "email_verified", False):
        logger.info(
            "[EMAIL] Skipping reminder email for user_id=%s — no verified email.",
            patient.id,
        )
        return False

    try:
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        subject = "تذكير بموعدك غداً — Clinic"
        text_content = (
            f"عزيزي {patient.name}،\n\n"
            f"تذكير: لديك موعد غداً مع {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}.\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<p>عزيزي <strong>{patient.name}</strong>،</p>"
            f"<p>تذكير: لديك موعد غداً مع <strong>{doctor_name}</strong> "
            f"بتاريخ <strong>{date_str}</strong> الساعة <strong>{time_str}</strong> "
            f"في <strong>{clinic_name}</strong>.</p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        )

        _send_email(patient.email, subject, html_content, text_content)
        logger.info(
            "[EMAIL] Reminder email sent to user_id=%s email=%s",
            patient.id, patient.email,
        )
        return True

    except Exception as e:
        logger.error(
            "[EMAIL] Failed to send reminder email to user_id=%s: %r",
            patient.id, e,
        )
        return False


def send_appointment_rescheduled_email(patient, appointment, old_date, old_time):
    """
    Send a reschedule notification email to the patient.

    Only sent when patient.email is present and patient.email_verified is True.
    Returns True if email was sent successfully, False otherwise.
    """
    if not patient.email or not getattr(patient, "email_verified", False):
        logger.info(
            "[EMAIL] Skipping reschedule email for user_id=%s — no verified email.",
            patient.id,
        )
        return False

    try:
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        old_date_str = old_date.strftime("%Y-%m-%d")
        old_time_str = old_time.strftime("%H:%M")
        new_date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        new_time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        subject = "تم تعديل موعدك — Clinic"
        text_content = (
            f"عزيزي {patient.name}،\n\n"
            f"تم تعديل موعدك مع {doctor_name} في {clinic_name}.\n\n"
            f"الموعد القديم: {old_date_str} الساعة {old_time_str}\n"
            f"الموعد الجديد: {new_date_str} الساعة {new_time_str}\n\n"
            f"مع تحيات،\nفريق كلينك"
        )
        html_content = (
            f"<p>عزيزي <strong>{patient.name}</strong>،</p>"
            f"<p>تم تعديل موعدك مع <strong>{doctor_name}</strong> "
            f"في <strong>{clinic_name}</strong>.</p>"
            f"<p>الموعد القديم: <strong>{old_date_str}</strong> الساعة <strong>{old_time_str}</strong></p>"
            f"<p>الموعد الجديد: <strong>{new_date_str}</strong> الساعة <strong>{new_time_str}</strong></p>"
            f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        )

        _send_email(patient.email, subject, html_content, text_content)
        logger.info(
            "[EMAIL] Reschedule email sent to user_id=%s email=%s",
            patient.id, patient.email,
        )
        return True

    except Exception as e:
        logger.error(
            "[EMAIL] Failed to send reschedule email to user_id=%s: %r",
            patient.id, e,
        )
        return False


def send_verification_rejected_email(user, reason="", layer="identity"):
    """
    Notify a doctor that their verification has been rejected.
    """
    if not user.email:
        return

    if layer == "identity":
        subject = "مطلوب إجراء — التحقق من الهوية — كلينك"
        body = "تم رفض التحقق من هويتك على منصة كلينك."
    else:
        subject = "مطلوب إجراء — المؤهلات الطبية — كلينك"
        body = "تم رفض التحقق من مؤهلاتك الطبية."

    reason_line = f"\n\nالسبب: {reason}" if reason else ""

    text_content = (
        f"\u200fمرحباً د. {user.name}،\n\n"
        f"{body}{reason_line}\n\n"
        f"يرجى تحديث مستنداتك وإعادة التقديم.\n\n"
        f"مع تحيات،\nفريق كلينك"
    )
    html_content = (
        f"<div dir='rtl'>"
        f"<h2>مرحباً د. {user.name}،</h2>"
        f"<p>{body}</p>"
        f"{'<p><strong>السبب:</strong> ' + reason + '</p>' if reason else ''}"
        f"<p>يرجى تحديث مستنداتك وإعادة التقديم.</p>"
        f"<br><p>مع تحيات،<br>فريق كلينك</p>"
        f"</div>"
    )

    try:
        _send_email(user.email, subject, html_content, text_content)
        logger.info("[EMAIL] Verification rejected email sent to %s", user.email)
    except Exception as e:
        logger.error("[EMAIL] Failed to send rejection email to %s: %r", user.email, e)
