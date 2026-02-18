import json
from datetime import datetime, date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from appointments.models import Appointment, AppointmentAnswer, AppointmentAttachment, AppointmentType
from appointments.services import (
    BookingError,
    InvalidSlotError,
    PastDateError,
    SlotUnavailableError,
    book_appointment,
)
from clinics.models import Clinic
from doctors.models import DoctorAvailability, DoctorIntakeFormTemplate, DoctorIntakeQuestion, DoctorIntakeRule
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


# ─── Intake Form Helpers ──────────────────────────────────────────────────


def get_active_intake_template(doctor_id, appointment_type_id=None):
    """
    Find the active intake form template for a doctor.

    Lookup order (per README Section 6.3):
      1. Template specific to this appointment_type
      2. Template for all types (appointment_type=NULL)
      3. None

    Returns: (DoctorIntakeFormTemplate, list[DoctorIntakeQuestion]) or (None, [])
    """
    # 1. Try type-specific template
    if appointment_type_id:
        try:
            template = DoctorIntakeFormTemplate.objects.prefetch_related("questions").get(
                doctor_id=doctor_id,
                appointment_type_id=appointment_type_id,
                is_active=True,
            )
            return template, list(template.ordered_questions)
        except DoctorIntakeFormTemplate.DoesNotExist:
            pass

    # 2. Try generic template (appointment_type=NULL)
    try:
        template = DoctorIntakeFormTemplate.objects.prefetch_related("questions").get(
            doctor_id=doctor_id,
            appointment_type__isnull=True,
            is_active=True,
        )
        return template, list(template.ordered_questions)
    except DoctorIntakeFormTemplate.DoesNotExist:
        pass

    return None, []


def get_rules_for_template(template):
    """
    Load all conditional display rules for a template.
    Returns a list of dicts ready for JSON serialization (for client-side JS).
    """
    if template is None:
        return []
    rules = DoctorIntakeRule.objects.filter(
        source_question__template=template,
    ).select_related("source_question", "target_question")

    return [
        {
            "source_question_id": r.source_question_id,
            "expected_value": r.expected_value,
            "operator": r.operator,
            "target_question_id": r.target_question_id,
            "action": r.action,
        }
        for r in rules
    ]


def evaluate_rules_server_side(questions, answers_dict, rules):
    """
    Re-evaluate conditional rules server-side to determine which questions
    are actually visible. Returns a set of visible question IDs.
    """
    visible = set()
    # Start: all questions visible, except those targeted by SHOW rules
    # (they start hidden until their condition is met)
    show_targets = set()
    hide_targets = set()

    for rule in rules:
        if rule.action == DoctorIntakeRule.Action.SHOW:
            show_targets.add(rule.target_question_id)
        elif rule.action == DoctorIntakeRule.Action.HIDE:
            hide_targets.add(rule.target_question_id)

    for q in questions:
        if q.id in show_targets:
            # Hidden by default; will be shown if condition met
            pass
        else:
            visible.add(q.id)

    # Evaluate each rule
    for rule in rules:
        source_answer = answers_dict.get(str(rule.source_question_id), "")
        match = False

        if rule.operator == DoctorIntakeRule.Operator.EQUALS:
            match = source_answer == rule.expected_value
        elif rule.operator == DoctorIntakeRule.Operator.NOT_EQUALS:
            match = source_answer != rule.expected_value
        elif rule.operator == DoctorIntakeRule.Operator.CONTAINS:
            match = rule.expected_value in source_answer
        elif rule.operator == DoctorIntakeRule.Operator.IN:
            if isinstance(source_answer, list):
                match = rule.expected_value in source_answer
            else:
                match = rule.expected_value in source_answer

        if match:
            if rule.action == DoctorIntakeRule.Action.SHOW:
                visible.add(rule.target_question_id)
            elif rule.action == DoctorIntakeRule.Action.HIDE:
                visible.discard(rule.target_question_id)

    return visible


