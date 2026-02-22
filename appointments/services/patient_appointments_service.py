"""
Patient Appointments Service.

Retrieves all appointments for a given patient user, split into:
- upcoming: combined datetime >= timezone.now() AND status in active statuses
- past:     combined datetime <  timezone.now() OR  status in terminal statuses

Classification uses django.utils.timezone.now() to correctly handle same-day
appointments whose time has already passed — they are classified as past, not
upcoming, regardless of their date.

ORM-level ordering:
- Upcoming: ascending by date then time  (soonest first)
- Past:     descending by date then time (most recent first)

Optimised with select_related + prefetch_related — zero N+1 queries.
"""

import logging
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from appointments.models import Appointment

logger = logging.getLogger(__name__)


# ── Status sets ──────────────────────────────────────────────────────────────

# Appointments that are still live/upcoming
_UPCOMING_STATUSES = (
    Appointment.Status.PENDING,
    Appointment.Status.CONFIRMED,
    Appointment.Status.CHECKED_IN,
    Appointment.Status.IN_PROGRESS,
)

# Appointments that are definitively over
_PAST_STATUSES = (
    Appointment.Status.COMPLETED,
    Appointment.Status.CANCELLED,
    Appointment.Status.NO_SHOW,
)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _base_qs(patient_user):
    """
    Return the fully-optimised base QuerySet for a patient's appointments.
    Applies select_related and prefetch_related once, shared by both branches.
    """
    return (
        Appointment.objects.filter(patient=patient_user)
        .select_related(
            "clinic",
            "doctor",
            "doctor__doctor_profile",
            "appointment_type",
        )
        .prefetch_related(
            "doctor__doctor_profile__doctor_specialties__specialty",
        )
    )


def _serialize_appointment(appointment):
    """
    Return a plain dict representation of an Appointment for template consumption.

    Relies on select_related/prefetch_related having been applied on the
    queryset — no extra queries are issued here.
    """
    doctor_name = appointment.doctor.name if appointment.doctor else "\u2014"

    # Primary specialty via prefetched doctor_profile
    doctor_specialty = ""
    if appointment.doctor:
        try:
            profile = appointment.doctor.doctor_profile
            primary = profile.primary_specialty
            if primary:
                doctor_specialty = primary.name_ar or primary.name
        except Exception:
            pass

    # Appointment type display name (Arabic preferred)
    apt_type_name = ""
    if appointment.appointment_type:
        apt_type_name = (
            appointment.appointment_type.name_ar
            or appointment.appointment_type.name
        )

    return {
        "id": appointment.id,
        "doctor_name": doctor_name,
        "doctor_specialty": doctor_specialty,
        "clinic_name": appointment.clinic.name,
        "clinic_id": appointment.clinic_id,
        "clinic_address": appointment.clinic.address,
        "appointment_type": apt_type_name,
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time,
        "status": appointment.status,
        "status_display": appointment.get_status_display(),
        "can_edit": appointment.can_patient_edit,
        "edits_remaining": appointment.edits_remaining,
    }


def _appointment_has_passed(appointment, local_now):
    """
    Return True if the appointment's scheduled datetime is strictly before now.

    The model stores date and time as separate naive fields.
    We combine them and compare against local time (naive) so that a
    same-day appointment at 09:00 when it is 10:00 is correctly marked past.
    """
    appt_naive_dt = datetime.combine(
        appointment.appointment_date, appointment.appointment_time
    )
    local_now_naive = local_now.replace(tzinfo=None)
    return appt_naive_dt < local_now_naive


# ── Public API ───────────────────────────────────────────────────────────────


