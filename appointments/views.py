from datetime import datetime, date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from appointments.models import Appointment, AppointmentType
from appointments.services import (
    BookingError,
    InvalidSlotError,
    PastDateError,
    SlotUnavailableError,
    book_appointment,
)
from clinics.models import Clinic
from doctors.models import DoctorAvailability
from doctors.services import generate_slots_for_date

User = get_user_model()


@login_required
def book_appointment_view(request, clinic_id):
    """
    Patient-facing booking page.

    GET: Renders the booking form with doctor selection, appointment type,
         date picker, and time slots.
    POST: Processes the booking via the booking service.

    Flow:
        1. Patient selects a doctor (query param or form)
        2. Patient selects an appointment type
        3. Patient picks a date → slots are loaded via HTMX
        4. Patient clicks a slot → POST submits the booking
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Only patients can book appointments.")

    clinic = get_object_or_404(Clinic, id=clinic_id, is_active=True)

    # Get available doctors at this clinic
    from clinics.models import ClinicStaff

    doctors = []
    # Main doctor
    if clinic.main_doctor:
        doctors.append(clinic.main_doctor)
    # Staff doctors
    staff_doctors = ClinicStaff.objects.filter(
        clinic=clinic, role="DOCTOR", is_active=True
    ).select_related("user")
    for staff in staff_doctors:
        if staff.user not in doctors:
            doctors.append(staff.user)

    # Pre-selected doctor (from query param, e.g., coming from browse_doctors)
    doctor_id = request.GET.get("doctor_id") or request.POST.get("doctor_id")
    selected_doctor = None
    appointment_types = []

    if doctor_id:
        try:
            selected_doctor = User.objects.get(
                id=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"]
            )
            appointment_types = AppointmentType.objects.filter(
                doctor=selected_doctor, clinic=clinic, is_active=True
            )
        except User.DoesNotExist:
            selected_doctor = None

    # Handle POST (booking submission)
    if request.method == "POST":
        try:
            appointment_type_id = int(request.POST.get("appointment_type_id", 0))
            appointment_date_str = request.POST.get("appointment_date", "")
            appointment_time_str = request.POST.get("appointment_time", "")
            reason = request.POST.get("reason", "")

            if not all([doctor_id, appointment_type_id, appointment_date_str, appointment_time_str]):
                messages.error(request, "يرجى ملء جميع الحقول المطلوبة.")
                return redirect("patients:book_appointment", clinic_id=clinic_id)

            appointment_date = datetime.strptime(appointment_date_str, "%Y-%m-%d").date()
            appointment_time = datetime.strptime(appointment_time_str, "%H:%M").time()

            appointment = book_appointment(
                patient=request.user,
                doctor_id=int(doctor_id),
                clinic_id=clinic_id,
                appointment_type_id=appointment_type_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                reason=reason,
            )

            messages.success(
                request,
                f"تم حجز موعدك بنجاح! رقم الحجز: #{appointment.id}"
            )
            return redirect("appointments:booking_confirmation", appointment_id=appointment.id)

        except SlotUnavailableError as e:
            messages.error(request, e.message)
        except (InvalidSlotError, PastDateError) as e:
            messages.error(request, e.message)
        except BookingError as e:
            messages.error(request, e.message)
        except (ValueError, TypeError):
            messages.error(request, "بيانات غير صالحة. يرجى المحاولة مرة أخرى.")

        return redirect(
            f"/patients/appointments/book/{clinic_id}/?doctor_id={doctor_id}"
        )

    context = {
        "clinic": clinic,
        "doctors": doctors,
        "selected_doctor": selected_doctor,
        "appointment_types": appointment_types,
        "today": date.today().isoformat(),
    }
    return render(request, "appointments/book_appointment.html", context)


@login_required
def load_appointment_types(request, clinic_id):
    """
    HTMX endpoint: Returns appointment types for a selected doctor.
    GET /appointments/<clinic_id>/htmx/appointment-types/?doctor_id=X
    """
    doctor_id = request.GET.get("doctor_id")
    if not doctor_id:
        return render(request, "appointments/partials/appointment_types.html", {"appointment_types": []})

    appointment_types = AppointmentType.objects.filter(
        doctor_id=doctor_id, clinic_id=clinic_id, is_active=True
    )
    return render(
        request,
        "appointments/partials/appointment_types.html",
        {"appointment_types": appointment_types},
    )


@login_required
def load_available_slots(request, clinic_id):
    """
    HTMX endpoint: Returns available time slots for a doctor on a date.
    GET /appointments/<clinic_id>/htmx/slots/?doctor_id=X&date=YYYY-MM-DD&appointment_type_id=Y
    """
    doctor_id = request.GET.get("doctor_id")
    date_str = request.GET.get("date")
    appointment_type_id = request.GET.get("appointment_type_id")

    if not all([doctor_id, date_str, appointment_type_id]):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        appointment_type = AppointmentType.objects.get(
            id=appointment_type_id,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            is_active=True,
        )
    except (ValueError, AppointmentType.DoesNotExist):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    if target_date < date.today():
        return render(
            request,
            "appointments/partials/time_slots.html",
            {"slots": [], "error": "لا يمكن الحجز في تاريخ سابق."},
        )

    slots = generate_slots_for_date(
        doctor_id=int(doctor_id),
        clinic_id=int(clinic_id),
        target_date=target_date,
        duration_minutes=appointment_type.duration_minutes,
    )

    # Filter to only available slots
    available_slots = [s for s in slots if s["is_available"]]

    return render(
        request,
        "appointments/partials/time_slots.html",
        {
            "slots": slots,
            "available_slots": available_slots,
            "target_date": target_date,
            "appointment_type": appointment_type,
        },
    )


@login_required
def booking_confirmation(request, appointment_id):
    """
    Displays booking confirmation after successful appointment creation.
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Only patients can view this page.")

    appointment = get_object_or_404(
        Appointment,
        id=appointment_id,
        patient=request.user,
    )

    context = {
        "appointment": appointment,
    }
    return render(request, "appointments/booking_confirmation.html", context)