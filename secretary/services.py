"""
Secretary-side business logic layer.

Provides appointment booking and status transition functions that bypass
patient-portal restrictions (doctor platform verification, subscription
quota checks) since all operations are performed by clinic staff.
"""

from datetime import date as date_cls

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.db.models import Max

from appointments.models import Appointment, AppointmentType
from appointments.services.booking_service import BookingError, SlotUnavailableError


def _next_queue_priority(clinic_id, today):
    """Return the next queue_priority value to assign for a clinic's queue (max + 1, min 1)."""
    result = Appointment.objects.filter(
        clinic_id=clinic_id,
        appointment_date=today,
        status=Appointment.Status.CHECKED_IN,
        queue_priority__isnull=False,
    ).aggregate(mx=Max("queue_priority"))
    return (result["mx"] or 0) + 1


# ── Valid status transitions ──────────────────────────────────────────────────

VALID_TRANSITIONS = {
    Appointment.Status.PENDING: [
        Appointment.Status.CONFIRMED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ],
    Appointment.Status.CONFIRMED: [
        Appointment.Status.CHECKED_IN,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ],
    Appointment.Status.CHECKED_IN: [
        Appointment.Status.IN_PROGRESS,
        Appointment.Status.CONFIRMED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ],
    Appointment.Status.IN_PROGRESS: [
        Appointment.Status.COMPLETED,
        Appointment.Status.NO_SHOW,
    ],
    # Terminal states — no transitions allowed
    Appointment.Status.COMPLETED: [],
    Appointment.Status.CANCELLED: [],
    Appointment.Status.NO_SHOW: [],
}

# Human-readable labels for validation error messages
_STATUS_LABELS = dict(Appointment.Status.choices)


def get_valid_transitions(current_status: str) -> list[str]:
    """Return list of valid target statuses from the given current status."""
    return VALID_TRANSITIONS.get(current_status, [])


def transition_appointment_status(
    appointment: "Appointment",
    new_status: str,
    cancellation_reason: str = "",
    actor=None,
) -> "Appointment":
    """
    Apply a status transition to an appointment.

    Rules:
    - Only transitions defined in VALID_TRANSITIONS are allowed.
    - CANCELLED requires a non-blank cancellation_reason.
    - CHECKED_IN sets checked_in_at timestamp automatically.

    Args:
        appointment: The Appointment instance to update.
        new_status: Target status string (must be in Appointment.Status choices).
        cancellation_reason: Required when new_status == CANCELLED.
        actor: The User performing the action (unused now, reserved for audit log).

    Returns:
        The updated appointment instance.

    Raises:
        BookingError: If the transition is invalid.
    """
    current = appointment.status
    allowed = VALID_TRANSITIONS.get(current, [])

    if new_status not in allowed:
        current_label = _STATUS_LABELS.get(current, current)
        new_label = _STATUS_LABELS.get(new_status, new_status)
        raise BookingError(
            f"لا يمكن تحويل الموعد من «{current_label}» إلى «{new_label}»."
        )

    if new_status == Appointment.Status.CANCELLED and not cancellation_reason.strip():
        raise BookingError("يرجى ذكر سبب الإلغاء.")

    update_fields = ["status", "updated_at"]
    appointment.status = new_status

    if new_status == Appointment.Status.CHECKED_IN:
        appointment.checked_in_at = timezone.now()
        update_fields.append("checked_in_at")

    if (
        new_status == Appointment.Status.CONFIRMED
        and current == Appointment.Status.CHECKED_IN
    ):
        appointment.checked_in_at = None
        appointment.queue_priority = None
        update_fields.extend(["checked_in_at", "queue_priority"])

    if new_status == Appointment.Status.CANCELLED:
        appointment.cancellation_reason = cancellation_reason.strip()
        update_fields.append("cancellation_reason")

    appointment.save(update_fields=update_fields)

    # Keep any open billing session in lockstep with the appointment: lock charges
    # on COMPLETED, auto-void an untouched session on CANCELLED/NO_SHOW. Never raises.
    from secretary import billing
    billing.on_appointment_status_changed(appointment, new_status)

    # Notify the patient that their appointment status changed.
    # Cancellations are intentionally skipped here — callers handle them via
    # the dedicated notify_appointment_cancelled_by_staff() which records
    # cancelled_by_staff for audit. Likewise reschedules go through their
    # own notifier. We only fire here for transitions the patient cares
    # about that aren't already covered: PENDING → CONFIRMED is the
    # primary case (secretary approves a pending booking).
    notify_transitions = {
        (Appointment.Status.PENDING, Appointment.Status.CONFIRMED),
    }
    if (current, new_status) in notify_transitions:
        from appointments.services.appointment_notification_service import (
            notify_patient_status_changed,
        )
        old_status = current  # captured before mutation
        transaction.on_commit(
            lambda: notify_patient_status_changed(
                appointment, old_status, new_status
            )
        )

    return appointment


