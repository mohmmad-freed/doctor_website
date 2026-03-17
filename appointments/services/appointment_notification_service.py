"""
Appointment Notification Service.

Central service for creating in-app and email notifications for appointment events.

Rules:
- In-app notification is ALWAYS created first; email failures never block it.
- Email is sent only if user.email is set AND user.email_verified is True.
- All failures are caught and logged — never raised to callers.
- Use logger = logging.getLogger(__name__) for structured logging.
"""

import logging

from appointments.models import AppointmentNotification

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _create_notification(patient, appointment, notification_type, title, message, cancelled_by_staff=None):
    """
    Create and persist an in-app AppointmentNotification.

    Returns the created notification, or None if creation fails.
    Failures are logged and never raised.
    """
    try:
        notification = AppointmentNotification.objects.create(
            patient=patient,
            appointment=appointment,
            notification_type=notification_type,
            title=title,
            message=message,
            cancelled_by_staff=cancelled_by_staff,
            is_delivered=True,
        )
        logger.info(
            "[NOTIFICATION] Created %s for patient_id=%s appointment_id=%s",
            notification_type, patient.id, appointment.id if appointment else None,
        )
        return notification
    except Exception as exc:
        logger.warning(
            "[NOTIFICATION] Failed to create %s for patient_id=%s appointment_id=%s: %r",
            notification_type,
            patient.id if patient else None,
            appointment.id if appointment else None,
            exc,
        )
        return None


def _try_send_email(send_fn, *args, **kwargs):
    """
    Call an email-sending function, catching all exceptions.

    Returns True if the function returned True (success), False otherwise.
    """
    try:
        return bool(send_fn(*args, **kwargs))
    except Exception as exc:
        logger.error("[EMAIL] Unexpected error in notification email: %r", exc)
        return False


# ── Public API ────────────────────────────────────────────────────────────────


def notify_appointment_booked(appointment):
    """
    Create in-app + email notification to patient when a booking is confirmed.

    Safe to call from transaction.on_commit().
    """
    try:
        patient = appointment.patient
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تم تأكيد موعدك"
        message = (
            f"تم تأكيد موعدك مع {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title=title,
            message=message,
        )

        if notification is None:
            return

        # Email (non-blocking)
        from accounts.email_utils import send_appointment_booking_email
        email_sent = _try_send_email(send_appointment_booking_email, patient, appointment)
        if email_sent and notification:
            try:
                notification.sent_via_email = True
                notification.save(update_fields=["sent_via_email"])
            except Exception as exc:
                logger.warning("[NOTIFICATION] Could not update sent_via_email: %r", exc)

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_appointment_booked failed: %r", exc)


def notify_appointment_cancelled_by_staff(appointment, clinic_staff):
    """
    Create in-app + email notification to patient when staff cancels.

    Records cancelled_by_staff for audit.
    Safe to call from transaction.on_commit().
    """
    try:
        patient = appointment.patient
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تم إلغاء موعدك"
        message = (
            f"تم إلغاء موعدك مع الدكتور {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
            title=title,
            message=message,
            cancelled_by_staff=clinic_staff,
        )

        if notification is None:
            return

        # Email (non-blocking)
        from accounts.email_utils import send_appointment_cancellation_email
        email_sent = _try_send_email(send_appointment_cancellation_email, patient, appointment)
        if email_sent and notification:
            try:
                notification.sent_via_email = True
                notification.save(update_fields=["sent_via_email"])
            except Exception as exc:
                logger.warning("[NOTIFICATION] Could not update sent_via_email: %r", exc)

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_appointment_cancelled_by_staff failed: %r", exc)


