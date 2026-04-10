"""
Secretary-side business logic layer.

Provides appointment booking and status transition functions that bypass
patient-portal restrictions (doctor platform verification, subscription
quota checks) since all operations are performed by clinic staff.
"""

from datetime import date as date_cls

from django.db import transaction
from django.utils import timezone

from appointments.models import Appointment, AppointmentType
from appointments.services.booking_service import BookingError, SlotUnavailableError


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

    if new_status == Appointment.Status.CANCELLED:
        appointment.cancellation_reason = cancellation_reason.strip()
        update_fields.append("cancellation_reason")

    appointment.save(update_fields=update_fields)
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
    status: str = Appointment.Status.CONFIRMED,
    created_by,
) -> "Appointment":
    """
    Create an appointment on behalf of a patient, from the secretary's interface.

    Differences from the patient-side book_appointment():
    - Skips doctor platform-verification check (doctor is already vetted by clinic).
    - Skips subscription quota check (internal clinic operation).
    - Allows CHECKED_IN as initial status (walk-in registrations).
    - Sets created_by to the secretary user.

    Args:
        patient:               The patient User instance.
        doctor_id:             Doctor's user PK.
        clinic_id:             Clinic PK.
        appointment_type_id:   AppointmentType PK.
        appointment_date:      date object.
        appointment_time:      time object.
        reason:                Optional reason for visit.
        notes:                 Optional internal notes.
        status:                Initial status (default CONFIRMED, can be CHECKED_IN).
        created_by:            The secretary User who created this.

    Returns:
        The created Appointment.

    Raises:
        BookingError / SlotUnavailableError on validation or conflict.
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

    # 5. Appointment type
    try:
        appt_type = AppointmentType.objects.get(
            id=appointment_type_id, clinic_id=clinic_id, is_active=True
        )
    except AppointmentType.DoesNotExist:
        raise BookingError("نوع الموعد المحدد غير موجود أو غير مفعّل في هذه العيادة.")

    # 6. Slot conflict check with row-level lock
    with transaction.atomic():
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
            created_by=created_by,
        )

    return appointment