def secretary_book_appointment(
    *,
    patient,
    doctor_id: int,
    clinic_id: int,
    appointment_type_id: int,
    appointment_date,
    appointment_time,
    reason: str = "",
    notes: str = "",
    secretary_note: str = "",
    doctor_note: str = "",
    status: str = Appointment.Status.CONFIRMED,
    created_by,
    is_walk_in: bool = False,
) -> "Appointment":
    """
    Create an appointment on behalf of a patient, from the secretary's interface.

    Differences from the patient-side book_appointment():
    - Skips doctor platform-verification check (doctor is already vetted by clinic).
    - Skips subscription quota check (internal clinic operation).
    - Allows CHECKED_IN as initial status (walk-in registrations).
    - Sets created_by to the secretary user.

    Walk-ins (is_walk_in=True) skip the slot-conflict check entirely and don't
    block other appointments on the same slot — they live in the waiting queue,
    not on the booked-slot grid.
    """
    from clinics.models import Clinic, ClinicStaff

    # 1. Patient must be a patient
    if not patient.has_role("PATIENT"):
        raise BookingError("المستخدم المحدد ليس مريضاً في النظام.")

    # 2. Date validation
    today = date_cls.today()
    if appointment_date < today:
        raise BookingError("لا يمكن حجز موعد في تاريخ ماضٍ.")

    # 3. Clinic
    try:
        clinic = Clinic.objects.get(id=clinic_id, is_active=True)
    except Clinic.DoesNotExist:
        raise BookingError("العيادة غير موجودة أو غير نشطة.")

    # 4. Doctor active at clinic
    is_staff = ClinicStaff.objects.filter(
        clinic=clinic,
        user_id=doctor_id,
        role__in=["DOCTOR", "MAIN_DOCTOR"],
        is_active=True,
    ).exists()
    is_owner = clinic.main_doctor_id == doctor_id
    if not is_staff and not is_owner:
        raise BookingError("الطبيب المحدد غير نشط في هذه العيادة.")

    # 4b. Doctor cannot be booked as their own patient
    if patient.id == doctor_id:
        raise BookingError(_("لا يمكن حجز موعد للطبيب مع نفسه. الرجاء اختيار طبيب آخر."))

    # 5. Appointment type
    try:
        appt_type = AppointmentType.objects.get(
            id=appointment_type_id, clinic_id=clinic_id, is_active=True
        )
    except AppointmentType.DoesNotExist:
        raise BookingError("نوع الموعد المحدد غير موجود أو غير مفعّل في هذه العيادة.")

    # 6. Slot conflict check with row-level lock
    # Walk-ins are excluded from both sides: they don't reserve a slot, and an
    # existing walk-in shouldn't block a real booking (or another walk-in).
    with transaction.atomic():
        if not is_walk_in:
            conflict = (
                Appointment.objects.select_for_update()
                .filter(
                    doctor_id=doctor_id,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    status__in=[
                        Appointment.Status.PENDING,
                        Appointment.Status.CONFIRMED,
                        Appointment.Status.CHECKED_IN,
                        Appointment.Status.IN_PROGRESS,
                    ],
                    is_walk_in=False,
                )
                .exists()
            )
            if conflict:
                raise SlotUnavailableError(
                    "هذا الوقت محجوز بالفعل لدى هذا الطبيب. يرجى اختيار وقت آخر."
                )

        appointment = Appointment.objects.create(
            patient=patient,
            clinic=clinic,
            doctor_id=doctor_id,
            appointment_type=appt_type,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status=status,
            reason=reason,
            notes=notes,
            secretary_note=secretary_note,
            doctor_note=doctor_note,
            created_by=created_by,
            is_walk_in=is_walk_in,
        )

        # Notify other clinic staff after commit (skip walk-ins — those are
        # already physically present and have their own queue UI).
        if not is_walk_in:
            from appointments.models import AppointmentNotification
            from appointments.services.appointment_notification_service import (
                notify_staff_appointment_booked,
            )
            # Booked via the secretary portal → always flag as SECRETARY, even if
            # the booking user also holds a doctor role / is the appointment's doctor.
            transaction.on_commit(
                lambda: notify_staff_appointment_booked(
                    appointment,
                    actor_role=AppointmentNotification.ActorRole.SECRETARY,
                )
            )

    return appointment


