from datetime import datetime, date

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.http import HttpResponse

from appointments.models import Appointment, AppointmentType
from clinics.models import ClinicStaff
from .models import DoctorAvailability, DoctorProfile, DoctorVerification, ClinicDoctorCredential
from .services import generate_slots_for_date

User = get_user_model()


# ============================================
# DOCTOR DASHBOARD
# ============================================


@login_required
def dashboard(request):
    """Full doctor dashboard with verification status, clinic memberships, and today's appointments."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    # Identity verification
    verification = DoctorVerification.objects.filter(user=user).first()

    # Clinic memberships + credential status
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("added_at")
    )

    clinic_cards = []
    for m in memberships:
        credentials = ClinicDoctorCredential.objects.filter(
            doctor=user, clinic=m.clinic
        ).select_related("specialty")
        all_verified = credentials.exists() and all(
            c.credential_status == "CREDENTIALS_VERIFIED" for c in credentials
        )
        clinic_cards.append({
            "membership": m,
            "clinic": m.clinic,
            "role": m.role,
            "credentials": credentials,
            "all_verified": all_verified,
        })

    # Today's appointments across all clinics
    today = date.today()
    todays_appointments = (
        Appointment.objects.filter(
            doctor=user,
            appointment_date=today,
        )
        .select_related("patient", "clinic", "appointment_type")
        .order_by("appointment_time")
    )

    # Pending invitations count
    from accounts.backends import PhoneNumberAuthBackend
    from clinics.models import ClinicInvitation
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    pending_invitations_count = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, status="PENDING"
    ).count()

    # Profile completeness
    profile = DoctorProfile.objects.filter(user=user).first()
    profile_complete = bool(
        profile and profile.bio and profile.years_of_experience
    )

    # Visibility check
    identity_verified = bool(
        verification and verification.identity_status == "IDENTITY_VERIFIED"
    )

    return render(request, "doctors/doctor_dashboard.html", {
        "verification": verification,
        "identity_verified": identity_verified,
        "clinic_cards": clinic_cards,
        "todays_appointments": todays_appointments,
        "pending_invitations_count": pending_invitations_count,
        "profile": profile,
        "profile_complete": profile_complete,
        "today": today,
    })


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
    Public endpoint for email/SMS link.
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

    # Check if the invited phone belongs to an existing user.
    from django.contrib.auth import get_user_model as _get_user_model
    _User = _get_user_model()
    existing_user = _User.objects.filter(phone=invitation.doctor_phone).first()

    inbox_url = reverse("doctors:doctor_invitations_inbox")

    if existing_user:
        login_url = reverse("accounts:login") + f"?next={inbox_url}"
        if invitation.role == "SECRETARY":
            messages.info(
                request,
                f"مرحباً {invitation.doctor_name}، لديك حساب بالفعل. يرجى تسجيل الدخول لقبول الدعوة."
            )
        else:
            messages.info(
                request,
                f"مرحباً د. {invitation.doctor_name}، لديك حساب بالفعل. يرجى تسجيل الدخول لقبول الدعوة."
            )
        return redirect(login_url)

    # No account yet: store next url and pre-fill phone, then send to registration.
    request.session["next_after_login"] = inbox_url
    request.session["registration_phone"] = invitation.doctor_phone

    if invitation.role == "SECRETARY":
        messages.info(request, f"مرحباً {invitation.doctor_name}، أنت مدعو للانضمام كـ سكرتير/ة في {invitation.clinic.name}. يرجى إدخال رقم هاتفك لإنشاء حسابك أو تسجيل الدخول.")
    else:
        messages.info(request, f"مرحباً د. {invitation.doctor_name}، أنت مدعو للانضمام إلى {invitation.clinic.name}. يرجى إدخال رقم هاتفك لإنشاء حسابك أو تسجيل الدخول.")

    return redirect(reverse("accounts:register_patient_phone"))


# ============================================
# DOCTOR VERIFICATION & CREDENTIAL UPLOAD
# ============================================

from doctors.models import DoctorVerification, ClinicDoctorCredential
from django import forms as django_forms
from core.validators.file_validators import validate_file_extension, validate_file_signature, validate_file_size


class DoctorCredentialUploadForm(django_forms.Form):
    """Form for uploading doctor identity verification documents."""
    identity_document = django_forms.FileField(
        label="وثيقة الهوية (بطاقة هوية / جواز سفر)",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".jpg,.jpeg,.png,.pdf"}),
    )
    medical_license = django_forms.FileField(
        label="رخصة مزاولة المهنة الطبية",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".jpg,.jpeg,.png,.pdf"}),
    )

    def clean_identity_document(self):
        f = self.cleaned_data.get("identity_document")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f

    def clean_medical_license(self):
        f = self.cleaned_data.get("medical_license")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f


@login_required
def doctor_verification_status(request):
    """Show the doctor's dual-layer verification status."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))

    verification = DoctorVerification.objects.filter(user=user).first()
    credentials = ClinicDoctorCredential.objects.filter(
        doctor=user
    ).select_related("clinic", "specialty").order_by("clinic__name")

    return render(request, "doctors/verification_status.html", {
        "verification": verification,
        "credentials": credentials,
    })


