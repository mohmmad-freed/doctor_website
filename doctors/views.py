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

    # Appointment types for this clinic
    appointment_types = AppointmentType.objects.filter(
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
        clinic_id=clinic_id,
        is_active=True,
    ).order_by("name")

    context = {
        "doctor": doctor,
        "clinic_id": clinic_id,
        "appointment_types": appointment_types,
    }
    return render(request, "doctors/doctor_appointment_types.html", context)


# ============================================
# DOCTOR INVITATIONS FLOW
# ============================================

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from clinics.models import ClinicInvitation
from clinics.services import accept_invitation, reject_invitation
from accounts.backends import PhoneNumberAuthBackend

@login_required
def doctor_invitations_inbox(request):
    """View pending invitations for the logged-in doctor."""
    user = request.user
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    
    invitations = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, 
        status="PENDING"
    ).select_related('clinic', 'invited_by').prefetch_related('specialties').order_by('-created_at')
    
    return render(request, "doctors/invitations_inbox.html", {
        "invitations": invitations,
    })

@login_required
def accept_invitation_view(request, invitation_id):
    """Action to accept an invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id)
    
    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, f"تم الانضمام بنجاح إلى عيادة {staff.clinic.name}.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")
            
    return redirect(reverse("doctors:doctor_invitations_inbox"))

@login_required
def reject_invitation_view(request, invitation_id):
    """Action to reject an invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id)
    
    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, "تم رفض الدعوة.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")
            
    return redirect(reverse("doctors:doctor_invitations_inbox"))


def guest_accept_invitation_view(request, token):
    """
    Public endpoint for SMS link. 
    Shows generic error if token invalid/expired.
    If valid, redirects to login/reg storing token in session.
    """
    try:
        invitation = ClinicInvitation.objects.get(token=token)
    except ClinicInvitation.DoesNotExist:
        return render(request, "doctors/invalid_invitation.html", {
            "error": "رابط الدعوة غير صالح أو قد تم استخدامه مسبقاً."
        })
        
    if invitation.status != "PENDING" or invitation.is_expired:
         return render(request, "doctors/invalid_invitation.html", {
            "error": "انتهت صلاحية هذه الدعوة أو لم تعد متاحة."
        })
        
    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
             # Already logged in as the right doctor, redirect to inbox to accept
             return redirect(reverse("doctors:doctor_invitations_inbox"))
        else:
             # Logged in as someone else (wrong phone)
             return render(request, "doctors/invalid_invitation.html", {
                "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة. يرجى تسجيل الدخول بالحساب الصحيح."
            })
            
    # Unauthenticated but token is valid: store generic next url and redirect to registration
    request.session["next_after_login"] = reverse("doctors:doctor_invitations_inbox")
    
    # Pre-fill phone if we want to (optional, but good UX)
    request.session["registration_phone"] = invitation.doctor_phone
    
    if invitation.role == "SECRETARY":
        messages.info(request, f"مرحباً {invitation.doctor_name}، أنت مدعو للانضمام كـ سكرتير/ة في {invitation.clinic.name}. يرجى إدخال رقم هاتفك لإنشاء حسابك أو تسجيل الدخول.")
    else:
        messages.info(request, f"مرحباً د. {invitation.doctor_name}، أنت مدعو للانضمام إلى {invitation.clinic.name}. يرجى إدخال رقم هاتفك لإنشاء حسابك أو تسجيل الدخول.")
        
    return redirect(reverse("accounts:register_patient_phone"))