def register_walk_in(
    *,
    patient,
    doctor_id: int,
    clinic_id: int,
    appointment_type_id: int,
    created_by,
    reason: str = "",
    notes: str = "",
    secretary_note: str = "",
    doctor_note: str = "",
    override_same_day_conflict: bool = False,
) -> "Appointment":
    """
    Register a walk-in patient: appointment for today/now, status CHECKED_IN,
    is_walk_in=True. Patient is added to the waiting-room queue immediately.

    Guards:
    - Hard block if the patient is already actively in today's walk-in queue.
    - Block (overridable) if the patient has an active booked appointment today.
      Future-day bookings never block — they're informational.
    """
    today = date_cls.today()

    # Hard block: patient already in walk-in queue today
    already_in_queue = Appointment.objects.filter(
        clinic_id=clinic_id,
        patient=patient,
        is_walk_in=True,
        appointment_date=today,
        status__in=[
            Appointment.Status.CHECKED_IN,
            Appointment.Status.IN_PROGRESS,
        ],
    ).exists()
    if already_in_queue:
        raise BookingError(
            _("هذا المريض موجود بالفعل في طابور الانتظار كحضور مباشر.")
        )

    # Soft block: patient has a booked (non-walk-in) appointment today
    if not override_same_day_conflict:
        has_today_booked = Appointment.objects.filter(
            clinic_id=clinic_id,
            patient=patient,
            appointment_date=today,
            is_walk_in=False,
            status__in=[
                Appointment.Status.PENDING,
                Appointment.Status.CONFIRMED,
                Appointment.Status.CHECKED_IN,
                Appointment.Status.IN_PROGRESS,
            ],
        ).exists()
        if has_today_booked:
            raise BookingError(
                _(
                    "لهذا المريض موعد محجوز اليوم. يرجى إلغاؤه أو تأكيد التسجيل "
                    "كحضور مباشر إضافي."
                )
            )

    now_local = timezone.localtime()
    now_time = now_local.time().replace(second=0, microsecond=0)

    appointment = secretary_book_appointment(
        patient=patient,
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        appointment_type_id=appointment_type_id,
        appointment_date=today,
        appointment_time=now_time,
        reason=reason,
        notes=notes,
        secretary_note=secretary_note,
        doctor_note=doctor_note,
        status=Appointment.Status.CHECKED_IN,
        created_by=created_by,
        is_walk_in=True,
    )

    if appointment.checked_in_at is None:
        appointment.checked_in_at = timezone.now()
        appointment.queue_priority = _next_queue_priority(clinic_id, today)
        appointment.save(update_fields=["checked_in_at", "queue_priority", "updated_at"])

    return appointment


def get_patient_future_appointments(*, patient, clinic):
    """
    Return active future appointments (today and beyond) for this patient at
    this clinic. Used by the walk-in registration flow to surface conflicts
    and let the secretary cancel any of them.
    """
    today = date_cls.today()
    return (
        Appointment.objects.filter(
            clinic=clinic,
            patient=patient,
            appointment_date__gte=today,
            status__in=[
                Appointment.Status.PENDING,
                Appointment.Status.CONFIRMED,
            ],
        )
        .select_related("doctor", "appointment_type")
        .order_by("appointment_date", "appointment_time")
    )
