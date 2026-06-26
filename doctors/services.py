"""
Slot generation engine for doctor availability.

Generates bookable time slots based on:
1. Doctor's recurring weekly availability (DoctorAvailability)
2. Appointment type duration
3. Existing appointments across ALL clinics (R-03: global conflict check)
"""

from datetime import datetime, timedelta, date, time
from django.utils import timezone

from appointments.models import Appointment
from .models import DoctorAvailability


def generate_slots_for_date(
    doctor_id: int,
    clinic_id: int,
    target_date: date,
    duration_minutes: int,
    slot_step_minutes: int | None = None,
    exclude_appointment_id: int | None = None,
) -> list[dict]:
    """
    Generate time slots for a doctor on a specific date.

    Args:
        doctor_id: The doctor's user ID.
        clinic_id: The clinic ID (availability is per-clinic).
        target_date: The date to generate slots for.
        duration_minutes: Slot duration from AppointmentType.
        slot_step_minutes: Spacing between consecutive slot starts.
            If None, defaults to ``duration_minutes`` (legacy behavior).
            Passing a smaller value (e.g. 15) emits a slot every step
            while still requiring the full ``duration_minutes`` window
            to fit within the doctor's availability block.
        exclude_appointment_id: Optional appointment id to exclude from the
            booked check. Used when editing an appointment so that its own
            current slot appears available rather than booked-by-itself.

    Returns:
        List of dicts: [{"time": time, "end_time": time, "is_available": bool}, ...]
    """

    day_of_week = target_date.weekday()  # 0=Monday, 6=Sunday

    # 0a. Check for active clinic holidays on this date — return no slots
    from clinics.models import ClinicHoliday
    is_holiday = ClinicHoliday.objects.filter(
        clinic_id=clinic_id,
        is_active=True,
        start_date__lte=target_date,
        end_date__gte=target_date,
    ).exists()
    if is_holiday:
        return []

    # 0b. Check for an active doctor availability exception on this date
    from clinics.models import DoctorAvailabilityException
    is_exception = DoctorAvailabilityException.objects.filter(
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        is_active=True,
        start_date__lte=target_date,
        end_date__gte=target_date,
    ).exists()
    if is_exception:
        return []

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
    if exclude_appointment_id is not None:
        existing_appointments = existing_appointments.exclude(pk=exclude_appointment_id)

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
    step = timedelta(minutes=slot_step_minutes) if slot_step_minutes else duration
    is_today = target_date == timezone.localdate()
    now_time = timezone.localtime().time() if is_today else None

    for block in availability_blocks:
        current = datetime.combine(target_date, block.start_time)
        block_end = datetime.combine(target_date, block.end_time)

        while current + duration <= block_end:
            slot_start = current.time()
            slot_end = (current + duration).time()

            is_past = is_today and slot_start <= now_time
            is_booked = False if is_past else _is_slot_booked(slot_start, slot_end, booked_ranges)
            is_available = not is_past and not is_booked

            slots.append(
                {
                    "time": slot_start,
                    "end_time": slot_end,
                    "is_available": is_available,
                    "is_past": is_past,
                    "is_booked": is_booked,
                }
            )

            current += step

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Doctor reviews (Phase 3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# A review auto-hides once this many distinct reports accumulate (staff can still
# unhide). Manual staff hiding is independent of this threshold.
REVIEW_AUTOHIDE_REPORTS = 5


def patient_can_review_doctor(patient, doctor_id):
    """True iff *patient* has a COMPLETED appointment with the doctor — the only
    eligibility to leave a review (enforced at submit time, not on the model)."""
    if not getattr(patient, "is_authenticated", False):
        return False
    return Appointment.objects.filter(
        patient=patient, doctor_id=doctor_id, status="COMPLETED"
    ).exists()


def doctor_rating_summary(doctor_id):
    """{'avg': float|None, 'count': int} over VISIBLE reviews — for a doctor header."""
    from django.db.models import Avg, Count
    from .models import DoctorReview
    agg = DoctorReview.objects.filter(doctor_id=doctor_id, is_hidden=False).aggregate(
        avg=Avg("rating"), count=Count("id")
    )
    avg = agg["avg"]
    return {"avg": round(avg, 1) if avg is not None else None, "count": agg["count"] or 0}


def doctor_rating_summaries(doctor_ids):
    """Batched {doctor_id: {'avg', 'count'}} for a set of doctors (no N+1 on cards)."""
    from django.db.models import Avg, Count
    from .models import DoctorReview
    out = {did: {"avg": None, "count": 0} for did in doctor_ids}
    rows = (
        DoctorReview.objects.filter(doctor_id__in=doctor_ids, is_hidden=False)
        .values("doctor_id")
        .annotate(avg=Avg("rating"), count=Count("id"))
    )
    for r in rows:
        out[r["doctor_id"]] = {
            "avg": round(r["avg"], 1) if r["avg"] is not None else None,
            "count": r["count"],
        }
    return out


def visible_reviews_for_doctor(doctor_id, limit=None):
    """Visible reviews, newest first, with the reviewing patient prefetched."""
    from .models import DoctorReview
    qs = (
        DoctorReview.objects.filter(doctor_id=doctor_id, is_hidden=False)
        .select_related("patient")
        .order_by("-created_at")
    )
    return list(qs[:limit]) if limit else qs


def user_can_moderate_doctor_reviews(user, doctor_id):
    """Who may hide/unhide a doctor's reviews: an admin, or clinic staff (owner /
    secretary) at a clinic where the doctor works. A doctor can NEVER moderate
    reviews about themselves (conflict of interest)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.id == doctor_id:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from clinics.models import Clinic, ClinicStaff
    clinic_ids = set(
        ClinicStaff.objects.filter(
            user_id=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"], is_active=True
        ).values_list("clinic_id", flat=True)
    ) | set(
        Clinic.objects.filter(main_doctor_id=doctor_id).values_list("id", flat=True)
    )
    if not clinic_ids:
        return False
    if Clinic.objects.filter(id__in=clinic_ids, main_doctor=user).exists():
        return True
    return ClinicStaff.objects.filter(
        clinic_id__in=clinic_ids, user=user,
        role__in=["MAIN_DOCTOR", "SECRETARY"], is_active=True,
    ).exists()
