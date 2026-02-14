"""
Appointment booking service.

Handles the complete booking flow:
1. Validate the appointment type exists and is active
2. Validate the requested date is not in the past
3. Validate the requested time is a valid slot for the doctor
4. Acquire a row-level lock to prevent race conditions
5. Re-check slot availability under the lock
6. Create the appointment record

Uses select_for_update() for pessimistic locking to prevent
double-booking when two patients try to book the same slot
simultaneously.
"""

from datetime import date, datetime, timedelta, time

from django.db import transaction

from appointments.models import Appointment, AppointmentType
from clinics.models import Clinic
from doctors.services import generate_slots_for_date


class BookingError(Exception):
    """Base exception for booking failures."""

    def __init__(self, message, code="booking_error"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class SlotUnavailableError(BookingError):
    """Raised when the requested slot is no longer available."""

    def __init__(self, message="This time slot is no longer available. Please select another slot."):
        super().__init__(message, code="slot_unavailable")


class InvalidSlotError(BookingError):
    """Raised when the requested time does not match any valid slot."""

    def __init__(self, message="The selected time is not a valid slot for this doctor."):
        super().__init__(message, code="invalid_slot")


class PastDateError(BookingError):
    """Raised when trying to book a date in the past."""

    def __init__(self, message="Cannot book appointments for past dates."):
        super().__init__(message, code="past_date")


def book_appointment(
    *,
    patient,
    doctor_id: int,
    clinic_id: int,
    appointment_type_id: int,
    appointment_date: date,
    appointment_time: time,
    reason: str = "",
) -> Appointment:
    """
    Book an appointment for a patient.

    This function is the single entry point for creating appointments
    from the patient side. It validates everything and uses DB-level
    locking to prevent race conditions.

    Args:
        patient: The User instance (patient) booking the appointment.
        doctor_id: The doctor's user ID.
        clinic_id: The clinic ID.
        appointment_type_id: The AppointmentType ID.
        appointment_date: The desired date (YYYY-MM-DD).
        appointment_time: The desired start time (HH:MM).
        reason: Optional reason for visit.

    Returns:
        The created Appointment instance.

    Raises:
        BookingError: If any validation fails.
        SlotUnavailableError: If the slot is already taken.
        InvalidSlotError: If the time doesn't match a valid slot.
        PastDateError: If the date is in the past.
        AppointmentType.DoesNotExist: If the appointment type is invalid.
        Clinic.DoesNotExist: If the clinic is invalid.
    """

    # ── 1. Basic date validation ──────────────────────────────────────
    today = date.today()
    if appointment_date < today:
        raise PastDateError()

    # If booking for today, ensure the time hasn't already passed
    if appointment_date == today:
        now = datetime.now().time()
        if appointment_time <= now:
            raise PastDateError("Cannot book a slot that has already passed today.")

    # ── 2. Validate clinic exists ─────────────────────────────────────
    try:
        clinic = Clinic.objects.get(id=clinic_id, is_active=True)
    except Clinic.DoesNotExist:
        raise BookingError("Clinic not found or inactive.", code="invalid_clinic")

    # ── 3. Validate appointment type ──────────────────────────────────
    try:
        appointment_type = AppointmentType.objects.get(
            id=appointment_type_id,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            is_active=True,
        )
    except AppointmentType.DoesNotExist:
        raise BookingError(
            "Appointment type not found for this doctor and clinic.",
            code="invalid_appointment_type",
        )

    # ── 4. Validate the slot is a legitimate generated slot ───────────
    slots = generate_slots_for_date(
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        target_date=appointment_date,
        duration_minutes=appointment_type.duration_minutes,
    )

    matching_slot = None
    for slot in slots:
        if slot["time"] == appointment_time:
            matching_slot = slot
            break

    if matching_slot is None:
        raise InvalidSlotError()

    # Quick pre-check before acquiring lock (fail fast)
    if not matching_slot["is_available"]:
        raise SlotUnavailableError()

    # ── 5. Acquire lock and re-validate (atomic) ─────────────────────
    with transaction.atomic():
        # Lock all CONFIRMED/CHECKED_IN/IN_PROGRESS appointments for
        # this doctor on this date across ALL clinics (R-03 global check).
        # This prevents two concurrent requests from both passing the
        # availability check and creating duplicate bookings.
        _locked_appointments = (
            Appointment.objects.select_for_update()
            .filter(
                doctor_id=doctor_id,
                appointment_date=appointment_date,
                status__in=[
                    Appointment.Status.CONFIRMED,
                    Appointment.Status.CHECKED_IN,
                    Appointment.Status.IN_PROGRESS,
                ],
            )
            .values_list("appointment_time", flat=True)
        )

        # Force query evaluation to acquire the locks
        locked_times = list(_locked_appointments)

        # Re-generate slots under the lock to get fresh availability
        slots_under_lock = generate_slots_for_date(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            target_date=appointment_date,
            duration_minutes=appointment_type.duration_minutes,
        )

        slot_under_lock = None
        for slot in slots_under_lock:
            if slot["time"] == appointment_time:
                slot_under_lock = slot
                break

        if slot_under_lock is None or not slot_under_lock["is_available"]:
            raise SlotUnavailableError()

        # ── 6. Create the appointment ─────────────────────────────────
        appointment = Appointment.objects.create(
            patient=patient,
            clinic=clinic,
            doctor_id=doctor_id,
            appointment_type=appointment_type,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status=Appointment.Status.CONFIRMED,
            reason=reason,
            created_by=patient,
        )

    return appointment