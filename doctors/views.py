from datetime import datetime, date

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.http import HttpResponse

from appointments.models import AppointmentType
from .models import DoctorAvailability
from .services import generate_slots_for_date

User = get_user_model()


# --- Existing staff views ---


@login_required
def dashboard(request):
    return HttpResponse("Doctor Dashboard - Coming Soon!")


@login_required
def appointments_list(request):
    return HttpResponse("Doctor Appointments List - Coming Soon!")


@login_required
def appointment_detail(request, appointment_id):
    return HttpResponse(f"Appointment {appointment_id} Detail - Coming Soon!")


@login_required
def patients_list(request):
    return HttpResponse("Doctor's Patients List - Coming Soon!")


# --- Patient-facing views ---


@login_required
def doctor_availability_view(request, doctor_id):
    """
    Patient-facing view: Shows a doctor's weekly schedule and
    available time slots for a selected date.

    Query params:
        clinic_id (required): Which clinic to view availability for.
        date (optional): Date to generate slots for (YYYY-MM-DD).
        appointment_type_id (optional): Required when date is provided.
    """
    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    clinic_id = request.GET.get("clinic_id")

    if not clinic_id:
        return render(
            request,
            "doctors/doctor_availability.html",
            {"error": "clinic_id is required.", "doctor": doctor},
        )

    # Weekly schedule
    weekly_schedule = DoctorAvailability.objects.filter(
        doctor=doctor,
        clinic_id=clinic_id,
        is_active=True,
    )

    # Appointment types for this doctor at this clinic
    appointment_types = AppointmentType.objects.filter(
        doctor=doctor,
        clinic_id=clinic_id,
        is_active=True,
    )

    # Slot generation (if date + appointment_type_id provided)
    slots = []
    target_date = None
    selected_type = None
    date_str = request.GET.get("date")
    appointment_type_id = request.GET.get("appointment_type_id")

    if date_str and appointment_type_id:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = None

        if target_date and target_date >= date.today():
            try:
                selected_type = AppointmentType.objects.get(
                    id=appointment_type_id,
                    doctor=doctor,
                    clinic_id=clinic_id,
                    is_active=True,
                )
                slots = generate_slots_for_date(
                    doctor_id=doctor.id,
                    clinic_id=int(clinic_id),
                    target_date=target_date,
                    duration_minutes=selected_type.duration_minutes,
                )
            except AppointmentType.DoesNotExist:
                selected_type = None

    context = {
        "doctor": doctor,
        "clinic_id": clinic_id,
        "weekly_schedule": weekly_schedule,
        "appointment_types": appointment_types,
        "slots": slots,
        "target_date": target_date,
        "selected_type": selected_type,
        "today": date.today().isoformat(),
    }
    return render(request, "doctors/doctor_availability.html", context)


@login_required
def doctor_appointment_types_view(request, doctor_id):
    """
    Patient-facing view: Shows appointment types offered by a doctor.

    Query params:
        clinic_id (required): Which clinic to view types for.
    """
    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    clinic_id = request.GET.get("clinic_id")

    if not clinic_id:
        return render(
            request,
            "doctors/doctor_appointment_types.html",
            {"error": "clinic_id is required.", "doctor": doctor},
        )

    appointment_types = AppointmentType.objects.filter(
        doctor=doctor,
        clinic_id=clinic_id,
        is_active=True,
    )

    context = {
        "doctor": doctor,
        "clinic_id": clinic_id,
        "appointment_types": appointment_types,
    }
    return render(request, "doctors/doctor_appointment_types.html", context)