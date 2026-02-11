from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseForbidden
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from .serializers import PatientProfileSerializer
from .permissions import IsPatient
from .models import PatientProfile
from .forms import UserUpdateForm, PatientProfileUpdateForm
from clinics.models import Clinic

User = get_user_model()


@login_required
def dashboard(request):
    return render(request, "patients/dashboard.html")


@login_required
def browse_doctors(request):
    """
    Patient-facing view: Browse all doctors grouped by clinic.
    Patients can view doctors from all clinics globally.
    """
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    # Get all active clinics with their doctors
    clinics = Clinic.objects.filter(is_active=True).select_related("city", "main_doctor")

    doctors_by_clinic = []
    for clinic in clinics:
        # Collect doctors for this clinic:
        # 1. The main doctor (clinic owner)
        # 2. Staff doctors (from ClinicStaff)
        doctors = []

        # Main doctor
        if clinic.main_doctor:
            doctors.append(clinic.main_doctor)

        # Staff doctors
        from clinics.models import ClinicStaff

        staff_doctors = ClinicStaff.objects.filter(
            clinic=clinic,
            role="DOCTOR",
            is_active=True,
        ).select_related("user")

        for staff in staff_doctors:
            if staff.user not in doctors:
                doctors.append(staff.user)

        if doctors:
            doctors_by_clinic.append(
                {
                    "clinic": clinic,
                    "doctors": doctors,
                }
            )

    context = {
        "doctors_by_clinic": doctors_by_clinic,
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