def collect_and_validate_intake(post_data, files, questions, rules):
    """
    Collect answers from POST, validate required fields (respecting conditional rules),
    and validate file uploads (type + size).

    Returns: (answers_dict, file_data, errors)
      - answers_dict: {str(question_id): answer_text}
      - file_data: {str(question_id): uploaded_file}
      - errors: list of error strings
    """
    answers = {}
    file_data = {}

    for q in questions:
        key = f"intake_{q.id}"
        if q.field_type == DoctorIntakeQuestion.FieldType.MULTISELECT:
            value = post_data.getlist(key)
            if value:
                answers[str(q.id)] = "، ".join(value)
        elif q.field_type == DoctorIntakeQuestion.FieldType.FILE:
            uploaded = files.get(key)
            if uploaded:
                file_data[str(q.id)] = uploaded
        elif q.field_type == DoctorIntakeQuestion.FieldType.CHECKBOX:
            value = post_data.get(key, "")
            answers[str(q.id)] = value
        else:
            value = post_data.get(key, "").strip()
            if value:
                answers[str(q.id)] = value

    # Evaluate rules to determine visible questions
    db_rules = DoctorIntakeRule.objects.filter(
        source_question__template=questions[0].template if questions else None,
    ) if questions else DoctorIntakeRule.objects.none()
    visible_ids = evaluate_rules_server_side(questions, answers, db_rules)

    # Validate required fields + file constraints (only for visible questions)
    errors = []
    for q in questions:
        if q.id not in visible_ids:
            continue

        if q.field_type == DoctorIntakeQuestion.FieldType.FILE:
            uploaded = file_data.get(str(q.id))

            # Required check
            if q.is_required and not uploaded:
                errors.append(f'الحقل "{q.display_text}" مطلوب.')
                continue

            if uploaded:
                # ── File size validation ──
                if q.max_file_size_mb:
                    max_bytes = q.max_file_size_mb * 1024 * 1024
                    if uploaded.size > max_bytes:
                        errors.append(
                            f'الملف في "{q.display_text}" يتجاوز الحد الأقصى '
                            f'({q.max_file_size_mb} ميغابايت). '
                            f'حجم الملف: {uploaded.size / (1024 * 1024):.1f} ميغابايت.'
                        )

                # ── File extension validation ──
                if q.allowed_extensions:
                    file_ext = ""
                    if "." in uploaded.name:
                        file_ext = uploaded.name.rsplit(".", 1)[-1].lower()
                    allowed = [ext.lower().lstrip(".") for ext in q.allowed_extensions]
                    if file_ext not in allowed:
                        errors.append(
                            f'صيغة الملف في "{q.display_text}" غير مسموحة. '
                            f'الصيغ المسموحة: {", ".join(allowed)}. '
                            f'صيغة الملف المرفوع: .{file_ext}.'
                        )
        else:
            if q.is_required and not answers.get(str(q.id)):
                errors.append(f'الحقل "{q.display_text}" مطلوب.')

    return answers, file_data, errors


def save_intake_answers(appointment, questions, answers_dict, file_data, uploaded_by):
    """
    Create AppointmentAnswer records for each answered question,
    and AppointmentAttachment records for file uploads.
    """
    answer_objects = []
    for q in questions:
        text = answers_dict.get(str(q.id), "")
        if text or str(q.id) in file_data:
            answer_objects.append(
                AppointmentAnswer(
                    appointment=appointment,
                    question=q,
                    answer_text=text,
                )
            )
    if answer_objects:
        AppointmentAnswer.objects.bulk_create(answer_objects)

    # Save file attachments
    for q_id_str, uploaded_file in file_data.items():
        q = next((q for q in questions if str(q.id) == q_id_str), None)
        if q:
            AppointmentAttachment.objects.create(
                appointment=appointment,
                question=q,
                file=uploaded_file,
                original_name=uploaded_file.name,
                file_size=uploaded_file.size,
                mime_type=getattr(uploaded_file, "content_type", ""),
                uploaded_by=uploaded_by,
            )


# ─── Main Views ───────────────────────────────────────────────────────────


@login_required
def book_appointment_view(request, clinic_id):
    """
    Patient-facing booking page (multi-step HTMX form).

    Steps: Select Doctor → Select Type → Select Date/Time → Fill Intake Form → Confirm
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
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

    # Pre-selected doctor
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
            appointment_type_id = int(request.POST.get("appointment_type_id") or 0)
            appointment_date_str = request.POST.get("appointment_date", "").strip()
            appointment_time_str = request.POST.get("appointment_time", "").strip()
            reason = request.POST.get("reason", "").strip()

            # Validate description length
            if len(reason) > 1000:
                messages.error(request, "وصف الحالة الطبية يجب أن لا يتجاوز 1000 حرف.")
                return redirect(f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}")

            if not all([doctor_id, appointment_type_id, appointment_date_str, appointment_time_str]):
                messages.error(request, "يرجى ملء جميع الحقول المطلوبة.")
                return redirect(f"/appointments/book/{clinic_id}/?doctor_id={doctor_id}")

            appointment_date = datetime.strptime(appointment_date_str, "%Y-%m-%d").date()
            appointment_time = datetime.strptime(appointment_time_str, "%H:%M").time()

            # ── Collect and validate intake form answers ──
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

            # ── Also build legacy intake_responses for backward compat ──
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

            # ── Save structured AppointmentAnswer records ──
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
            messages.error(request, f"بيانات غير صالحة: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f"خطأ غير متوقع: {e}")

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
    """HTMX endpoint: Returns appointment types for a selected doctor."""
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
    """HTMX endpoint: Returns available time slots for a doctor on a date."""
    doctor_id = request.GET.get("doctor_id")
    date_str = request.GET.get("appointment_date") or request.GET.get("date")
    appointment_type_id = request.GET.get("appointment_type_id")

    if not all([doctor_id, date_str, appointment_type_id]):
        return render(request, "appointments/partials/time_slots.html", {"slots": []})

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        appointment_type = AppointmentType.objects.get(
            id=appointment_type_id, doctor_id=doctor_id, clinic_id=clinic_id, is_active=True,
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
        doctor_id=int(doctor_id), clinic_id=int(clinic_id),
        target_date=target_date, duration_minutes=appointment_type.duration_minutes,
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
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
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
                value = "، ".join(value)
            intake_display.append({
                "label": data.get("label", ""),
                "value": value,
            })

    # Get attachments
    attachments = appointment.attachments.all()

    context = {
        "appointment": appointment,
        "appointment_date_ar": format_date_ar(appointment.appointment_date),
        "intake_display": intake_display,
        "attachments": attachments,
    }
    return render(request, "appointments/booking_confirmation.html", context)