def get_patient_appointments(patient_user, upcoming_limit=None, past_limit=None):
    """
    Return upcoming and past appointments for the given patient user.

    Classification is based on timezone.now() so that same-day appointments
    whose slot time has already passed are correctly categorised as past.

    Args:
        patient_user:   The User instance (role=PATIENT).
        upcoming_limit: Optional int — max upcoming appointments to return.
                        Defaults to None (return all — existing behaviour).
        past_limit:     Optional int — max past appointments to return.
                        Defaults to None (return all — existing behaviour).

    Returns:
        dict:
            "upcoming":       list of dicts, ascending by date/time.
            "past":           list of dicts, descending by date/time.
            "upcoming_count": total count before limit (for pagination UI).
            "past_count":     total count before limit (for pagination UI).
    """
    now = timezone.now()
    local_now = timezone.localtime(now)
    today = local_now.date()

    base = _base_qs(patient_user)

    # ── Upcoming branch ───────────────────────────────────────────────────────
    upcoming_qs = (
        base
        .filter(
            appointment_date__gte=today,
            status__in=_UPCOMING_STATUSES,
        )
        .order_by("appointment_date", "appointment_time")
    )

    upcoming_all = [
        _serialize_appointment(a)
        for a in upcoming_qs
        if not _appointment_has_passed(a, local_now)
    ]
    upcoming_count = len(upcoming_all)
    upcoming = upcoming_all[:upcoming_limit] if upcoming_limit is not None else upcoming_all

    # ── Past branch ───────────────────────────────────────────────────────────
    past_qs = (
        base
        .filter(
            Q(status__in=_PAST_STATUSES)
            | Q(appointment_date__lt=today, status__in=_UPCOMING_STATUSES)
        )
        .order_by("-appointment_date", "-appointment_time")
    )

    same_day_passed_qs = (
        base
        .filter(appointment_date=today, status__in=_UPCOMING_STATUSES)
        .order_by("-appointment_date", "-appointment_time")
    )

    past_from_qs = [_serialize_appointment(a) for a in past_qs]
    past_from_same_day = [
        _serialize_appointment(a)
        for a in same_day_passed_qs
        if _appointment_has_passed(a, local_now)
    ]

    # Merge, de-duplicate, re-sort descending (most recent first)
    seen_ids: set = set()
    past_all = []
    for item in sorted(
        past_from_same_day + past_from_qs,
        key=lambda x: (x["appointment_date"], x["appointment_time"]),
        reverse=True,
    ):
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            past_all.append(item)

    past_count = len(past_all)
    past = past_all[:past_limit] if past_limit is not None else past_all

    return {
        "upcoming": upcoming,
        "past": past,
        "upcoming_count": upcoming_count,
        "past_count": past_count,
    }


# ── Cancellation ─────────────────────────────────────────────────────────────

# Statuses that cannot be cancelled (appointment is already closed)
_TERMINAL_STATUSES = (
    Appointment.Status.COMPLETED,
    Appointment.Status.CANCELLED,
    Appointment.Status.NO_SHOW,
)

# Minimum hours before appointment that a patient can cancel.
# Set to 0 to allow cancellation at any time.
CANCELLATION_WINDOW_HOURS = 2


def cancel_appointment(appointment_id, patient):
    """
    Cancel a patient's own appointment.

    Security:
    - Ownership enforced at ORM level: filter(id=..., patient=patient).
      A patient can never cancel another patient's appointment.
    - Terminal-status guard prevents re-cancelling already-closed appointments.
    - Time-based policy: cannot cancel within CANCELLATION_WINDOW_HOURS of appointment.

    Notification:
    - On successful cancellation, doctor + clinic secretaries are notified
      via transaction.on_commit to ensure notification fires only after DB commit.

    Args:
        appointment_id: PK of the Appointment to cancel.
        patient:        The User instance (role=PATIENT) making the request.

    Returns:
        True on success.

    Raises:
        ValueError: Appointment not found / not owned by patient.
        ValueError: Appointment is already in a terminal state.
        ValueError: Cancellation window has passed.
    """
    try:
        appointment = Appointment.objects.select_related(
            "doctor", "clinic", "patient"
        ).get(id=appointment_id, patient=patient)
    except Appointment.DoesNotExist:
        raise ValueError("الموعد غير موجود.")

    if appointment.status in _TERMINAL_STATUSES:
        raise ValueError("لا يمكن إلغاء هذا الموعد.")

    # ── Time-based cancellation policy ────────────────────────────────────
    if CANCELLATION_WINDOW_HOURS > 0:
        local_now = timezone.localtime(timezone.now())
        appt_dt = datetime.combine(
            appointment.appointment_date, appointment.appointment_time
        )
        local_now_naive = local_now.replace(tzinfo=None)
        hours_until = (appt_dt - local_now_naive).total_seconds() / 3600

        if hours_until < CANCELLATION_WINDOW_HOURS:
            raise ValueError(
                f"لا يمكن إلغاء الموعد قبل أقل من {CANCELLATION_WINDOW_HOURS} ساعة من موعده. "
                f"يرجى التواصل مع العيادة مباشرة."
            )

    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    # ── Notify doctor + secretaries after DB commit ───────────────────────
    transaction.on_commit(
        lambda: _notify_staff_patient_cancelled(appointment)
    )

    return True


