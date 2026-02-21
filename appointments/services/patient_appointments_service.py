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

from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from appointments.models import Appointment


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
        "clinic_address": appointment.clinic.address,
        "appointment_type": apt_type_name,
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time,
        "status": appointment.status,
        "status_display": appointment.get_status_display(),
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
    # ORM pre-filter: on-or-after today AND active status (fast DB filter).
    # Python post-filter: exclude same-day appointments whose time has passed.
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
    # Past = terminal status (any date) OR active status with a date already past
    # (whole days behind today) OR same-day active appointment whose time has passed.
    past_qs = (
        base
        .filter(
            Q(status__in=_PAST_STATUSES)
            | Q(appointment_date__lt=today, status__in=_UPCOMING_STATUSES)
        )
        .order_by("-appointment_date", "-appointment_time")
    )

    # Same-day rows with active status that have now passed in time
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


def cancel_appointment(appointment_id, patient):
    """
    Cancel a patient's own appointment.

    Security:
    - Ownership enforced at ORM level: filter(id=..., patient=patient).
      A patient can never cancel another patient's appointment.
    - Terminal-status guard prevents re-cancelling already-closed appointments.

    Args:
        appointment_id: PK of the Appointment to cancel.
        patient:        The User instance (role=PATIENT) making the request.

    Returns:
        True on success.

    Raises:
        ValueError: Appointment not found / not owned by patient.
        ValueError: Appointment is already in a terminal state.
    """
    try:
        appointment = Appointment.objects.get(id=appointment_id, patient=patient)
    except Appointment.DoesNotExist:
        raise ValueError("Appointment not found.")

    if appointment.status in _TERMINAL_STATUSES:
        raise ValueError("Cannot cancel this appointment.")

    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status"])

    return True


# ── Staff Cancellation ────────────────────────────────────────────────────────

import logging
from django.db import transaction

logger = logging.getLogger(__name__)


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
    FIX 4: Clean configuration gate for TweetsMS.

    Returns True only when ALL required settings are present AND
    SMS_PROVIDER is set to 'TWEETSMS'.  Matches the pattern used
    by accounts/otp_utils._is_using_tweetsms().
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

    FIX 2: clinic_staff is stored in cancelled_by_staff for audit.

    Channels:
    - In-app: ALWAYS created first; email/SMS failures cannot prevent it.
    - Email:   Only if patient.email is set AND patient.email_verified=True.
    - SMS:     Only if TweetsMS is configured (SMS_PROVIDER=TWEETSMS +
               TWEETSMS_API_KEY + TWEETSMS_SENDER); skipped silently otherwise.
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
        cancelled_by_staff=clinic_staff,  # FIX 2: audit field
        is_delivered=True,
    )
    logger.info(
        "[NOTIFICATION] In-app notification created for patient_id=%s "
        "appointment_id=%s cancelled_by_staff_id=%s",
        patient.id,
        appointment.id,
        clinic_staff.id if clinic_staff else None,
    )

    # ── 2. Email (FIX 5: verified email only; failure never blocks in-app) ────
    try:
        send_appointment_cancellation_email(patient, appointment)
    except Exception as exc:
        logger.error(
            "[EMAIL] Unexpected error sending cancellation email to patient_id=%s: %r",
            patient.id,
            exc,
        )

    # ── 3. SMS (FIX 4: explicit config gate; skip silently if not configured) ─
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
        # Network issue or provider error — log and continue
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

    Notification (via transaction.on_commit — FIX 6):
    - Fires exactly once after the DB commit succeeds.
    - Passes clinic_staff to the notification helper for audit (FIX 2).
    - In-app notification always created.
    - Email only if patient has a verified email (FIX 5).
    - SMS only if TweetsMS is configured (FIX 4).

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

    # FIX 6: notify exactly once, after the DB commit, with clinic_staff for audit
    transaction.on_commit(
        lambda: _notify_patient_cancellation(appointment, clinic_staff)
    )

    return True
