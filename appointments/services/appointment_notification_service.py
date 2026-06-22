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


def _create_notification(patient, appointment, notification_type, title, message, cancelled_by_staff=None, context_role=None, title_en="", message_en="", actor_role="", actor_name=""):
    """
    Create and persist an in-app AppointmentNotification.

    Stored bilingually: Arabic in title/message, English in title_en/message_en.

    The actor (who triggered the event) is denormalized via actor_role/actor_name
    so the notification's actor badge survives appointment deletion.

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
            actor_role=actor_role,
            actor_name=actor_name,
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


def _actor_from_staff(clinic_staff):
    """
    Map a ClinicStaff member to an (actor_role, actor_name) pair for the
    notification actor badge.

    SECRETARY → SECRETARY; DOCTOR / MAIN_DOCTOR → DOCTOR. Returns ("", "") when
    no staff member is supplied (e.g. an automatic/system transition).
    """
    if clinic_staff is None:
        return "", ""
    role = getattr(clinic_staff, "role", "")
    user = getattr(clinic_staff, "user", None)
    name = user.name if user else ""
    if role == "SECRETARY":
        return AppointmentNotification.ActorRole.SECRETARY, name
    if role in ("DOCTOR", "MAIN_DOCTOR"):
        return AppointmentNotification.ActorRole.DOCTOR, name
    return "", ""


def _actor_from_booking(appointment):
    """
    Derive (actor_role, actor_name) for a booking from appointment.created_by.

    The patient who owns the appointment → PATIENT; the appointment's doctor →
    DOCTOR; otherwise fall back to the creator's roles (SECRETARY, else
    DOCTOR/MAIN_DOCTOR → DOCTOR). Returns ("", "") when created_by is unset.
    """
    creator = getattr(appointment, "created_by", None)
    if creator is None:
        return "", ""
    if creator.id == appointment.patient_id:
        return AppointmentNotification.ActorRole.PATIENT, creator.name
    if appointment.doctor_id and creator.id == appointment.doctor_id:
        return AppointmentNotification.ActorRole.DOCTOR, creator.name
    if creator.has_role("SECRETARY"):
        return AppointmentNotification.ActorRole.SECRETARY, creator.name
    if creator.has_role("MAIN_DOCTOR") or creator.has_role("DOCTOR"):
        return AppointmentNotification.ActorRole.DOCTOR, creator.name
    return "", ""


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

        actor_role, actor_name = _actor_from_booking(appointment)
        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
            actor_role=actor_role,
            actor_name=actor_name,
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

        actor_role, actor_name = _actor_from_staff(clinic_staff)
        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
            title=title,
            message=message,
            cancelled_by_staff=clinic_staff,
            title_en=title_en,
            message_en=message_en,
            actor_role=actor_role,
            actor_name=actor_name,
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


def notify_secretaries_appointment_cancelled_by_doctor(appointment, by_staff=None):
    """
    Notify the clinic's secretaries (in-app only) when a doctor cancels an
    appointment.

    The patient is notified separately via notify_appointment_cancelled_by_staff().
    Clinic-owner notifications are intentionally not created here.

    Safe to call from transaction.on_commit().
    """
    try:
        from clinics.models import ClinicStaff

        patient_name = appointment.patient.name if appointment.patient else "مريض"
        doctor_ar, doctor_en = _doctor_names(appointment)
        date_str = appointment.appointment_date.strftime("%Y-%m-%d")
        time_str = appointment.appointment_time.strftime("%H:%M")

        title = "تم إلغاء الموعد"
        message = (
            f"قام {doctor_ar} بإلغاء موعد المريض {patient_name} "
            f"بتاريخ {date_str} الساعة {time_str}."
        )
        title_en = "Appointment Cancelled"
        message_en = (
            f"{doctor_en} cancelled patient {patient_name}'s appointment "
            f"on {date_str} at {time_str}."
        )

        actor_role, actor_name = _actor_from_staff(by_staff)
        if not actor_role:
            actor_role = AppointmentNotification.ActorRole.DOCTOR
            actor_name = appointment.doctor.name if appointment.doctor else ""

        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)

        for user_id in secretary_ids:
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    context_role=AppointmentNotification.ContextRole.SECRETARY,
                    notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
                    title=title,
                    message=message,
                    title_en=title_en,
                    message_en=message_en,
                    actor_role=actor_role,
                    actor_name=actor_name,
                    is_delivered=True,
                )
            except Exception as exc:
                logger.warning(
                    "[NOTIFICATION] Could not create doctor-cancel notification "
                    "for secretary user_id=%s appointment_id=%s: %r",
                    user_id, appointment.id, exc,
                )

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_secretaries_appointment_cancelled_by_doctor failed: %r", exc)


def notify_appointment_rescheduled_by_staff(appointment, old_date, old_time, clinic_staff=None):
    """
    Create in-app + email notification to patient when staff reschedules.

    Pass the acting ClinicStaff to record the actor badge; when omitted the
    notification shows no actor (backward compatible).

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

        actor_role, actor_name = _actor_from_staff(clinic_staff)
        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_RESCHEDULED,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
            actor_role=actor_role,
            actor_name=actor_name,
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

        title = "تم إلغاء الموعد"
        message = (
            f"قام المريض {patient_name} بإلغاء موعده "
            f"بتاريخ {date_str} الساعة {time_str}."
        )
        title_en = "Appointment Cancelled"
        message_en = (
            f"Patient {patient_name} cancelled their appointment on "
            f"{date_str} at {time_str}."
        )

        actor_role = AppointmentNotification.ActorRole.PATIENT
        actor_name = patient_name if appointment.patient else ""

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        # The clinic owner is intentionally NOT notified about appointment events
        # (booked/cancelled/edited) — the owner notification center is reserved for
        # business events such as purchase requests. If the owner is also the
        # appointment's doctor they still get the DOCTOR-context notification above.

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
                    actor_role=actor_role,
                    actor_name=actor_name,
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

        actor_role = AppointmentNotification.ActorRole.PATIENT
        actor_name = patient_name if appointment.patient else ""

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        # The clinic owner is intentionally NOT notified about appointment events.

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
                    actor_role=actor_role,
                    actor_name=actor_name,
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


