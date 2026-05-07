import json
from datetime import datetime, date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from appointments.models import Appointment, AppointmentAnswer, AppointmentAttachment, AppointmentType
from appointments.services import (
    BookingError,
    InvalidSlotError,
    PastDateError,
    SlotUnavailableError,
    book_appointment,
)
from appointments.services.intake_service import (
    collect_and_validate_intake,
    get_active_intake_template,
    get_rules_for_template,
    save_intake_answers,
)
from clinics.models import Clinic
from doctors.models import DoctorAvailability
from doctors.services import generate_slots_for_date

User = get_user_model()

ARABIC_DAYS = {
    0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس",
    4: "الجمعة", 5: "السبت", 6: "الأحد",
}
ARABIC_MONTHS = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
    5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}


def format_date_ar(d):
    """Format a date object as 'الاثنين 16 فبراير 2026'."""
    if d is None:
        return ""
    day_name = ARABIC_DAYS.get(d.weekday(), "")
    month_name = ARABIC_MONTHS.get(d.month, "")
    return f"{day_name} {d.day} {month_name} {d.year}"


# أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬ Intake Form Helpers أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬أ¢â€‌â‚¬






def book_appointment_view(request, clinic_id):
    """
    Patient-facing booking page (multi-step HTMX form).

    Steps: Select Doctor أ¢â€ â€™ Select Type أ¢â€ â€™ Select Date/Time أ¢â€ â€™ Fill Intake Form أ¢â€ â€™ Confirm
    """
    if not request.user.has_role("PATIENT"):
        return HttpResponseForbidden("Only patients can book appointments.")

    clinic = get_object_or_404(Clinic, id=clinic_id, is_active=True)

    # Get available doctors at this clinic
    from clinics.models import ClinicStaff

    doctors = []
    if clinic.main_doctor:
        doctors.append(clinic.main_doctor)
    staff_doctors = ClinicStaff.objects.filter(
        clinic=clinic, role="DOCTOR", is_active=True
    ).select_related("user")
    for staff in staff_doctors:
        if staff.user not in doctors:
            doctors.append(staff.user)

    # Exclude the current user — patients cannot book with themselves
    doctors = [d for d in doctors if d.id != request.user.id]

    # Pre-selected doctor
    doctor_id = request.GET.get("doctor_id") or request.POST.get("doctor_id")
    selected_doctor = None
    appointment_types = []

    if doctor_id:
        try:
            if int(doctor_id) == request.user.id:
                messages.error(request, _("لا يمكنك حجز موعد مع نفسك."))
                return redirect("patients:browse_doctors")
        except (TypeError, ValueError):
            pass
        try:
            selected_doctor = User.objects.get(
                id=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"]
            )
            from appointments.services.appointment_type_service import (
                get_appointment_types_for_doctor_in_clinic,
            )
            appointment_types = get_appointment_types_for_doctor_in_clinic(
                doctor_id=int(doctor_id), clinic_id=clinic_id
            )
        except User.DoesNotExist:
            selected_doctor = None

    # Handle POST (booking submission)
    if request.method == "POST":
        try:
            appointment_type_id = int(request.POST.get("appointment_type_id") or 0)
            appointment_date_str = request.POST.get("appointment_date", "").strip()
            appointment_time_str = request.POST.get("appointment_time", "").strip()
            reason = request.POST.get("reason", "").strip()

            # Validate reason field against template settings
            template_check, _ = get_active_intake_template(doctor_id, appointment_type_id)
            if template_check and template_check.show_reason_field and template_check.reason_field_required and not reason:
                reason_label = template_check.reason_field_label or "وصف الحالة الطبية"
                messages.error(request, f"حقل '{reason_label}' مطلوب.")
                return redirect(f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}")

            # Validate description length
            if len(reason) > 1000:
                messages.error(request, "ط¸ث†ط·آµط¸ظ¾ ط·آ§ط¸â€‍ط·آ­ط·آ§ط¸â€‍ط·آ© ط·آ§ط¸â€‍ط·آ·ط·آ¨ط¸ظ¹ط·آ© ط¸ظ¹ط·آ¬ط·آ¨ ط·آ£ط¸â€  ط¸â€‍ط·آ§ ط¸ظ¹ط·ع¾ط·آ¬ط·آ§ط¸ث†ط·آ² 1000 ط·آ­ط·آ±ط¸ظ¾.")
                return redirect(f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}")

            if not all([doctor_id, appointment_type_id, appointment_date_str, appointment_time_str]):
                messages.error(request, "ط¸ظ¹ط·آ±ط·آ¬ط¸â€° ط¸â€¦ط¸â€‍ط·طŒ ط·آ¬ط¸â€¦ط¸ظ¹ط·آ¹ ط·آ§ط¸â€‍ط·آ­ط¸â€ڑط¸ث†ط¸â€‍ ط·آ§ط¸â€‍ط¸â€¦ط·آ·ط¸â€‍ط¸ث†ط·آ¨ط·آ©.")
                return redirect(f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}")

            appointment_date = datetime.strptime(appointment_date_str, "%Y-%m-%d").date()
            appointment_time = datetime.strptime(appointment_time_str, "%H:%M").time()

            # أ¢â€‌â‚¬أ¢â€‌â‚¬ Collect and validate intake form answers أ¢â€‌â‚¬أ¢â€‌â‚¬
            template, questions = get_active_intake_template(doctor_id, appointment_type_id)
            answers_dict = {}
            file_data = {}

            if questions:
                answers_dict, file_data, validation_errors = collect_and_validate_intake(
                    request.POST, request.FILES, questions, []
                )
                if validation_errors:
                    for err in validation_errors:
                        messages.error(request, err)
                    return redirect(
                        f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}"
                    )

            # أ¢â€‌â‚¬أ¢â€‌â‚¬ Also build legacy intake_responses for backward compat أ¢â€‌â‚¬أ¢â€‌â‚¬
            legacy_responses = {}
            for q in questions:
                val = answers_dict.get(str(q.id), "")
                if val:
                    legacy_responses[str(q.id)] = {
                        "label": q.display_text,
                        "value": val,
                        "field_type": q.field_type,
                    }

            appointment = book_appointment(
                patient=request.user,
                doctor_id=int(doctor_id),
                clinic_id=clinic_id,
                appointment_type_id=appointment_type_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                reason=reason,
            )

            # == Save structured AppointmentAnswer records ==
            if questions:
                save_intake_answers(appointment, questions, answers_dict, file_data, request.user)

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
        except (ValueError, TypeError) as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f"ط·آ¨ط¸ظ¹ط·آ§ط¸â€ ط·آ§ط·ع¾ ط·ط›ط¸ظ¹ط·آ± ط·آµط·آ§ط¸â€‍ط·آ­ط·آ©: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f"ط·آ®ط·آ·ط·آ£ ط·ط›ط¸ظ¹ط·آ± ط¸â€¦ط·ع¾ط¸ث†ط¸â€ڑط·آ¹: {e}")

        return redirect(
            f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}"
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

    If the doctor has configured DoctorClinicAppointmentType rows, only those
    active types are returned.  Falls back to all active clinic types when no
    configuration exists (backwards-compat).
    """
    doctor_id = request.GET.get("doctor_id")
    if not doctor_id:
        return render(request, "appointments/partials/appointment_types.html", {"appointment_types": []})

    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )
    appointment_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=int(doctor_id), clinic_id=clinic_id
    )
    return render(
        request,
        "appointments/partials/appointment_types.html",
        {"appointment_types": appointment_types},
    )


@login_required
def load_available_slots(request, clinic_id):
    """HTMX endpoint: Returns available time slots for a doctor on a date."""
    doctor_id = request.GET.get("doctor_id")
    date_str = request.GET.get("appointment_date") or request.GET.get("date")
    appointment_type_id = request.GET.get("appointment_type_id")

    if not all([doctor_id, date_str, appointment_type_id]):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        appointment_type = AppointmentType.objects.get(
            id=appointment_type_id, clinic_id=clinic_id, is_active=True,
        )
    except (ValueError, AppointmentType.DoesNotExist):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    # Verify this appointment type is actually enabled for the selected doctor
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
        get_slot_step_minutes_for_doctor,
    )
    enabled_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=int(doctor_id), clinic_id=clinic_id
    )
    if not any(t.id == appointment_type.id for t in enabled_types):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    if target_date < date.today():
        return render(
            request,
            "appointments/partials/time_slots.html",
            {"slots": [], "error": "ط¸â€‍ط·آ§ ط¸ظ¹ط¸â€¦ط¸ئ’ط¸â€  ط·آ§ط¸â€‍ط·آ­ط·آ¬ط·آ² ط¸ظ¾ط¸ظ¹ ط·ع¾ط·آ§ط·آ±ط¸ظ¹ط·آ® ط·آ³ط·آ§ط·آ¨ط¸â€ڑ."},
        )

    slot_step = get_slot_step_minutes_for_doctor(int(doctor_id), int(clinic_id))
    slots = generate_slots_for_date(
        doctor_id=int(doctor_id), clinic_id=int(clinic_id),
        target_date=target_date, duration_minutes=appointment_type.duration_minutes,
        slot_step_minutes=slot_step,
    )
    available_slots = [s for s in slots if s["is_available"]]

    return render(
        request,
        "appointments/partials/time_slots.html",
        {
            "slots": slots,
            "available_slots": available_slots,
            "target_date": target_date,
            "target_date_ar": format_date_ar(target_date),
            "appointment_type": appointment_type,
        },
    )


@login_required
def load_intake_form(request, clinic_id):
    """
    HTMX endpoint: Returns the intake form for a doctor + appointment type.

    GET /appointments/<clinic_id>/htmx/intake-form/?doctor_id=X&appointment_type_id=Y

    Loads the matching DoctorIntakeFormTemplate and its questions.
    If conditional rules exist, they are serialized as JSON for client-side JS.
    """
    doctor_id = request.GET.get("doctor_id")
    appointment_type_id = request.GET.get("appointment_type_id")

    if not doctor_id:
        return render(request, "appointments/partials/intake_form.html", {})

    template, questions = get_active_intake_template(doctor_id, appointment_type_id)

    if template and questions:
        rules_json = json.dumps(get_rules_for_template(template), ensure_ascii=False)
        return render(
            request,
            "appointments/partials/intake_form.html",
            {
                "form_template": template,
                "questions": questions,
                "rules_json": rules_json,
            },
        )
    else:
        return render(
            request,
            "appointments/partials/intake_form.html",
            {"no_form": True},
        )


@login_required
def booking_confirmation(request, appointment_id):
    """Displays booking confirmation after successful appointment creation."""
    if not request.user.has_role("PATIENT"):
        return HttpResponseForbidden("Only patients can view this page.")

    appointment = get_object_or_404(Appointment, id=appointment_id, patient=request.user)

    # Build display list from AppointmentAnswer records
    intake_display = []
    answers = appointment.answers.select_related("question").order_by("question__order")
    for ans in answers:
        if ans.answer_text:
            intake_display.append({
                "label": ans.question.display_text,
                "value": ans.answer_text,
            })

    # Fallback to legacy JSON if no structured answers
    if not intake_display and appointment.intake_responses:
        for field_id, data in appointment.intake_responses.items():
            value = data.get("value", "")
            if isinstance(value, list):
                value = "ط·إ’ ".join(value)
            intake_display.append({
                "label": data.get("label", ""),
                "value": value,
            })

    # Get attachments أ¢â‚¬â€‌ separate regular from dated, order dated by date
    all_attachments = appointment.attachments.select_related("question").order_by(
        "file_group_date", "uploaded_at"
    )
    regular_attachments = []
    dated_groups_dict = {}  # {date: [att, ...]}
    for att in all_attachments:
        if att.file_group_date:
            dated_groups_dict.setdefault(att.file_group_date, []).append(att)
        else:
            regular_attachments.append(att)

    # Sort dated groups by date (ascending)
    dated_file_groups = sorted(dated_groups_dict.items(), key=lambda x: x[0])

    context = {
        "appointment": appointment,
        "appointment_date_ar": format_date_ar(appointment.appointment_date),
        "intake_display": intake_display,
        "attachments": regular_attachments,
        "dated_file_groups": dated_file_groups,
    }
    return render(request, "appointments/booking_confirmation.html", context)
