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

    # ── 0. Validate the patient argument is actually a patient ────────
    if not patient.has_role("PATIENT"):
        raise BookingError(
            "Only patients can book appointments.",
            code="not_a_patient",
        )

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

    # ── 2a. Validate compliance (block if patient is blocked) ───────
    from compliance.services.compliance_service import is_patient_blocked
    if hasattr(patient, 'patient_profile'):
        if is_patient_blocked(clinic, patient.patient_profile):
            raise BookingError("You are blocked from booking at this clinic due to repeated no-shows.", code="patient_blocked")

    # ── 2b. Validate doctor is active at this clinic ──────────────────
    from clinics.models import ClinicStaff
    is_main_doctor = (clinic.main_doctor_id == doctor_id)
    is_active_staff = ClinicStaff.objects.filter(
        clinic=clinic, user_id=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"], is_active=True
    ).exists()
    if not is_main_doctor and not is_active_staff:
        raise BookingError(
            "This doctor is not available at this clinic.",
            code="doctor_not_active",
        )

    # ── 2c. Validate doctor identity is verified ──────────────────────
    from doctors.models import DoctorVerification
    try:
        verification = DoctorVerification.objects.get(user_id=doctor_id)
        if verification.identity_status != "IDENTITY_VERIFIED":
            raise BookingError(
                "This doctor is not available for booking at this time.",
                code="doctor_not_verified",
            )
    except DoctorVerification.DoesNotExist:
        raise BookingError(
            "This doctor is not available for booking at this time.",
            code="doctor_not_verified",
        )

    # ── 2d. Validate clinic subscription is active ────────────────────
    from clinics.models import ClinicSubscription
    try:
        subscription = ClinicSubscription.objects.get(clinic=clinic)
        if not subscription.is_effectively_active():
            raise BookingError(
                "Appointments cannot be booked at this clinic right now.",
                code="clinic_subscription_inactive",
            )
    except ClinicSubscription.DoesNotExist:
        pass  # No subscription record — allow booking

    # ── 2e. Check for clinic holiday on the requested date ────────────
    from clinics.models import ClinicHoliday
    if ClinicHoliday.objects.filter(
        clinic=clinic,
        is_active=True,
        start_date__lte=appointment_date,
        end_date__gte=appointment_date,
    ).exists():
        raise BookingError(
            "The clinic is closed on the selected date.",
            code="clinic_holiday",
        )

    # ── 2f. Check for doctor availability exception on the date ───────
    from clinics.models import DoctorAvailabilityException
    if DoctorAvailabilityException.objects.filter(
        doctor_id=doctor_id,
        clinic=clinic,
        is_active=True,
        start_date__lte=appointment_date,
        end_date__gte=appointment_date,
    ).exists():
        raise BookingError(
            "The doctor is not available on the selected date.",
            code="doctor_exception",
        )

    # ── 3. Validate appointment type belongs to clinic ────────────────
    try:
        appointment_type = AppointmentType.objects.get(
            id=appointment_type_id,
            clinic_id=clinic_id,
            is_active=True,
        )
    except AppointmentType.DoesNotExist:
        raise BookingError(
            "Appointment type not found for this clinic.",
            code="invalid_appointment_type",
        )

    # ── 3.5. Validate appointment type is enabled for this doctor ─────
    # Uses backwards-compat fall-back: if no DCAT rows configured for the
    # (doctor, clinic) pair, all active clinic types are allowed.
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )
    enabled_types = get_appointment_types_for_doctor_in_clinic(doctor_id, clinic_id)
    if not any(t.id == appointment_type.id for t in enabled_types):
        raise BookingError(
            "هذا النوع من المواعيد غير متاح لدى هذا الطبيب في هذه العيادة.",
            code="type_not_enabled_for_doctor",
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

        # Notify patient after DB commit (in-app + email)
        from appointments.services.appointment_notification_service import (
            notify_appointment_booked,
        )
        transaction.on_commit(lambda: notify_appointment_booked(appointment))

    return appointment