# ── Edit Appointment ─────────────────────────────────────────────────────────

MAX_PATIENT_EDITS = 2  # mirrors Appointment.MAX_PATIENT_EDITS


def edit_appointment(appointment_id, patient, new_date, new_time, new_type_id=None, new_reason=None):
    """
    Edit a patient's own appointment (date, time, type, reason).

    Rules:
    - Patient can edit up to MAX_PATIENT_EDITS times (2).
    - Cannot edit doctor (locked).
    - Only PENDING or CONFIRMED appointments can be edited.
    - New slot must be available (validated with DB lock).
    - Same time-based policy as cancellation (cannot edit within 2h).

    Returns:
        The updated Appointment instance.

    Raises:
        ValueError on any validation failure.
    """
    from doctors.services import generate_slots_for_date
    from appointments.models import AppointmentType

    try:
        appointment = Appointment.objects.select_related(
            "doctor", "clinic", "patient", "appointment_type"
        ).get(id=appointment_id, patient=patient)
    except Appointment.DoesNotExist:
        raise ValueError("الموعد غير موجود.")

    if appointment.status not in (Appointment.Status.PENDING, Appointment.Status.CONFIRMED):
        raise ValueError("لا يمكن تعديل هذا الموعد.")

    if appointment.patient_edit_count >= MAX_PATIENT_EDITS:
        raise ValueError(
            f"لقد استنفدت الحد الأقصى من التعديلات ({MAX_PATIENT_EDITS}). "
            f"يرجى التواصل مع العيادة لتعديل الموعد."
        )

    # Time-based policy
    if CANCELLATION_WINDOW_HOURS > 0:
        local_now = timezone.localtime(timezone.now())
        appt_dt = datetime.combine(
            appointment.appointment_date, appointment.appointment_time
        )
        local_now_naive = local_now.replace(tzinfo=None)
        hours_until = (appt_dt - local_now_naive).total_seconds() / 3600
        if hours_until < CANCELLATION_WINDOW_HOURS:
            raise ValueError(
                f"لا يمكن تعديل الموعد قبل أقل من {CANCELLATION_WINDOW_HOURS} ساعة من موعده. "
                f"يرجى التواصل مع العيادة مباشرة."
            )

    # Date validation
    from datetime import date as date_cls
    today = date_cls.today()
    if new_date < today:
        raise ValueError("لا يمكن اختيار تاريخ في الماضي.")
    if new_date == today:
        from datetime import datetime as dt_cls
        if new_time <= dt_cls.now().time():
            raise ValueError("لا يمكن اختيار وقت قد مضى اليوم.")

    # Appointment type validation
    doctor_id = appointment.doctor_id
    clinic_id = appointment.clinic_id
    appointment_type = appointment.appointment_type

    if new_type_id and new_type_id != (appointment_type.id if appointment_type else None):
        try:
            appointment_type = AppointmentType.objects.get(
                id=new_type_id, doctor_id=doctor_id, clinic_id=clinic_id, is_active=True,
            )
        except AppointmentType.DoesNotExist:
            raise ValueError("نوع الموعد غير صالح.")

    if not appointment_type:
        raise ValueError("لم يتم تحديد نوع الموعد.")

    # Slot validation (pre-check)
    slots = generate_slots_for_date(
        doctor_id=doctor_id, clinic_id=clinic_id,
        target_date=new_date, duration_minutes=appointment_type.duration_minutes,
    )
    matching_slot = None
    for slot in slots:
        if slot["time"] == new_time:
            matching_slot = slot
            break
    if matching_slot is None:
        raise ValueError("الوقت المحدد غير متاح ضمن جدول الطبيب.")

    is_same_slot = (new_date == appointment.appointment_date and new_time == appointment.appointment_time)
    if not matching_slot["is_available"] and not is_same_slot:
        raise ValueError("هذا الموعد محجوز بالفعل. يرجى اختيار وقت آخر.")

    # Atomic update with lock
    with transaction.atomic():
        list(
            Appointment.objects.select_for_update()
            .filter(
                doctor_id=doctor_id, appointment_date=new_date,
                status__in=[
                    Appointment.Status.CONFIRMED, Appointment.Status.CHECKED_IN,
                    Appointment.Status.IN_PROGRESS, Appointment.Status.PENDING,
                ],
            )
            .exclude(id=appointment.id)
            .values_list("appointment_time", flat=True)
        )

        slots_locked = generate_slots_for_date(
            doctor_id=doctor_id, clinic_id=clinic_id,
            target_date=new_date, duration_minutes=appointment_type.duration_minutes,
        )
        slot_locked = None
        for s in slots_locked:
            if s["time"] == new_time:
                slot_locked = s
                break
        if slot_locked is None:
            raise ValueError("الوقت المحدد غير متاح.")
        if not slot_locked["is_available"] and not is_same_slot:
            raise ValueError("هذا الموعد محجوز بالفعل. يرجى اختيار وقت آخر.")

        old_date = appointment.appointment_date
        old_time = appointment.appointment_time
        old_type = appointment.appointment_type

        appointment.appointment_date = new_date
        appointment.appointment_time = new_time
        appointment.appointment_type = appointment_type
        if new_reason is not None:
            appointment.reason = new_reason
        appointment.patient_edit_count += 1
        appointment.save(update_fields=[
            "appointment_date", "appointment_time", "appointment_type",
            "reason", "patient_edit_count", "updated_at",
        ])

    transaction.on_commit(
        lambda: _notify_staff_patient_edited(appointment, old_date, old_time, old_type)
    )

    return appointment


