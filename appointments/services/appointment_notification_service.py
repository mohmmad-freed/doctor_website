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


def _create_notification(patient, appointment, notification_type, title, message, cancelled_by_staff=None, context_role=None, title_en="", message_en=""):
    """
    Create and persist an in-app AppointmentNotification.

    Stored bilingually: Arabic in title/message, English in title_en/message_en.

    Returns the created notification, or None if creation fails.
    Failures are logged and never raised.
    """
    if context_role is None:
        context_role = AppointmentNotification.ContextRole.PATIENT

    try:
        notification = AppointmentNotification.objects.create(
            patient=patient,
            appointment=appointment,
            context_role=context_role,
            notification_type=notification_type,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
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


def _doctor_names(appointment):
    """Return (arabic, english) doctor display names with title prefix."""
    doc = appointment.doctor
    if doc and doc.name:
        return f"د. {doc.name}", f"Dr. {doc.name}"
    return "الطبيب", "the doctor"


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
    Create in-app + email notification to patient when a booking is created.

    Differentiates between CONFIRMED and PENDING status in title/message.

    Safe to call from transaction.on_commit().
    """
    try:
        from appointments.models import Appointment as _Appt

        patient = appointment.patient
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        if appointment.status == _Appt.Status.PENDING:
            title = "تم استلام طلب الحجز"
            message = (
                f"تم استلام طلب حجز موعد مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str} "
                f"في {clinic_name}. الموعد قيد المراجعة من السكرتارية."
            )
            title_en = "Booking Request Received"
            message_en = (
                f"Your booking request with {doctor_en} on {date_str} "
                f"at {time_str} at {clinic_name} has been received. "
                f"It is under review by the secretary."
            )
        else:
            title = "تم تأكيد موعدك"
            message = (
                f"تم تأكيد موعدك مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str} "
                f"في {clinic_name}."
            )
            title_en = "Your Appointment Is Confirmed"
            message_en = (
                f"Your appointment with {doctor_en} on {date_str} "
                f"at {time_str} at {clinic_name} has been confirmed."
            )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
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
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تم إلغاء موعدك"
        message = (
            f"تم إلغاء موعدك مع {doctor_ar} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}."
        )
        title_en = "Your Appointment Was Cancelled"
        message_en = (
            f"Your appointment with {doctor_en} on {date_str} "
            f"at {time_str} at {clinic_name} has been cancelled."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
            title=title,
            message=message,
            cancelled_by_staff=clinic_staff,
            title_en=title_en,
            message_en=message_en,
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
        doctor_ar, doctor_en = _doctor_names(appointment)
        old_date_str = old_date.strftime("%Y-%m-%d")
        old_time_str = old_time.strftime("%H:%M")
        new_date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        new_time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تم تعديل موعدك"
        message = (
            f"تم تعديل موعدك مع {doctor_ar} في {clinic_name}. "
            f"الموعد القديم: {old_date_str} الساعة {old_time_str}. "
            f"الموعد الجديد: {new_date_str} الساعة {new_time_str}."
        )
        title_en = "Your Appointment Was Rescheduled"
        message_en = (
            f"Your appointment with {doctor_en} at {clinic_name} was "
            f"rescheduled. Old: {old_date_str} at {old_time_str}. "
            f"New: {new_date_str} at {new_time_str}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_RESCHEDULED,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
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
            f"بتاريخ {date_str} الساعة {time_str}."
        )
        title_en = "Appointment Cancelled by Patient"
        message_en = (
            f"Patient {patient_name} cancelled their appointment on "
            f"{date_str} at {time_str}."
        )

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        # Notify the Clinic Owner (MAIN_DOCTOR) under CLINIC_OWNER context,
        # but only if they are not already the doctor on this appointment
        # (to avoid sending them a duplicate DOCTOR + CLINIC_OWNER pair).
        owner_id = appointment.clinic.main_doctor_id
        if owner_id and owner_id != appointment.doctor_id:
            recipients.append((owner_id, AppointmentNotification.ContextRole.CLINIC_OWNER))

        for user_id, ctx_role in recipients:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    context_role=ctx_role,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
                    title=title,
                    message=message,
                    title_en=title_en,
                    message_en=message_en,
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
            f"إلى {new_date_str} الساعة {new_time_str}."
        )
        title_en = "Appointment Edited by Patient"
        message_en = (
            f"Patient {patient_name} changed their appointment from "
            f"{old_date_str} at {old_time_str} to {new_date_str} "
            f"at {new_time_str}."
        )

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        # Notify the Clinic Owner under CLINIC_OWNER context.
        owner_id = appointment.clinic.main_doctor_id
        if owner_id and owner_id != appointment.doctor_id:
            recipients.append((owner_id, AppointmentNotification.ContextRole.CLINIC_OWNER))

        for user_id, ctx_role in recipients:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    context_role=ctx_role,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_EDITED,
                    title=title,
                    message=message,
                    title_en=title_en,
                    message_en=message_en,
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
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        title = "تذكير بموعدك"
        message = (
            f"تذكير: لديك موعد مع {doctor_ar} "
            f"بتاريخ {date_str} الساعة {time_str} "
            f"في {clinic_name}."
        )
        title_en = "Appointment Reminder"
        message_en = (
            f"Reminder: you have an appointment with {doctor_en} on "
            f"{date_str} at {time_str} at {clinic_name}."
        )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_REMINDER,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
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


def notify_staff_appointment_booked(appointment):
    """
    Notify doctor + secretaries + clinic owner (in-app only) when a new
    appointment is booked.

    Title/message differentiate between PENDING (needs review) and CONFIRMED.

    Safe to call from transaction.on_commit().
    """
    try:
        from appointments.models import Appointment as _Appt
        from clinics.models import ClinicStaff

        patient_name = appointment.patient.name if appointment.patient else "مريض"
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")

        is_pending = appointment.status == _Appt.Status.PENDING
        if is_pending:
            title = "حجز جديد بانتظار المراجعة"
            message = (
                f"قام المريض {patient_name} بحجز موعد مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str}. "
                f"الحجز قيد الانتظار ويحتاج إلى تأكيد."
            )
            title_en = "New Booking — Pending Review"
            message_en = (
                f"Patient {patient_name} booked an appointment with "
                f"{doctor_en} on {date_str} at {time_str}. "
                f"The booking is pending and needs confirmation."
            )
        else:
            title = "حجز موعد جديد"
            message = (
                f"تم حجز موعد جديد للمريض {patient_name} مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str}."
            )
            title_en = "New Appointment Booked"
            message_en = (
                f"A new appointment was booked for patient {patient_name} "
                f"with {doctor_en} on {date_str} at {time_str}."
            )

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        owner_id = appointment.clinic.main_doctor_id
        if owner_id and owner_id != appointment.doctor_id:
            recipients.append((owner_id, AppointmentNotification.ContextRole.CLINIC_OWNER))

        for user_id, ctx_role in recipients:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    context_role=ctx_role,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
                    title=title,
                    message=message,
                    title_en=title_en,
                    message_en=message_en,
                    is_delivered=True,
                )
            except Exception as exc:
                logger.warning(
                    "[NOTIFICATION] Could not create staff-booked notification "
                    "for user_id=%s appointment_id=%s: %r",
                    user_id, appointment.id, exc,
                )

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_staff_appointment_booked failed: %r", exc)


def notify_patient_status_changed(appointment, old_status, new_status, by_staff=None):
    """
    Notify the patient (in-app + email if email_verified) that staff changed
    their appointment status — typically PENDING → CONFIRMED.

    Caller is responsible for filtering to the transitions they want surfaced
    (e.g. PENDING → CONFIRMED). Cancellations and reschedules have their own
    dedicated notifiers.

    Safe to call from transaction.on_commit().
    """
    try:
        from appointments.models import Appointment as _Appt

        patient = appointment.patient
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")
        clinic_name = appointment.clinic.name

        if new_status == _Appt.Status.CONFIRMED:
            title = "تم تأكيد موعدك"
            message = (
                f"تم تأكيد موعدك مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str} "
                f"في {clinic_name}."
            )
            title_en = "Your Appointment Is Confirmed"
            message_en = (
                f"Your appointment with {doctor_en} on {date_str} "
                f"at {time_str} at {clinic_name} has been confirmed."
            )
        else:
            title = "تم تحديث حالة موعدك"
            new_label = appointment.get_status_display() if hasattr(appointment, "get_status_display") else new_status
            message = (
                f"تم تحديث حالة موعدك مع {doctor_ar} "
                f"بتاريخ {date_str} الساعة {time_str} "
                f"في {clinic_name} إلى: {new_label}."
            )
            title_en = "Your Appointment Status Was Updated"
            message_en = (
                f"Your appointment with {doctor_en} on {date_str} "
                f"at {time_str} at {clinic_name} was updated to: {new_label}."
            )

        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_STATUS_CHANGED,
            title=title,
            message=message,
            cancelled_by_staff=by_staff,
            title_en=title_en,
            message_en=message_en,
        )

        if notification is None:
            return

        # Email (non-blocking) — reuse the booking-confirmation email template
        # for PENDING → CONFIRMED so the patient sees the same details as a
        # freshly-confirmed booking. For other transitions, only in-app is sent.
        if (
            old_status == _Appt.Status.PENDING
            and new_status == _Appt.Status.CONFIRMED
        ):
            from accounts.email_utils import send_appointment_booking_email
            email_sent = _try_send_email(
                send_appointment_booking_email, patient, appointment
            )
            if email_sent:
                try:
                    notification.sent_via_email = True
                    notification.save(update_fields=["sent_via_email"])
                except Exception as exc:
                    logger.warning("[NOTIFICATION] Could not update sent_via_email: %r", exc)

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_patient_status_changed failed: %r", exc)
