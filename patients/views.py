from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseForbidden
from django.db.models import Q

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from .serializers import PatientProfileSerializer
from .permissions import IsPatient
from .models import PatientProfile
from .forms import UserUpdateForm, PatientProfileUpdateForm
from clinics.models import Clinic, ClinicStaff
from doctors.models import Specialty, DoctorProfile, DoctorIntakeFormTemplate
from appointments.models import AppointmentType
from appointments.services.patient_appointments_service import (
    cancel_appointment,
    edit_appointment,
    get_patient_appointments,
)

User = get_user_model()


@login_required
def dashboard(request):
    return render(request, "patients/dashboard.html")


@login_required
def browse_doctors(request):
    """
    Patient-facing view: Browse all doctors grouped by clinic.
    Supports:
        - Search by doctor name, clinic name, or specialty name (?q=...)
        - Filter by specialty (?specialty_id=...)
        - Both combined
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    # Get all specialties for the filter bar
    all_specialties = Specialty.objects.all()

    # Get query params
    search_query = request.GET.get("q", "").strip()
    selected_specialty_id = request.GET.get("specialty_id")
    selected_specialty = None

    if selected_specialty_id:
        try:
            selected_specialty = Specialty.objects.get(id=selected_specialty_id)
        except Specialty.DoesNotExist:
            selected_specialty_id = None

    # --- Build filtered doctor user IDs ---
    # Start with all doctor/main_doctor users
    doctor_users = User.objects.filter(role__in=["DOCTOR", "MAIN_DOCTOR"])

    # Apply specialty filter
    if selected_specialty:
        users_with_specialty = DoctorProfile.objects.filter(
            specialties=selected_specialty
        ).values_list("user_id", flat=True)
        doctor_users = doctor_users.filter(id__in=users_with_specialty)

    # Apply search filter
    if search_query:
        # Search by: doctor name, specialty name (ar/en), clinic name
        # 1. Doctor name match
        name_match = Q(name__icontains=search_query)

        # 2. Specialty match → get user IDs of doctors with matching specialty
        specialty_user_ids = DoctorProfile.objects.filter(
            Q(specialties__name__icontains=search_query)
            | Q(specialties__name_ar__icontains=search_query)
        ).values_list("user_id", flat=True)
        specialty_match = Q(id__in=specialty_user_ids)

        # 3. Clinic name match → get user IDs of doctors at matching clinics
        matching_clinics = Clinic.objects.filter(
            name__icontains=search_query, is_active=True
        )
        matching_clinic_ids = set(matching_clinics.values_list("id", flat=True))

        # Doctors who are main_doctor of matching clinics
        main_doc_ids = matching_clinics.values_list("main_doctor_id", flat=True)
        # Doctors who are staff at matching clinics
        staff_doc_ids = ClinicStaff.objects.filter(
            clinic__in=matching_clinics, role="DOCTOR", is_active=True
        ).values_list("user_id", flat=True)

        clinic_match = Q(id__in=list(main_doc_ids) + list(staff_doc_ids))

        doctor_users = doctor_users.filter(name_match | specialty_match | clinic_match)
    else:
        matching_clinic_ids = set()

    filtered_doctor_ids = set(doctor_users.values_list("id", flat=True))

    # --- Build clinic → doctors mapping ---
    clinics = Clinic.objects.filter(is_active=True).select_related(
        "main_doctor", "city"
    )

    # If searching, only show clinics that have matching doctors
    if search_query or selected_specialty:
        relevant_clinic_ids = set()
        for clinic in clinics:
            if clinic.main_doctor and clinic.main_doctor.id in filtered_doctor_ids:
                relevant_clinic_ids.add(clinic.id)
            staff = ClinicStaff.objects.filter(
                clinic=clinic, role="DOCTOR", is_active=True
            ).values_list("user_id", flat=True)
            if set(staff) & filtered_doctor_ids:
                relevant_clinic_ids.add(clinic.id)
        # Also include clinics matched by name
        relevant_clinic_ids |= matching_clinic_ids
        clinics = clinics.filter(id__in=relevant_clinic_ids)

    doctors_by_clinic = []
    for clinic in clinics:
        doctors = []

        # Main doctor
        if clinic.main_doctor and clinic.main_doctor.id in filtered_doctor_ids:
            doctors.append(clinic.main_doctor)

        # Staff doctors
        staff_doctors = ClinicStaff.objects.filter(
            clinic=clinic,
            role="DOCTOR",
            is_active=True,
        ).select_related("user")

        for staff in staff_doctors:
            if staff.user not in doctors and staff.user.id in filtered_doctor_ids:
                doctors.append(staff.user)

        # If searching by clinic name, include the clinic even if
        # no doctors matched by name/specialty (they're still at that clinic)
        if not doctors and matching_clinic_ids and clinic.id in matching_clinic_ids:
            # Re-add all doctors of this clinic (unfiltered by name/specialty)
            if clinic.main_doctor:
                doctors.append(clinic.main_doctor)
            for staff in staff_doctors:
                if staff.user not in doctors:
                    doctors.append(staff.user)

        if doctors:
            # Prefetch doctor profiles for display
            doctor_ids = [d.id for d in doctors]
            profiles_map = {
                p.user_id: p
                for p in DoctorProfile.objects.filter(
                    user_id__in=doctor_ids
                ).prefetch_related("doctor_specialties__specialty")
            }

            doctors_with_profiles = []
            for doctor in doctors:
                profile = profiles_map.get(doctor.id)
                # Count active appointment types for this doctor at this clinic
                type_count = AppointmentType.objects.filter(
                    doctor=doctor, clinic=clinic, is_active=True
                ).count()
                # Check if doctor has an active intake form
                has_intake = DoctorIntakeFormTemplate.objects.filter(
                    doctor=doctor, is_active=True
                ).exists()
                doctors_with_profiles.append(
                    {
                        "user": doctor,
                        "profile": profile,
                        "primary_specialty": profile.primary_specialty if profile else None,
                        "secondary_specialties": list(profile.secondary_specialties) if profile else [],
                        "appointment_type_count": type_count,
                        "has_intake_form": has_intake,
                    }
                )

            doctors_by_clinic.append(
                {
                    "clinic": clinic,
                    "doctors": doctors_with_profiles,
                }
            )

    # Count total doctors found
    total_doctors = sum(len(c["doctors"]) for c in doctors_by_clinic)

    context = {
        "doctors_by_clinic": doctors_by_clinic,
        "all_specialties": all_specialties,
        "selected_specialty": selected_specialty,
        "search_query": search_query,
        "total_doctors": total_doctors,
    }
    return render(request, "patients/browse_doctors.html", context)


@login_required
def clinics_list(request):
    """
    Patient-facing view: Browse all active clinics with search and city filter.

    Supports:
        - Search by clinic name, address, or specialization (?q=...)
        - Filter by city (?city_id=...)
        - Both combined

    Security:
        - @login_required: unauthenticated users redirected to login.
        - PATIENT role enforced: non-patients receive HTTP 403.
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    from accounts.models import City

    search_query = request.GET.get("q", "").strip()
    selected_city_id = request.GET.get("city_id", "").strip()
    selected_city = None

    if selected_city_id:
        try:
            selected_city = City.objects.get(id=selected_city_id)
        except City.DoesNotExist:
            selected_city_id = ""

    # Base queryset: active clinics with related objects for display
    clinics_qs = Clinic.objects.filter(is_active=True).select_related(
        "main_doctor", "city"
    )

    # Apply search filter
    if search_query:
        clinics_qs = clinics_qs.filter(
            Q(name__icontains=search_query)
            | Q(address__icontains=search_query)
            | Q(specialization__icontains=search_query)
        )

    # Apply city filter
    if selected_city:
        clinics_qs = clinics_qs.filter(city=selected_city)

    clinics_qs = clinics_qs.order_by("-created_at")

    # Annotate each clinic with doctor count
    clinics_data = []
    for clinic in clinics_qs:
        # Count main doctor + active staff doctors at this clinic
        staff_doctor_count = ClinicStaff.objects.filter(
            clinic=clinic, role="DOCTOR", is_active=True
        ).count()
        doctor_count = staff_doctor_count + (1 if clinic.main_doctor else 0)

        clinics_data.append(
            {
                "clinic": clinic,
                "doctor_count": doctor_count,
            }
        )

    all_cities = City.objects.order_by("name")

    context = {
        "clinics_data": clinics_data,
        "all_cities": all_cities,
        "selected_city": selected_city,
        "search_query": search_query,
        "total_clinics": len(clinics_data),
    }
    return render(request, "patients/clinics_list.html", context)