def _notify_staff_patient_edited(appointment, old_date, old_time, old_type):
    """Notify doctor and secretaries that a patient edited their appointment."""
    from appointments.models import AppointmentNotification
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
                notification_type="APPOINTMENT_EDITED",
                title=title,
                message=message,
                is_delivered=True,
            )
        except Exception as exc:
            logger.warning(
                "[NOTIFICATION] Could not create edit notification "
                "for user_id=%s appointment_id=%s: %r",
                user_id, appointment.id, exc,
            )
    """
    Notify the doctor and clinic secretaries that a patient cancelled.

    Called inside transaction.on_commit() — fires only after successful DB write.

    Creates in-app AppointmentNotification for:
    1. The assigned doctor (if any)
    2. All active secretaries at the appointment's clinic
    """
    from appointments.models import AppointmentNotification
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

    # 1. Notify the assigned doctor
    if appointment.doctor_id:
        recipients.add(appointment.doctor_id)

    # 2. Notify all active secretaries at this clinic
    secretary_ids = ClinicStaff.objects.filter(
        clinic=appointment.clinic,
        role="SECRETARY",
        is_active=True,
    ).values_list("user_id", flat=True)
    recipients.update(secretary_ids)

    for user_id in recipients:
        try:
            AppointmentNotification.objects.create(
                patient_id=user_id,  # recipient (doctor or secretary)
                appointment=appointment,
                notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
                title=title,
                message=message,
                is_delivered=True,
            )
        except Exception as exc:
            # UniqueConstraint or other error — log and continue
            logger.warning(
                "[NOTIFICATION] Could not create patient-cancel notification "
                "for user_id=%s appointment_id=%s: %r",
                user_id, appointment.id, exc,
            )


# ── Staff Cancellation ────────────────────────────────────────────────────────


def _build_cancellation_message(appointment):
    """
    Build a localised Arabic notification message for a cancellation.

    Returns (title, message) tuple.
    """
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
    return title, message


def _is_sms_configured():
    """
    Clean configuration gate for TweetsMS.

    Returns True only when ALL required settings are present AND
    SMS_PROVIDER is set to 'TWEETSMS'.
    """
    from django.conf import settings as django_settings

    provider = getattr(django_settings, "SMS_PROVIDER", "").upper()
    if provider != "TWEETSMS":
        return False
    return bool(
        getattr(django_settings, "TWEETSMS_API_KEY", "")
        and getattr(django_settings, "TWEETSMS_SENDER", "")
    )