@login_required
def doctor_upload_credentials(request):
    """Upload identity documents for platform verification."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))

    verification, _ = DoctorVerification.objects.get_or_create(
        user=user,
        defaults={"identity_status": "IDENTITY_UNVERIFIED"},
    )

    if request.method == "POST":
        form = DoctorCredentialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            changed = False
            if form.cleaned_data.get("identity_document"):
                verification.identity_document = form.cleaned_data["identity_document"]
                changed = True
            if form.cleaned_data.get("medical_license"):
                verification.medical_license = form.cleaned_data["medical_license"]
                changed = True

            if changed:
                if verification.identity_status in ("IDENTITY_UNVERIFIED", "IDENTITY_REJECTED"):
                    verification.identity_status = "IDENTITY_PENDING_REVIEW"
                verification.save()
                messages.success(request, "تم رفع المستندات بنجاح. سيتم مراجعتها من قبل الإدارة.")
            else:
                messages.warning(request, "يرجى اختيار ملف واحد على الأقل.")

            return redirect(reverse("doctors:verification_status"))
    else:
        form = DoctorCredentialUploadForm()

    return render(request, "doctors/upload_credentials.html", {
        "form": form,
        "verification": verification,
    })


# ============================================
# DOCTOR PROFILE
# ============================================


class DoctorProfileForm(django_forms.Form):
    """Form for editing the doctor's public profile."""
    bio = django_forms.CharField(
        label="نبذة عنك",
        required=False,
        widget=django_forms.Textarea(attrs={
            "class": "form-control",
            "rows": 4,
            "placeholder": "اكتب نبذة مختصرة عن خبرتك وتخصصاتك...",
        }),
    )
    years_of_experience = django_forms.IntegerField(
        label="سنوات الخبرة",
        required=False,
        min_value=0,
        max_value=70,
        widget=django_forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "مثال: 10",
        }),
    )


@login_required
def doctor_profile_view(request):
    """View and edit the doctor's public profile."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))

    profile, _ = DoctorProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        form = DoctorProfileForm(request.POST)
        if form.is_valid():
            profile.bio = form.cleaned_data.get("bio", "")
            profile.years_of_experience = form.cleaned_data.get("years_of_experience")
            profile.save()
            messages.success(request, "تم تحديث الملف الشخصي بنجاح.")
            return redirect(reverse("doctors:doctor_profile"))
    else:
        form = DoctorProfileForm(initial={
            "bio": profile.bio,
            "years_of_experience": profile.years_of_experience,
        })

    # Specialties (read-only, managed via invitations)
    specialties = profile.specialties.all()

    # Clinic memberships
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
    )

    return render(request, "doctors/doctor_profile.html", {
        "form": form,
        "profile": profile,
        "specialties": specialties,
        "memberships": memberships,
    })


# ============================================
# PER-CLINIC CREDENTIAL UPLOAD
# ============================================


class ClinicCredentialUploadForm(django_forms.Form):
    """Form for uploading a specialty certificate for a specific clinic-credential."""
    specialty_certificate = django_forms.FileField(
        label="شهادة التخصص",
        required=True,
        widget=django_forms.ClearableFileInput(attrs={
            "class": "form-control",
            "accept": ".jpg,.jpeg,.png,.pdf",
        }),
    )

    def clean_specialty_certificate(self):
        f = self.cleaned_data.get("specialty_certificate")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f


@login_required
def doctor_upload_clinic_credential(request, credential_id):
    """Upload a specialty certificate for a specific clinic credential."""
    user = request.user
    credential = get_object_or_404(
        ClinicDoctorCredential, id=credential_id, doctor=user
    )

    if request.method == "POST":
        form = ClinicCredentialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            credential.specialty_certificate = form.cleaned_data["specialty_certificate"]
            if credential.credential_status in ("CREDENTIALS_PENDING", "CREDENTIALS_REJECTED"):
                credential.credential_status = "CREDENTIALS_PENDING"
            credential.save()
            messages.success(
                request,
                f"تم رفع شهادة التخصص ({credential.specialty.name_ar}) بنجاح. سيتم مراجعتها."
            )
            return redirect(reverse("doctors:verification_status"))
    else:
        form = ClinicCredentialUploadForm()

    return render(request, "doctors/upload_clinic_credential.html", {
        "form": form,
        "credential": credential,
    })