@login_required
def my_appointments(request):
    """
    Patient-facing view: View upcoming and past appointments.

    Security:
    - @login_required enforced via decorator.
    - Patient role enforced: non-patients receive HTTP 403.
    - Data is scoped strictly to request.user — no cross-patient exposure.
    """
    if getattr(request.user, "role", None) != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    try:
        patient_profile = request.user.patient_profile
    except PatientProfile.DoesNotExist:
        patient_profile = PatientProfile.objects.create(user=request.user)

    data = get_patient_appointments(request.user)

    context = {
        "upcoming_appointments": data["upcoming"],
        "past_appointments": data["past"],
        "upcoming_count": data["upcoming_count"],
        "past_count": data["past_count"],
        "upcoming_has_more": False,
        "past_has_more": False,
        "patient_profile": patient_profile,
    }
    return render(request, "patients/my_appointments.html", context)


@login_required
def cancel_appointment_view(request, appointment_id):
    """
    Cancel a patient's upcoming appointment.

    Security:
    - @login_required: unauthenticated users redirected to login.
    - Patient role enforced: non-PATIENT roles receive HTTP 403.
    - POST only: GET requests are rejected to prevent CSRF-less cancellation.
    - Ownership enforced inside cancel_appointment() at ORM level.
    - Time-based policy enforced inside cancel_appointment() service.
    """
    if getattr(request.user, "role", None) != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    if request.method != "POST":
        return HttpResponseForbidden("Method not allowed.")

    try:
        cancel_appointment(appointment_id=appointment_id, patient=request.user)
        messages.success(request, "تم إلغاء الموعد بنجاح.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("patients:my_appointments")


@login_required
def edit_appointment_view(request, appointment_id):
    """
    Edit a patient's upcoming appointment (date, time, type, reason, intake form).
    Doctor cannot be changed.

    GET:  Show the edit form pre-filled with current values + intake answers.
    POST: Validate and apply the edit including intake form updates.
    """
    import json
    from datetime import datetime as dt_cls, date as date_cls
    from appointments.models import Appointment, AppointmentType, AppointmentAnswer, AppointmentAttachment
    from appointments.views import (
        get_active_intake_template, get_rules_for_template,
        collect_and_validate_intake, save_intake_answers,
    )
    from doctors.services import generate_slots_for_date

    if getattr(request.user, "role", None) != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    # Fetch appointment with ownership check
    try:
        appointment = Appointment.objects.select_related(
            "doctor", "clinic", "appointment_type",
            "doctor__doctor_profile",
        ).get(id=appointment_id, patient=request.user)
    except Appointment.DoesNotExist:
        messages.error(request, "الموعد غير موجود.")
        return redirect("patients:my_appointments")

    # Check if editable
    if not appointment.can_patient_edit:
        if appointment.patient_edit_count >= Appointment.MAX_PATIENT_EDITS:
            messages.error(
                request,
                f"لقد استنفدت الحد الأقصى من التعديلات ({Appointment.MAX_PATIENT_EDITS})."
            )
        else:
            messages.error(request, "لا يمكن تعديل هذا الموعد.")
        return redirect("patients:my_appointments")

    # Get appointment types for this doctor+clinic
    appointment_types = AppointmentType.objects.filter(
        doctor=appointment.doctor,
        clinic=appointment.clinic,
        is_active=True,
    )

    if request.method == "POST":
        try:
            new_type_id = int(request.POST.get("appointment_type_id") or 0)
            new_date_str = request.POST.get("appointment_date", "").strip()
            new_time_str = request.POST.get("appointment_time", "").strip()
            new_reason = request.POST.get("reason", "").strip()

            if not all([new_type_id, new_date_str, new_time_str]):
                messages.error(request, "يرجى ملء جميع الحقول المطلوبة.")
                return redirect("patients:edit_appointment", appointment_id=appointment_id)

            new_date = dt_cls.strptime(new_date_str, "%Y-%m-%d").date()
            new_time = dt_cls.strptime(new_time_str, "%H:%M").time()

            if len(new_reason) > 1000:
                messages.error(request, "وصف الحالة الطبية يجب أن لا يتجاوز 1000 حرف.")
                return redirect("patients:edit_appointment", appointment_id=appointment_id)

            # ── Collect and validate intake form answers ──
            template, questions = get_active_intake_template(
                appointment.doctor_id, new_type_id
            )
            answers_dict = {}
            file_data = {}

            if questions:
                answers_dict, file_data, validation_errors = collect_and_validate_intake(
                    request.POST, request.FILES, questions, []
                )
                if validation_errors:
                    for err in validation_errors:
                        messages.error(request, err)
                    return redirect("patients:edit_appointment", appointment_id=appointment_id)

            updated = edit_appointment(
                appointment_id=appointment_id,
                patient=request.user,
                new_date=new_date,
                new_time=new_time,
                new_type_id=new_type_id,
                new_reason=new_reason,
            )

            # ── Update intake answers ──
            if questions:
                # Delete old answers + attachments, then re-save
                AppointmentAnswer.objects.filter(appointment=updated).delete()
                # Keep existing file attachments unless new ones are uploaded
                if file_data:
                    # Delete old attachments for questions that have new uploads
                    for q_id_str in file_data:
                        AppointmentAttachment.objects.filter(
                            appointment=updated,
                            question_id=int(q_id_str),
                        ).delete()
                save_intake_answers(updated, questions, answers_dict, file_data, request.user)

            messages.success(
                request,
                f"تم تعديل الموعد بنجاح! المتبقي: {updated.edits_remaining} تعديل(ات)."
            )
            return redirect("patients:my_appointments")

        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("patients:edit_appointment", appointment_id=appointment_id)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            messages.error(request, f"خطأ غير متوقع: {exc}")
            return redirect("patients:edit_appointment", appointment_id=appointment_id)

    # ── GET — render edit form with intake data ──
    # Load intake form for current appointment type
    template, questions = get_active_intake_template(
        appointment.doctor_id,
        appointment.appointment_type_id,
    )

    # Fetch existing answers as dict {question_id: answer_text}
    existing_answers = {}
    if questions:
        answers_qs = AppointmentAnswer.objects.filter(
            appointment=appointment,
        ).values_list("question_id", "answer_text")
        existing_answers = {str(qid): text for qid, text in answers_qs}

    # Fetch existing attachments grouped by question
    existing_attachments = {}
    if questions:
        atts = AppointmentAttachment.objects.filter(appointment=appointment)
        for att in atts:
            q_id = str(att.question_id) if att.question_id else "none"
            existing_attachments.setdefault(q_id, []).append(att)

    # Annotate each question with its existing answer
    for q in questions:
        q.existing_answer = existing_answers.get(str(q.id), "")
        q.existing_files = existing_attachments.get(str(q.id), [])

    rules_json = ""
    if template:
        rules_json = json.dumps(get_rules_for_template(template), ensure_ascii=False)

    context = {
        "appointment": appointment,
        "appointment_types": appointment_types,
        "today": date_cls.today().isoformat(),
        "edits_remaining": appointment.edits_remaining,
        "form_template": template,
        "questions": questions,
        "rules_json": rules_json,
    }
    return render(request, "patients/edit_appointment.html", context)


@login_required
def load_edit_slots(request, appointment_id):
    """HTMX endpoint: load available slots for a date when editing an appointment."""
    from datetime import datetime as dt_cls
    from appointments.models import Appointment, AppointmentType
    from doctors.services import generate_slots_for_date

    if getattr(request.user, "role", None) != "PATIENT":
        return HttpResponse("")

    try:
        appointment = Appointment.objects.select_related(
            "appointment_type"
        ).get(id=appointment_id, patient=request.user)
    except Appointment.DoesNotExist:
        return HttpResponse("<p class='text-red-500 text-sm'>الموعد غير موجود.</p>")

    date_str = request.GET.get("appointment_date", "")
    type_id = request.GET.get("appointment_type_id", "")

    if not date_str:
        return HttpResponse("")

    try:
        target_date = dt_cls.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponse("<p class='text-red-500 text-sm'>تاريخ غير صالح.</p>")

    # Use selected type or current type
    apt_type = appointment.appointment_type
    if type_id:
        try:
            apt_type = AppointmentType.objects.get(
                id=int(type_id), doctor=appointment.doctor,
                clinic=appointment.clinic, is_active=True,
            )
        except (AppointmentType.DoesNotExist, ValueError):
            pass

    if not apt_type:
        return HttpResponse("<p class='text-gray-400 text-sm'>لا يوجد نوع موعد محدد.</p>")

    slots = generate_slots_for_date(
        doctor_id=appointment.doctor_id,
        clinic_id=appointment.clinic_id,
        target_date=target_date,
        duration_minutes=apt_type.duration_minutes,
    )

    # Mark current appointment's slot as available (it's the patient's own slot)
    is_same_date = (target_date == appointment.appointment_date)
    current_time = appointment.appointment_time

    html_parts = []
    if not slots:
        html_parts.append('<p class="text-gray-400 text-sm">لا توجد مواعيد متاحة لهذا اليوم.</p>')
    else:
        html_parts.append('<div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">')
        for slot in slots:
            time_str = slot["time"].strftime("%H:%M")
            is_own_slot = is_same_date and slot["time"] == current_time
            available = slot["is_available"] or is_own_slot
            is_current = is_own_slot

            if available:
                extra_classes = ""
                badge = ""
                if is_current:
                    extra_classes = "ring-2 ring-primary-500 bg-primary-50 dark:bg-primary-900/20"
                    badge = '<span class="text-[10px] text-primary-500">الحالي</span>'
                html_parts.append(
                    f'<button type="button" class="slot-btn px-3 py-2 text-sm rounded-lg '
                    f'border border-gray-200 dark:border-slate-600 hover:border-secondary-400 '
                    f'transition-colors text-center {extra_classes}" data-time="{time_str}">'
                    f'{time_str}{badge}</button>'
                )
            else:
                html_parts.append(
                    f'<button type="button" disabled class="px-3 py-2 text-sm rounded-lg '
                    f'border border-gray-100 dark:border-slate-700 text-gray-300 dark:text-gray-600 '
                    f'cursor-not-allowed line-through text-center">{time_str}</button>'
                )
        html_parts.append('</div>')

    return HttpResponse("\n".join(html_parts))


@login_required
def load_edit_intake_form(request, appointment_id):
    """
    HTMX endpoint: load intake form for the selected appointment type during edit.
    Pre-fills existing answers from AppointmentAnswer records.
    """
    import json
    from appointments.models import Appointment, AppointmentAnswer, AppointmentAttachment
    from appointments.views import get_active_intake_template, get_rules_for_template

    if getattr(request.user, "role", None) != "PATIENT":
        return HttpResponse("")

    try:
        appointment = Appointment.objects.get(id=appointment_id, patient=request.user)
    except Appointment.DoesNotExist:
        return HttpResponse("")

    type_id = request.GET.get("appointment_type_id", "")
    if not type_id:
        type_id = appointment.appointment_type_id

    template, questions = get_active_intake_template(appointment.doctor_id, type_id)

    if not template or not questions:
        return HttpResponse(
            '<p class="text-gray-400 text-sm py-3">'
            '<i class="fa-solid fa-circle-info ml-1"></i>'
            'لا يوجد نموذج استقبال لنوع الموعد المحدد.</p>'
        )

    # Fetch existing answers
    answers_qs = AppointmentAnswer.objects.filter(
        appointment=appointment,
    ).values_list("question_id", "answer_text")
    existing_answers = {str(qid): text for qid, text in answers_qs}

    # Fetch existing attachments
    existing_attachments = {}
    atts = AppointmentAttachment.objects.filter(appointment=appointment)
    for att in atts:
        q_id = str(att.question_id) if att.question_id else "none"
        existing_attachments.setdefault(q_id, []).append(att)

    # Annotate questions
    for q in questions:
        q.existing_answer = existing_answers.get(str(q.id), "")
        q.existing_files = existing_attachments.get(str(q.id), [])

    rules_json = json.dumps(get_rules_for_template(template), ensure_ascii=False)

    return render(
        request,
        "patients/partials/edit_intake_form.html",
        {
            "form_template": template,
            "questions": questions,
            "rules_json": rules_json,
        },
    )


@login_required
def book_appointment(request, clinic_id):
    """
    Redirect to the appointments app booking view.
    Preserves query parameters (e.g., doctor_id).
    """
    query_string = request.META.get("QUERY_STRING", "")
    url = f"/appointments/book/{clinic_id}/"
    if query_string:
        url += f"?{query_string}"
    return redirect(url)


@login_required
def profile(request):
    """
    Render patient profile page.
    Strictly restricted to PATIENT role.
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    try:
        patient_profile = request.user.patient_profile
    except PatientProfile.DoesNotExist:
        patient_profile = PatientProfile.objects.create(user=request.user)

    context = {
        "profile": patient_profile,
        "user": request.user,
    }
    return render(request, "patients/profile.html", context)


@login_required
def edit_profile(request):
    """
    Render and handle patient profile edit form.
    Strictly restricted to PATIENT role.
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    try:
        patient_profile = request.user.patient_profile
    except PatientProfile.DoesNotExist:
        patient_profile = PatientProfile.objects.create(user=request.user)

    if request.method == "POST":
        if request.POST.get("delete_avatar") == "true":
            if patient_profile.avatar:
                patient_profile.avatar.delete()

        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = PatientProfileUpdateForm(
            request.POST, request.FILES, instance=patient_profile
        )

        if u_form.is_valid() and p_form.is_valid():
            old_email = request.user.email or ""
            new_email = u_form.cleaned_data.get("email") or ""

            email_changed = False
            if new_email and old_email:
                email_changed = new_email.lower().strip() != old_email.lower().strip()
            elif new_email and not old_email:
                email_changed = True

            if email_changed:
                request.session["pending_email_change"] = new_email
                messages.info(
                    request, "يرجى التحقق من البريد الإلكتروني الجديد لإتمام التغيير."
                )
                return redirect("accounts:change_email_request")

            u_form.save()
            p_form.save()
            messages.success(request, "تم تحديث ملفك الشخصي بنجاح!")
            return redirect("patients:profile")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = PatientProfileUpdateForm(instance=patient_profile)

    context = {"u_form": u_form, "p_form": p_form}
    return render(request, "patients/edit_profile.html", context)


class PatientProfileAPIView(APIView):
    """
    API endpoint for patients to view their own profile.
    """

    permission_classes = [IsAuthenticated, IsPatient]

    def get(self, request):
        try:
            patient_profile = request.user.patient_profile
        except PatientProfile.DoesNotExist:
            return Response(
                {"detail": "Patient profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PatientProfileSerializer(patient_profile)
        return Response(serializer.data)