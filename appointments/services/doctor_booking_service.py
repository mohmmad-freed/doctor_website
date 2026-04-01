"""
Doctor-side follow-up appointment scheduling service.

Allows doctors to schedule follow-up appointments directly for their patients
without any role-switching or the patient-side booking flow.

Key differences from book_appointment() in booking_service.py:
- No patient-role check (doctors act on behalf of patients)
- Slot validation is advisory — doctors can override conflicts
- created_by is always the doctor
"""

from datetime import date, time
from django.db import transaction

from appointments.models import Appointment, AppointmentType


class DoctorSchedulingError(Exception):
    def __init__(self, message, code="error"):
        self.message = message
        self.code = code
        super().__init__(self.message)


def schedule_followup(
    *,
    doctor,
    patient_id: int,
    clinic_id: int,
    appointment_date: date,
    appointment_time: time,
    appointment_type_id: int | None = None,
    notes: str = "",
    allow_conflict: bool = False,
) -> Appointment:
    """
    Schedule a follow-up appointment from the doctor side.

    Args:
        doctor: The User instance (doctor) creating the appointment.
        patient_id: The patient's user ID.
        clinic_id: The clinic where the appointment will take place.
        appointment_date: The appointment date.
        appointment_time: The appointment start time.
        appointment_type_id: Optional appointment type. Defaults to
            the clinic's first active "follow-up" type, or None.
        notes: Optional notes / reason for follow-up.
        allow_conflict: If True, skips the time-conflict check.

    Returns:
        The created Appointment instance (status=CONFIRMED).

    Raises:
        DoctorSchedulingError: For any validation failure.
    """
    from django.contrib.auth import get_user_model
    from clinics.models import Clinic, ClinicStaff
    from patients.models import ClinicPatient

    User = get_user_model()

    # ── 1. Patient must be registered at this clinic ──────────
    if not ClinicPatient.objects.filter(
        patient_id=patient_id, clinic_id=clinic_id
    ).exists():
        raise DoctorSchedulingError(
            "This patient is not registered at the selected clinic.",
            code="patient_not_in_clinic",
        )

    try:
        patient = User.objects.get(pk=patient_id)
    except User.DoesNotExist:
        raise DoctorSchedulingError("Patient not found.", code="patient_not_found")

    # ── 2. Validate clinic ────────────────────────────────────
    try:
        clinic = Clinic.objects.get(id=clinic_id)
    except Clinic.DoesNotExist:
        raise DoctorSchedulingError("Clinic not found.", code="invalid_clinic")

    # ── 3. Doctor must be active staff at this clinic ─────────
    is_staff = ClinicStaff.objects.filter(
        clinic_id=clinic_id,
        user=doctor,
        role__in=["DOCTOR", "MAIN_DOCTOR"],
        is_active=True,
    ).exists()
    if not is_staff:
        raise DoctorSchedulingError(
            "You are not an active doctor at this clinic.",
            code="not_clinic_staff",
        )

    # ── 4. Date must not be in the past ───────────────────────
    if appointment_date < date.today():
        raise DoctorSchedulingError(
            "Cannot schedule appointments for past dates.",
            code="past_date",
        )

    # ── 5. Check for existing upcoming appointment ────────────
    existing_upcoming = (
        Appointment.objects.filter(
            doctor=doctor,
            patient_id=patient_id,
            appointment_date__gte=date.today(),
            status__in=[
                Appointment.Status.PENDING,
                Appointment.Status.CONFIRMED,
            ],
        )
        .order_by("appointment_date", "appointment_time")
        .first()
    )
    if existing_upcoming and not allow_conflict:
        raise DoctorSchedulingError(
            f"This patient already has an upcoming appointment on "
            f"{existing_upcoming.appointment_date.strftime('%d %b %Y')} at "
            f"{existing_upcoming.appointment_time.strftime('%H:%M')}.",
            code="already_scheduled",
        )

    # ── 6. Resolve appointment type ───────────────────────────
    appointment_type = None
    if appointment_type_id:
        try:
            appointment_type = AppointmentType.objects.get(
                id=appointment_type_id,
                clinic_id=clinic_id,
                is_active=True,
            )
        except AppointmentType.DoesNotExist:
            raise DoctorSchedulingError(
                "Appointment type not found for this clinic.",
                code="invalid_type",
            )
    else:
        # Default: first active follow-up type for this clinic
        appointment_type = AppointmentType.objects.filter(
            clinic_id=clinic_id,
            is_active=True,
            name__icontains="follow",
        ).first()

    # ── 7. Conflict check + create (atomic, with row-level lock) ──
    with transaction.atomic():
        conflict_exists = (
            Appointment.objects.select_for_update()
            .filter(
                doctor=doctor,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                status__in=[
                    Appointment.Status.CONFIRMED,
                    Appointment.Status.CHECKED_IN,
                    Appointment.Status.IN_PROGRESS,
                ],
            )
            .exists()
        )

        if conflict_exists and not allow_conflict:
            raise DoctorSchedulingError(
                "You already have an appointment at this time.",
                code="slot_conflict",
            )

        appointment = Appointment.objects.create(
            patient=patient,
            clinic=clinic,
            doctor=doctor,
            appointment_type=appointment_type,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status=Appointment.Status.CONFIRMED,
            notes=notes,
            created_by=doctor,
        )

    return appointment