def _notify_patient_cancellation(appointment, clinic_staff):
    """
    Persist an in-app notification and attempt optional email + SMS.

    Must be called inside transaction.on_commit() to fire only after
    the cancellation DB write has committed successfully.

    Channels:
    - In-app: ALWAYS created first; email/SMS failures cannot prevent it.
    - Email:   Only if patient.email is set AND patient.email_verified=True.
    - SMS:     Only if TweetsMS is configured; skipped silently otherwise.
    """
    from appointments.models import AppointmentNotification
    from accounts.email_utils import send_appointment_cancellation_email

    patient = appointment.patient
    title, message = _build_cancellation_message(appointment)

    # ── 1. In-app notification (mandatory — always first) ─────────────────────
    AppointmentNotification.objects.create(
        patient=patient,
        appointment=appointment,
        notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
        title=title,
        message=message,
        cancelled_by_staff=clinic_staff,
        is_delivered=True,
    )
    logger.info(
        "[NOTIFICATION] In-app notification created for patient_id=%s "
        "appointment_id=%s cancelled_by_staff_id=%s",
        patient.id,
        appointment.id,
        clinic_staff.id if clinic_staff else None,
    )

    # ── 2. Email (verified email only; failure never blocks in-app) ────
    try:
        send_appointment_cancellation_email(patient, appointment)
    except Exception as exc:
        logger.error(
            "[EMAIL] Unexpected error sending cancellation email to patient_id=%s: %r",
            patient.id,
            exc,
        )

    # ── 3. SMS (explicit config gate; skip silently if not configured) ─
    if not _is_sms_configured():
        logger.info(
            "[SMS] TweetsMS not configured; skipping cancellation SMS for patient_id=%s",
            patient.id,
        )
        return

    try:
        from accounts.otp_utils import _normalize_phone
        from accounts.services.tweetsms import send_sms

        phone = _normalize_phone(patient.phone)
        _, sms_message = _build_cancellation_message(appointment)
        send_sms(phone, sms_message)
        logger.info(
            "[SMS] Cancellation SMS sent to patient_id=%s phone=%s",
            patient.id,
            phone,
        )
    except Exception as exc:
        logger.error(
            "[SMS] Failed to send cancellation SMS to patient_id=%s: %r",
            patient.id,
            exc,
        )


def cancel_appointment_by_staff(appointment_id, clinic_staff):
    """
    Cancel an appointment on behalf of a ClinicStaff member.

    Security / Validation:
    - clinic_staff.clinic must match appointment.clinic (tenant isolation R-01).
    - Terminal statuses (COMPLETED, CANCELLED, NO_SHOW) cannot be cancelled again.

    Notification (via transaction.on_commit):
    - Fires exactly once after the DB commit succeeds.
    - Passes clinic_staff to the notification helper for audit.
    - In-app notification always created.
    - Email only if patient has a verified email.
    - SMS only if TweetsMS is configured.

    Args:
        appointment_id: PK of the Appointment to cancel.
        clinic_staff:   ClinicStaff instance performing the cancellation.

    Returns:
        True on success.

    Raises:
        ValueError: Appointment not found.
        ValueError: Staff does not belong to this appointment's clinic.
        ValueError: Appointment is in a terminal state.
    """
    try:
        appointment = (
            Appointment.objects.select_related("patient", "doctor", "clinic")
            .get(id=appointment_id)
        )
    except Appointment.DoesNotExist:
        raise ValueError("Appointment not found.")

    # Tenant isolation: staff can only cancel appointments at their own clinic
    if appointment.clinic_id != clinic_staff.clinic_id:
        raise ValueError(
            "You are not authorised to cancel appointments at this clinic."
        )

    if appointment.status in _TERMINAL_STATUSES:
        raise ValueError("Cannot cancel this appointment.")

    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    # Notify exactly once, after the DB commit, with clinic_staff for audit
    transaction.on_commit(
        lambda: _notify_patient_cancellation(appointment, clinic_staff)
    )

    return True