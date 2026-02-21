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
    Display the patient's upcoming and past appointments.

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
        # Pagination readiness flags — True when more items exist than currently shown.
        # The view does not apply limits today, so these are always False.
        # When a limit is introduced, pass upcoming_limit/past_limit and set these accordingly.
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