def notify_staff_appointment_booked(appointment, exclude_user_ids=None, actor_role=None):
    """
    Notify doctor + secretaries + clinic owner (in-app only) when a new
    appointment is booked.

    Title/message differentiate between PENDING (needs review) and CONFIRMED.

    Pass exclude_user_ids to skip specific recipients — e.g. a doctor booking
    their own follow-up should not be notified about their own action.

    Pass actor_role to force the actor flag from the booking *flow/portal* rather
    than inferring it from created_by. This is required because created_by alone
    is ambiguous for multi-role users (e.g. a doctor who is also a secretary,
    booking via the secretary portal and selecting themselves as the doctor —
    that must read as SECRETARY, not DOCTOR). The actor name is always taken from
    created_by. When actor_role is omitted, the role is inferred via
    _actor_from_booking().

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

        if actor_role:
            creator = getattr(appointment, "created_by", None)
            actor_name = creator.name if creator else ""
        else:
            actor_role, actor_name = _actor_from_booking(appointment)
        exclude = set(exclude_user_ids or [])

        recipients = []
        if appointment.doctor_id:
            recipients.append((appointment.doctor_id, AppointmentNotification.ContextRole.DOCTOR))
        secretary_ids = ClinicStaff.objects.filter(
            clinic=appointment.clinic, role="SECRETARY", is_active=True,
        ).values_list("user_id", flat=True)
        for s_id in secretary_ids:
            recipients.append((s_id, AppointmentNotification.ContextRole.SECRETARY))

        # The clinic owner is intentionally NOT notified about appointment events.

        for user_id, ctx_role in recipients:
            if user_id in exclude:
                continue
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
                    actor_role=actor_role,
                    actor_name=actor_name,
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

        actor_role, actor_name = _actor_from_staff(by_staff)
        notification = _create_notification(
            patient=patient,
            appointment=appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_STATUS_CHANGED,
            title=title,
            message=message,
            cancelled_by_staff=by_staff,
            title_en=title_en,
            message_en=message_en,
            actor_role=actor_role,
            actor_name=actor_name,
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


def notify_staff_note(note, actor_user):
    """
    Notify the right audience (in-app only) when staff adds a StaffNote.

    The author may be a secretary OR a doctor (note.author_role). The notification
    title is neutral ("New Note") — who wrote it is shown by the actor badge, not
    repeated in the title.

    Audience rules:
      - DOCTOR_PRIVATE note → nobody (visible to the authoring doctor only).
      - SECRETARY note → all active secretaries of the clinic EXCEPT the author.
      - DOCTOR note (the doctor↔secretary shared channel) → notify the *other* party:
          * authored by a secretary → the appointment's doctor, or (patient-scoped) the
            patient's *treating* doctors (the author is not excluded, so a secretary's
            doctor-note still reaches the doctor even when one person holds both roles).
          * authored by a doctor → all active secretaries EXCEPT the author (it's the
            doctor's "note for secretaries").

    Each notification carries subject_patient so patient-scoped notes (no appointment)
    can still route to the patient profile.

    Safe to call from transaction.on_commit(). Failures are logged, never raised.
    """
    try:
        from appointments.models import Appointment
        from clinics.models import ClinicStaff

        clinic = note.clinic
        appointment = note.appointment  # may be None
        patient = note.patient
        author_id = actor_user.id if actor_user else note.author_id
        actor_name = actor_user.name if actor_user else (note.author_name or "")
        author_is_doctor = note.author_role == "DOCTOR"
        actor_role = (
            AppointmentNotification.ActorRole.DOCTOR if author_is_doctor
            else AppointmentNotification.ActorRole.SECRETARY
        )
        patient_name = patient.name if patient else "المريض"

        # Private doctor notes are visible only to their author → notify nobody.
        if note.audience == note.Audience.DOCTOR_PRIVATE:
            return

        if note.audience == note.Audience.SECRETARY:
            context_role = AppointmentNotification.ContextRole.SECRETARY
            notification_type = AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
            title = "ملاحظة داخلية جديدة"
            message = (
                f"أضاف {actor_name} ملاحظة للسكرتارية بخصوص المريض {patient_name}."
            )
            title_en = "New Internal Note"
            message_en = (
                f"{actor_name} added a secretaries-only note about patient {patient_name}."
            )
            recipient_ids = list(
                ClinicStaff.objects.filter(
                    clinic=clinic, role="SECRETARY", is_active=True,
                ).values_list("user_id", flat=True)
            )
            exclude_author = True  # don't notify the author of their own internal note
        elif author_is_doctor:
            # DOCTOR audience written by a doctor = a "note for secretaries" → notify them.
            context_role = AppointmentNotification.ContextRole.SECRETARY
            notification_type = AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
            title = "ملاحظة جديدة للسكرتارية"
            message = (
                f"أضاف {actor_name} ملاحظة للسكرتارية بخصوص المريض {patient_name}."
            )
            title_en = "New Note for Secretaries"
            message_en = (
                f"{actor_name} added a note for the secretaries about patient {patient_name}."
            )
            recipient_ids = list(
                ClinicStaff.objects.filter(
                    clinic=clinic, role="SECRETARY", is_active=True,
                ).values_list("user_id", flat=True)
            )
            # Don't exclude the author: a multi-role doctor-secretary (e.g. a sole-operator
            # MAIN_DOCTOR who also runs reception) must still receive this in their SECRETARY
            # context — it's a different portal. Mirrors the secretary→doctor branch below.
            exclude_author = False
        else:
            # DOCTOR audience written by a secretary = a note for the doctor.
            context_role = AppointmentNotification.ContextRole.DOCTOR
            notification_type = AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
            title = "ملاحظة جديدة"
            message = (
                f"أضاف {actor_name} ملاحظة بخصوص المريض {patient_name}."
            )
            title_en = "New Note"
            message_en = (
                f"{actor_name} added a note about patient {patient_name}."
            )
            if appointment is not None and appointment.doctor_id:
                recipient_ids = [appointment.doctor_id]
            else:
                # Patient-scoped: notify the patient's treating doctors at this clinic.
                recipient_ids = list(
                    Appointment.objects.filter(
                        clinic=clinic, patient=patient, doctor__isnull=False,
                    ).values_list("doctor_id", flat=True).distinct()
                )
            # A secretary's doctor-note must reach the doctor (even if multi-role same user).
            exclude_author = False

        seen = set()
        for user_id in recipient_ids:
            if user_id in seen:
                continue
            seen.add(user_id)
            if exclude_author and author_id is not None and user_id == author_id:
                continue
            try:
                AppointmentNotification.objects.create(
                    patient_id=user_id,
                    appointment=appointment,
                    subject_patient=patient,
                    context_role=context_role,
                    notification_type=notification_type,
                    title=title,
                    message=message,
                    title_en=title_en,
                    message_en=message_en,
                    actor_role=actor_role,
                    actor_name=actor_name,
                    is_delivered=True,
                )
            except Exception as exc:
                logger.warning(
                    "[NOTIFICATION] Could not create staff-note notification "
                    "for user_id=%s note_id=%s: %r",
                    user_id, getattr(note, "id", None), exc,
                )

    except Exception as exc:
        logger.error("[NOTIFICATION] notify_staff_note failed: %r", exc)


# ── Procurement (purchase request) notifications ──────────────────────────────


def notify_owner_purchase_request_submitted(purchase_request):
    """
    Notify the clinic owner that a secretary submitted a purchase request.

    Routed to the owner's CLINIC_OWNER portal context. The actor badge shows the
    secretary who submitted it. Safe to call from transaction.on_commit().
    Failures are logged, never raised.
    """
    try:
        clinic = purchase_request.clinic
        owner_id = clinic.main_doctor_id
        if not owner_id:
            return

        secretary = purchase_request.requested_by
        actor_name = secretary.name if secretary else ""
        title = "طلب شراء جديد بانتظار المراجعة"
        message = (
            f"قدّم {actor_name} طلب شراء جديد: {purchase_request.title} "
            f"(الإجمالي ₪{purchase_request.total}). يرجى المراجعة والموافقة أو الرفض."
        )
        title_en = "New Purchase Request Pending Review"
        message_en = (
            f"{actor_name} submitted a purchase request: {purchase_request.title} "
            f"(total ₪{purchase_request.total}). Please review and approve or reject."
        )

        AppointmentNotification.objects.create(
            patient_id=owner_id,
            appointment=None,
            purchase_request=purchase_request,
            context_role=AppointmentNotification.ContextRole.CLINIC_OWNER,
            notification_type=AppointmentNotification.Type.PURCHASE_REQUEST_SUBMITTED,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
            actor_role=AppointmentNotification.ActorRole.SECRETARY,
            actor_name=actor_name,
            is_delivered=True,
        )
        logger.info(
            "[NOTIFICATION] Purchase request %s submitted → owner_id=%s",
            getattr(purchase_request, "id", None), owner_id,
        )
    except Exception as exc:
        logger.error("[NOTIFICATION] notify_owner_purchase_request_submitted failed: %r", exc)


def notify_secretary_purchase_request_reviewed(purchase_request):
    """
    Notify the secretary that the owner approved or rejected their purchase request.

    Routed to the secretary's SECRETARY portal context. The owner's note is embedded
    in the message so the secretary reads the decision feedback inline. The type
    reflects the outcome (APPROVED/REJECTED). Safe to call from
    transaction.on_commit(). Failures are logged, never raised.
    """
    try:
        secretary_id = purchase_request.requested_by_id
        if not secretary_id:
            return

        reviewer = purchase_request.reviewed_by
        actor_name = reviewer.name if reviewer else ""
        owner_note = (purchase_request.owner_note or "").strip()
        approved = purchase_request.status == purchase_request.Status.APPROVED

        if approved:
            notification_type = AppointmentNotification.Type.PURCHASE_REQUEST_APPROVED
            title = "تمت الموافقة على طلب الشراء"
            message = f"تمت الموافقة على طلب الشراء: {purchase_request.title}."
            title_en = "Purchase Request Approved"
            message_en = f"Your purchase request was approved: {purchase_request.title}."
        else:
            notification_type = AppointmentNotification.Type.PURCHASE_REQUEST_REJECTED
            title = "تم رفض طلب الشراء"
            message = f"تم رفض طلب الشراء: {purchase_request.title}."
            title_en = "Purchase Request Rejected"
            message_en = f"Your purchase request was rejected: {purchase_request.title}."

        if owner_note:
            message += f" ملاحظة المالك: {owner_note}"
            message_en += f" Owner's note: {owner_note}"

        AppointmentNotification.objects.create(
            patient_id=secretary_id,
            appointment=None,
            purchase_request=purchase_request,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            notification_type=notification_type,
            title=title,
            message=message,
            title_en=title_en,
            message_en=message_en,
            actor_role=AppointmentNotification.ActorRole.OWNER,
            actor_name=actor_name,
            is_delivered=True,
        )
        logger.info(
            "[NOTIFICATION] Purchase request %s reviewed (%s) → secretary_id=%s",
            getattr(purchase_request, "id", None), purchase_request.status, secretary_id,
        )
    except Exception as exc:
        logger.error("[NOTIFICATION] notify_secretary_purchase_request_reviewed failed: %r", exc)
