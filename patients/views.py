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
from doctors.models import Specialty, DoctorProfile

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

        # 3. Clinic name match → get user IDs of doctors in matching clinics
        #    (main_doctor of clinic OR staff at clinic)
        matching_clinics = Clinic.objects.filter(
            name__icontains=search_query, is_active=True
        )
        clinic_main_ids = matching_clinics.values_list("main_doctor_id", flat=True)
        clinic_staff_ids = ClinicStaff.objects.filter(
            clinic__in=matching_clinics,
            role="DOCTOR",
            is_active=True,
        ).values_list("user_id", flat=True)
        clinic_match = Q(id__in=clinic_main_ids) | Q(id__in=clinic_staff_ids)

        doctor_users = doctor_users.filter(name_match | specialty_match | clinic_match)

    filtered_doctor_ids = set(doctor_users.values_list("id", flat=True))

    # --- Build clinic → doctors grouping ---
    clinics = Clinic.objects.filter(is_active=True).select_related("city", "main_doctor")

    # If searching by clinic name, only show matching clinics
    # Otherwise show all clinics that have matching doctors
    if search_query:
        # Show clinics that either match the name OR contain matching doctors
        matching_clinic_ids = set(
            Clinic.objects.filter(
                name__icontains=search_query, is_active=True
            ).values_list("id", flat=True)
        )
    else:
        matching_clinic_ids = None  # show all

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
                doctors_with_profiles.append(
                    {
                        "user": doctor,
                        "profile": profile,
                        "primary_specialty": profile.primary_specialty if profile else None,
                        "secondary_specialties": list(profile.secondary_specialties) if profile else [],
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
    return HttpResponse("Available Clinics - Coming Soon!")


@login_required
def my_appointments(request):
    return HttpResponse("My Appointments - Coming Soon!")


@login_required
def book_appointment(request, clinic_id):
    return HttpResponse(f"Book Appointment at Clinic {clinic_id} - Coming Soon!")


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