def notify_appointment_rescheduled_by_staff(appointment, old_date, old_time):
    """
    Create in-app + email notification to patient when staff reschedules.

    Safe to call from transaction.on_commit().
    """
    try:
        patient = appointment.patient
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        old_date_str = old_date.strftime("%Y-%m-%d")
        old_time_str = old_time.strftime("%H:%M")
        new_date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        new_time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تم تعديل موعدك"
        message = (
            f"تم تعديل موعدك مع {doctor_name} في {clinic_name}. "
            f"الموعد القديم: {old_date_str} الساعة {old_time_str}. "
            f"الموعد الجديد: {new_date_str} الساعة {new_time_str}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_RESCHEDULED,
            title=title,
            message=message,
        )

        if notification is None:
            return

        # Email (non-blocking)
        from accounts.email_utils import send_appointment_rescheduled_email
        email_sent = _try_send_email(
            send_appointment_rescheduled_email, patient, appointment, old_date, old_time
        )
        if email_sent and notification:
            try:
                notification.sent_via_email = True
                notification.save(update_fields=["sent_via_email"])
            except Exception as exc:
                logger.warning("[NOTIFICATION] Could not update sent_via_email: %r", exc)

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_appointment_rescheduled_by_staff failed: %r", exc)


def notify_staff_patient_cancelled(appointment):
    """
    Notify doctor + secretaries (in-app only) when a patient cancels.

    Safe to call from transaction.on_commit().
    """
    try:
        from clinics.models import ClinicStaff

        patient_name = appointment.patient.name if appointment.patient else "مريض"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")

        title = "إلغاء موعد من قبل المريض"
        message = (
            f"قام المريض {patient_name} بإلغاء موعده "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {appointment.clinic.name}."
        )

        recipients = set()
        if appointment.doctor_id:
            recipients.add(appointment.doctor_id)
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        recipients.update(secretary_ids)

        for user_id in recipients:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
                    title=title,
                    message=message,
                    is_delivered=True,
                )
            except Exception as exc:
                logger.warning(
                    "[NOTIFICATION] Could not create patient-cancel notification "
                    "for user_id=%s appointment_id=%s: %r",
                    user_id, appointment.id, exc,
                )

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_staff_patient_cancelled failed: %r", exc)


def notify_staff_patient_edited(appointment, old_date, old_time, old_type):
    """
    Notify doctor + secretaries (in-app only) when a patient edits their appointment.

    Safe to call from transaction.on_commit().
    """
    try:
        from clinics.models import ClinicStaff

        patient_name = appointment.patient.name if appointment.patient else "مريض"
        old_date_str = old_date.strftime("%Y-%m-%d")
        old_time_str = old_time.strftime("%H:%M")
        new_date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        new_time_str = appointment.appointment_time.strftime("%H:%M")

        title = "تعديل موعد من قبل المريض"
        message = (
            f"قام المريض {patient_name} بتعديل موعده "
            f"من {old_date_str} الساعة {old_time_str} "
            f"إلى {new_date_str} الساعة {new_time_str} "
            f"في {appointment.clinic.name}."
        )

        recipients = set()
        if appointment.doctor_id:
            recipients.add(appointment.doctor_id)
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        recipients.update(secretary_ids)

        for user_id in recipients:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_EDITED,
                    title=title,
                    message=message,
                    is_delivered=True,
                )
            except Exception as exc:
                logger.warning(
                    "[NOTIFICATION] Could not create patient-edit notification "
                    "for user_id=%s appointment_id=%s: %r",
                    user_id, appointment.id, exc,
                )

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_staff_patient_edited failed: %r", exc)


def notify_appointment_reminder(appointment):
    """
    Create in-app + email reminder notification to patient.

    Only creates a notification if appointment.reminder_sent is False.
    This function does NOT flip reminder_sent — the caller (management command)
    is responsible for setting it to True after calling this function.

    Safe to call from the reminder management command.
    """
    try:
        if appointment.reminder_sent:
            logger.info(
                "[NOTIFICATION] Reminder already sent for appointment_id=%s — skipping.",
                appointment.id,
            )
            return

        patient = appointment.patient
        doctor_name = appointment.doctor.name if appointment.doctor else "الطبيب"
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تذكير بموعدك"
        message = (
            f"تذكير: لديك موعد مع {doctor_name} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_REMINDER,
            title=title,
            message=message,
        )

        if notification is None:
            return

        # Email (non-blocking)
        from accounts.email_utils import send_appointment_reminder_email
        email_sent = _try_send_email(send_appointment_reminder_email, patient, appointment)
        if email_sent and notification:
            try:
                notification.sent_via_email = True
                notification.save(update_fields=["sent_via_email"])
            except Exception as exc:
                logger.warning("[NOTIFICATION] Could not update sent_via_email: %r", exc)

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_appointment_reminder failed: %r", exc)
