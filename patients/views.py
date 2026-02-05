from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from .serializers import PatientProfileSerializer
from .permissions import IsPatient
from .models import PatientProfile
from .forms import UserUpdateForm, PatientProfileUpdateForm


@login_required
def dashboard(request):
    return render(request, "patients/dashboard.html")


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
    # Security: Verify role
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    # Fetch profile safely (OneToOne)
    try:
        profile = request.user.patient_profile
    except PatientProfile.DoesNotExist:
        # Auto-create if missing (though registration should have handled this)
        profile = PatientProfile.objects.create(user=request.user)

    context = {
        "profile": profile,
        "user": request.user,  # Explicitly passing user for clarity, though request.user is available
    }
    return render(request, "patients/profile.html", context)


@login_required
def edit_profile(request):
    """
    Render and handle patient profile edit form.
    Strictly restricted to PATIENT role.
    """
    # Security: Verify role
    role = getattr(request.user, "role", None)
    if role != "PATIENT":
        return HttpResponseForbidden("Unauthorized: This page is for patients only.")

    # Ensure profile exists
    try:
        profile = request.user.patient_profile
    except PatientProfile.DoesNotExist:
        profile = PatientProfile.objects.create(user=request.user)

    if request.method == "POST":
        # Handle Avatar Deletion
        if request.POST.get("delete_avatar") == "true":
            if profile.avatar:
                profile.avatar.delete()
                # If we only delete, we shouldn't continue to validate forms with old data necessarily,
                # but standard flow usually submits everything.
                # Simplest approach: Delete then continue processing forms (which might have new data).

        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = PatientProfileUpdateForm(request.POST, request.FILES, instance=profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, "تم تحديث ملفك الشخصي بنجاح!")
            return redirect("patients:profile")
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = PatientProfileUpdateForm(instance=profile)

    context = {"u_form": u_form, "p_form": p_form}
    return render(request, "patients/edit_profile.html", context)


class PatientProfileAPIView(APIView):
    """
    API endpoint for patients to view their own profile.
    """

    permission_classes = [IsAuthenticated, IsPatient]

    def get(self, request):
        try:
            profile = request.user.patient_profile
        except PatientProfile.DoesNotExist:
            return Response(
                {"detail": "Patient profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PatientProfileSerializer(profile)
        return Response(serializer.data)
