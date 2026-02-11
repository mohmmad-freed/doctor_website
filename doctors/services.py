"""
Slot generation engine for doctor availability.

Generates bookable time slots based on:
1. Doctor's recurring weekly availability (DoctorAvailability)
2. Appointment type duration
3. Existing appointments across ALL clinics (R-03: global conflict check)
"""

from datetime import datetime, timedelta, date, time

from appointments.models import Appointment
from .models import DoctorAvailability


def generate_slots_for_date(
    doctor_id: int,
    clinic_id: int,
    target_date: date,
    duration_minutes: int,
) -> list[dict]:
    """
    Generate time slots for a doctor on a specific date.

    Args:
        doctor_id: The doctor's user ID.
        clinic_id: The clinic ID (availability is per-clinic).
        target_date: The date to generate slots for.
        duration_minutes: Slot duration from AppointmentType.

    Returns:
        List of dicts: [{"time": time, "end_time": time, "is_available": bool}, ...]
    """

    day_of_week = target_date.weekday()  # 0=Monday, 6=Sunday

    # 1. Get active availability blocks for this doctor + clinic + day
    availability_blocks = DoctorAvailability.objects.filter(
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        day_of_week=day_of_week,
        is_active=True,
    ).order_by("start_time")

    if not availability_blocks.exists():
        return []

    # 2. Get ALL existing appointments for this doctor on this date
    #    across ALL clinics (R-03: global conflict check)
    existing_appointments = Appointment.objects.filter(
        doctor_id=doctor_id,
        appointment_date=target_date,
        status__in=["CONFIRMED", "COMPLETED"],
    ).select_related("appointment_type")

    # Build list of booked time ranges: [(start_time, end_time), ...]
    booked_ranges = []
    for appt in existing_appointments:
        appt_start = appt.appointment_time
        # Calculate end time from appointment_type duration, fallback to duration_minutes
        appt_duration = duration_minutes
        if appt.appointment_type and appt.appointment_type.duration_minutes:
            appt_duration = appt.appointment_type.duration_minutes

        appt_end = _add_minutes_to_time(appt_start, appt_duration)
        booked_ranges.append((appt_start, appt_end))

    # 3. Generate slots from each availability block
    slots = []
    duration = timedelta(minutes=duration_minutes)

    for block in availability_blocks:
        current = datetime.combine(target_date, block.start_time)
        block_end = datetime.combine(target_date, block.end_time)

        while current + duration <= block_end:
            slot_start = current.time()
            slot_end = (current + duration).time()

            # Check if this slot conflicts with any booked appointment
            is_available = not _is_slot_booked(slot_start, slot_end, booked_ranges)

            slots.append(
                {
                    "time": slot_start,
                    "end_time": slot_end,
                    "is_available": is_available,
                }
            )

            current += duration

    return slots


def _is_slot_booked(
    slot_start: time,
    slot_end: time,
    booked_ranges: list[tuple[time, time]],
) -> bool:
    """
    Check if a slot overlaps with any booked time range.
    Overlap exists when: booked_start < slot_end AND booked_end > slot_start
    """
    for booked_start, booked_end in booked_ranges:
        if booked_start < slot_end and booked_end > slot_start:
            return True
    return False


def _add_minutes_to_time(t: time, minutes: int) -> time:
    """Add minutes to a time object, returning a new time object."""
    dt = datetime.combine(datetime.today(), t)
    dt += timedelta(minutes=minutes)
    return dt.time()