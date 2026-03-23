from datetime import datetime, date

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.http import HttpResponse

from appointments.models import Appointment, AppointmentType
from clinics.models import ClinicStaff
from .models import DoctorAvailability, DoctorProfile, DoctorVerification, ClinicDoctorCredential, DoctorIntakeFormTemplate, DoctorIntakeQuestion, DoctorIntakeRule
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

    # Upcoming appointments this week (next 7 days, not today)
    from datetime import timedelta
    week_end = today + timedelta(days=7)
    upcoming_count = Appointment.objects.filter(
        doctor=user,
        appointment_date__gt=today,
        appointment_date__lte=week_end,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
    ).count()

    # Total unique patients seen
    from django.db.models import Count as _Count
    total_patients_count = (
        Appointment.objects.filter(doctor=user)
        .exclude(status=Appointment.Status.CANCELLED)
        .values("patient_id")
        .distinct()
        .count()
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
        "upcoming_count": upcoming_count,
        "total_patients_count": total_patients_count,
        "pending_invitations_count": pending_invitations_count,
        "profile": profile,
        "profile_complete": profile_complete,
        "today": today,
    })


@login_required
def appointments_list(request):
    """Doctor's full appointment list — filterable by date and status."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")
    clinic_filter = request.GET.get("clinic_id", "")

    patient_filter = request.GET.get("patient_id", "")

    qs = (
        Appointment.objects.filter(doctor=user)
        .select_related("patient", "clinic", "appointment_type")
        .order_by("-appointment_date", "appointment_time")
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_filter:
        try:
            from datetime import datetime as _dt
            qs = qs.filter(appointment_date=_dt.strptime(date_filter, "%Y-%m-%d").date())
        except ValueError:
            pass
    if clinic_filter:
        qs = qs.filter(clinic_id=clinic_filter)
    if patient_filter:
        try:
            qs = qs.filter(patient_id=int(patient_filter))
        except ValueError:
            pass

    # Clinics this doctor works at (for filter dropdown)
    my_clinics = (
        ClinicStaff.objects.filter(user=user, is_active=True)
        .select_related("clinic")
        .values_list("clinic_id", "clinic__name")
    )

    return render(request, "doctors/appointments_list.html", {
        "appointments": qs,
        "status_choices": Appointment.Status.choices,
        "current_status": status_filter,
        "current_date": date_filter,
        "current_clinic": clinic_filter,
        "current_patient": patient_filter,
        "my_clinics": my_clinics,
    })


@login_required
def appointment_detail(request, appointment_id):
    """Single appointment view with patient info, intake answers, and status controls."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    appointment = get_object_or_404(
        Appointment,
        id=appointment_id,
        doctor=user,
    )

    from appointments.models import AppointmentAnswer
    intake_answers = (
        AppointmentAnswer.objects.filter(appointment=appointment)
        .select_related("question")
        .order_by("question__order", "id")
    )

    # Status transitions the doctor can trigger — as (value, label) tuples for template use
    _TRANSITION_MAP = {
        Appointment.Status.PENDING: [
            Appointment.Status.CONFIRMED,
            Appointment.Status.CANCELLED,
        ],
        Appointment.Status.CONFIRMED: [
            Appointment.Status.CHECKED_IN,
            Appointment.Status.CANCELLED,
            Appointment.Status.NO_SHOW,
        ],
        Appointment.Status.CHECKED_IN: [Appointment.Status.IN_PROGRESS],
        Appointment.Status.IN_PROGRESS: [Appointment.Status.COMPLETED],
    }
    raw_transitions = _TRANSITION_MAP.get(appointment.status, [])
    # Build (value, human_label) tuples for the template
    allowed_transitions = [(s.value, s.label) for s in raw_transitions]
    # Keep a set of valid values for POST validation
    valid_transition_values = {s.value for s in raw_transitions}

    if request.method == "POST":
        new_status = request.POST.get("status", "").strip()
        notes = request.POST.get("notes", "").strip()
        # Backend enforcement: only allow whitelisted transitions (ignore stale/tampered POSTs)
        if new_status in valid_transition_values:
            appointment.status = new_status
            if notes:
                appointment.notes = notes
            appointment.save(update_fields=["status", "notes", "updated_at"])

            # Notify patient when doctor cancels
            if new_status == Appointment.Status.CANCELLED:
                from django.db import transaction as _txn
                from clinics.models import ClinicStaff as _CS
                from appointments.services.appointment_notification_service import (
                    notify_appointment_cancelled_by_staff,
                )
                doctor_staff = _CS.objects.filter(
                    clinic=appointment.clinic, user=user, revoked_at__isnull=True
                ).first()
                _txn.on_commit(
                    lambda: notify_appointment_cancelled_by_staff(appointment, doctor_staff)
                )

            from django.contrib import messages as _msg
            _msg.success(request, "تم تحديث حالة الموعد.")
            return redirect("doctors:appointment_detail", appointment_id=appointment_id)
        elif new_status:
            from django.contrib import messages as _msg
            _msg.error(request, "هذا التحديث غير مسموح به.")

    return render(request, "doctors/appointment_detail.html", {
        "appointment": appointment,
        "intake_answers": intake_answers,
        "allowed_transitions": allowed_transitions,
    })


@login_required
def patients_list(request):
    """Doctor's unique patient list across all their clinics."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    clinic_filter = request.GET.get("clinic_id", "")

    qs = Appointment.objects.filter(doctor=user).exclude(
        status=Appointment.Status.CANCELLED
    )
    if clinic_filter:
        qs = qs.filter(clinic_id=clinic_filter)

    # Distinct patients with their latest appointment date
    from django.db.models import Max, Count
    patient_stats = (
        qs.values("patient_id", "patient__name", "patient__phone")
        .annotate(last_visit=Max("appointment_date"), total_visits=Count("id"))
        .order_by("-last_visit")
    )

    my_clinics = (
        ClinicStaff.objects.filter(user=user, is_active=True)
        .select_related("clinic")
        .values_list("clinic_id", "clinic__name")
    )

    return render(request, "doctors/patients_list.html", {
        "patient_stats": patient_stats,
        "my_clinics": my_clinics,
        "current_clinic": clinic_filter,
    })


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

    # Appointment types enabled for this doctor at this clinic (not all clinic types)
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )
    appointment_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=doctor.id, clinic_id=int(clinic_id)
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
            # Only allow slot generation for types the doctor actually offers
            try:
                apt_type_id_int = int(appointment_type_id)
            except (ValueError, TypeError):
                apt_type_id_int = None

            if apt_type_id_int:
                selected_type = next(
                    (t for t in appointment_types if t.id == apt_type_id_int), None
                )
            if selected_type:
                slots = generate_slots_for_date(
                    doctor_id=doctor.id,
                    clinic_id=int(clinic_id),
                    target_date=target_date,
                    duration_minutes=selected_type.duration_minutes,
                )

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

    Returns only the types that doctor has enabled for the given clinic
    (falling back to all active clinic types if the doctor has no
    DoctorClinicAppointmentType rows configured yet).

    Query params:
        clinic_id (required): Which clinic to view types for.
    """
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )

    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    clinic_id = request.GET.get("clinic_id")

    if not clinic_id:
        return render(
            request,
            "doctors/doctor_appointment_types.html",
            {"error": "clinic_id is required.", "doctor": doctor},
        )

    appointment_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=doctor_id,
        clinic_id=int(clinic_id),
    )

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

    is_secretary_invite = (invitation.role == "SECRETARY")

    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
            # Already logged in as the right user — send to the correct inbox
            if is_secretary_invite:
                return redirect(reverse("secretary:secretary_invitations_inbox"))
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

    # Determine which app slug to store in session for post-login redirect
    app_slug = "secretary" if is_secretary_invite else "doctors"

    if existing_user:
        # Store token for redirection after login
        request.session["pending_invitation_token"] = token
        request.session["pending_invitation_app"] = app_slug
        login_url = reverse("accounts:login")

        if is_secretary_invite:
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

    # No account yet: instruct them to register first.
    if is_secretary_invite:
        messages.info(
            request,
            f"مرحباً {invitation.doctor_name}، تلقّيت دعوة للانضمام كسكرتير/ة في {invitation.clinic.name}. يرجى إنشاء حساب جديد لقبول الدعوة."
        )
    else:
        messages.info(
            request,
            f"مرحباً د. {invitation.doctor_name}، تلقّيت دعوة للانضمام إلى {invitation.clinic.name}. يرجى إنشاء حساب جديد لقبول الدعوة."
        )

    # Store token for redirection after registration
    request.session["pending_invitation_token"] = token
    request.session["pending_invitation_app"] = app_slug
    return redirect(reverse("accounts:register"))


# ============================================
# DOCTOR VERIFICATION & CREDENTIAL UPLOAD
# ============================================

from doctors.models import DoctorVerification, ClinicDoctorCredential
from django import forms as django_forms
from core.validators.file_validators import validate_file_extension, validate_file_signature, validate_file_size


_FILE_INPUT_CLASSES = (
    "w-full text-sm text-gray-700 dark:text-gray-300 cursor-pointer "
    "border border-gray-300 dark:border-gray-600 rounded-xl "
    "file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 "
    "file:text-sm file:font-semibold file:bg-indigo-50 dark:file:bg-indigo-900/40 "
    "file:text-indigo-700 dark:file:text-indigo-300 hover:file:bg-indigo-100 dark:hover:file:bg-indigo-900/60"
)

_TEXT_INPUT_CLASSES = (
    "w-full text-sm rounded-xl border border-gray-300 dark:border-gray-600 "
    "bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-100 "
    "px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
)


class DoctorCredentialUploadForm(django_forms.Form):
    """Form for uploading doctor identity verification documents."""
    identity_document = django_forms.FileField(
        label="وثيقة الهوية (بطاقة هوية / جواز سفر)",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": _FILE_INPUT_CLASSES, "accept": ".jpg,.jpeg,.png,.pdf"}),
    )
    medical_license = django_forms.FileField(
        label="رخصة مزاولة المهنة الطبية",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": _FILE_INPUT_CLASSES, "accept": ".jpg,.jpeg,.png,.pdf"}),
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
            "class": _TEXT_INPUT_CLASSES + " resize-none",
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
            "class": _TEXT_INPUT_CLASSES,
            "placeholder": "مثال: 10",
        }),
    )


@login_required
def doctor_profile_view(request):
    """Read-only view of the doctor's profile."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))

    profile, _ = DoctorProfile.objects.get_or_create(user=user)
    specialties = profile.specialties.all()
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
    )

    return render(request, "doctors/doctor_profile.html", {
        "profile": profile,
        "specialties": specialties,
        "memberships": memberships,
    })


@login_required
def doctor_edit_profile_view(request):
    """Edit doctor's bio, experience, and email (email change via OTP)."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))

    profile, _ = DoctorProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        form = DoctorProfileForm(request.POST)
        email = request.POST.get("email", "").strip().lower()
        current_email = user.email or ""
        email_changed = email and email != current_email.lower()

        if email_changed:
            from django.core.validators import validate_email as _validate_email
            from django.core.exceptions import ValidationError as DjangoValidationError
            from django.contrib.auth import get_user_model
            _User = get_user_model()
            try:
                _validate_email(email)
                if _User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    messages.error(request, "البريد الإلكتروني هذا مسجل بالفعل.")
                    return render(request, "doctors/doctor_edit_profile.html", {
                        "form": form,
                        "profile": profile,
                    })
                if form.is_valid():
                    profile.bio = form.cleaned_data.get("bio", "")
                    profile.years_of_experience = form.cleaned_data.get("years_of_experience")
                    profile.save()
                request.session["pending_email_change"] = email
                return redirect(reverse("accounts:change_email_request"))
            except DjangoValidationError:
                messages.error(request, "البريد الإلكتروني غير صحيح.")
                return render(request, "doctors/doctor_edit_profile.html", {
                    "form": form,
                    "profile": profile,
                })

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

    return render(request, "doctors/doctor_edit_profile.html", {
        "form": form,
        "profile": profile,
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
            "class": _FILE_INPUT_CLASSES,
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
            spec_name = credential.specialty.name_ar if credential.specialty else "العام"
            messages.success(
                request,
                f"تم رفع شهادة التخصص ({spec_name}) بنجاح. سيتم مراجعتها."
            )
            return redirect(reverse("doctors:verification_status"))
    else:
        form = ClinicCredentialUploadForm()

    return render(request, "doctors/upload_clinic_credential.html", {
        "form": form,
        "credential": credential,
    })


# ============================================
# DOCTOR APPOINTMENT TYPES (self-service)
# ============================================


@login_required
def my_appointment_types(request):
    """Doctor manages their own enabled appointment types per clinic."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    from appointments.services.appointment_type_service import (
        get_doctor_type_assignments,
        set_doctor_clinic_appointment_types,
    )
    from django.core.exceptions import ValidationError as DjangoValidationError

    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        _messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_reverse("accounts:home"))

    # All active clinics the doctor belongs to
    memberships = (
        ClinicStaff.objects.filter(user=user, is_active=True, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("clinic__name")
    )

    # Handle POST: update types for a specific clinic
    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        if clinic_id:
            try:
                clinic_id = int(clinic_id)
                # Verify doctor is in that clinic
                membership = next((m for m in memberships if m.clinic_id == clinic_id), None)
                if not membership:
                    _messages.error(request, "لا تملك صلاحية تعديل هذه العيادة.")
                else:
                    active_ids = request.POST.getlist("type_ids")
                    set_doctor_clinic_appointment_types(user.id, clinic_id, active_ids)
                    _messages.success(request, f"تم حفظ أنواع مواعيدك في {membership.clinic.name}.")
            except (ValueError, DjangoValidationError) as e:
                err = e.messages[0] if hasattr(e, "messages") else str(e)
                _messages.error(request, err)
        return redirect(_reverse("doctors:my_appointment_types"))

    # Build per-clinic assignment data for the template
    clinic_data = []
    for m in memberships:
        assignments = get_doctor_type_assignments(user.id, m.clinic_id)
        if assignments:  # only show clinics that have at least one appointment type defined
            clinic_data.append({
                "clinic": m.clinic,
                "assignments": assignments,
            })

    # Annotate each assignment with intake form question count
    for item in clinic_data:
        for a in item["assignments"]:
            template = DoctorIntakeFormTemplate.objects.filter(
                doctor=user,
                appointment_type=a["appointment_type"],
                is_active=True,
            ).first()
            a["question_count"] = template.questions.count() if template else 0

    return render(request, "doctors/my_appointment_types.html", {
        "clinic_data": clinic_data,
    })


# ============================================
# INTAKE FORM BUILDER
# ============================================

def _doctor_required(request):
    """Returns None if user is a doctor, otherwise returns a redirect response."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))
    return None


@login_required
def intake_form_builder(request, appointment_type_id):
    """
    Doctor builds / edits the intake form for a specific appointment type.
    GET  → show current template + questions.
    POST → save template title/description (creates template if needed).
    """
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    user = request.user

    # Fetch appointment type and verify doctor belongs to that clinic
    appointment_type = get_object_or_404(AppointmentType, pk=appointment_type_id, is_active=True)
    membership = ClinicStaff.objects.filter(
        user=user,
        clinic=appointment_type.clinic,
        is_active=True,
        revoked_at__isnull=True,
    ).first()
    if not membership:
        _messages.error(request, "لا تملك صلاحية إدارة نماذج هذه العيادة.")
        return redirect(_reverse("doctors:my_appointment_types"))

    template, _ = DoctorIntakeFormTemplate.objects.get_or_create(
        doctor=user,
        appointment_type=appointment_type,
        defaults={
            "title": f"نموذج {appointment_type.display_name}",
            "is_active": True,
        },
    )

    if request.method == "POST" and "save_template" in request.POST:
        title_ar = request.POST.get("title_ar", "").strip()
        description = request.POST.get("description", "").strip()
        if not title_ar:
            _messages.error(request, "عنوان النموذج مطلوب.")
        else:
            template.title_ar = title_ar
            template.title = title_ar  # keep in sync for display_title
            template.description = description
            template.show_reason_field = "show_reason_field" in request.POST
            template.reason_field_label = request.POST.get("reason_field_label", "").strip()
            template.reason_field_placeholder = request.POST.get("reason_field_placeholder", "").strip()
            template.reason_field_required = "reason_field_required" in request.POST
            template.save(update_fields=[
                "title", "title_ar", "description",
                "show_reason_field", "reason_field_label",
                "reason_field_placeholder", "reason_field_required",
                "updated_at",
            ])
            _messages.success(request, "تم حفظ معلومات النموذج.")
        return redirect(_reverse("doctors:intake_form_builder", args=[appointment_type_id]))

    questions = list(template.questions.order_by("order"))

    # Build follow-up structure: which questions are targets of SHOW rules
    rules_qs = DoctorIntakeRule.objects.filter(
        source_question__template=template,
        action=DoctorIntakeRule.Action.SHOW,
    ).select_related("target_question")

    # Map source_question_id → list of rules (each with target question)
    from collections import defaultdict as _dd
    followups_by_source = _dd(list)
    target_ids = set()
    for rule in rules_qs:
        followups_by_source[rule.source_question_id].append(rule)
        target_ids.add(rule.target_question_id)

    # Triggerable field types (can have follow-up questions)
    triggerable_types = {
        DoctorIntakeQuestion.FieldType.CHECKBOX,
        DoctorIntakeQuestion.FieldType.SELECT,
        DoctorIntakeQuestion.FieldType.MULTISELECT,
    }

    # Only top-level questions (not follow-up targets) shown in main list
    top_questions = [q for q in questions if q.id not in target_ids]

    next_order = (max(q.order for q in questions) + 1) if questions else 0

    return render(request, "doctors/intake_form_builder.html", {
        "appointment_type": appointment_type,
        "template": template,
        "questions": top_questions,
        "followups_by_source": dict(followups_by_source),
        "triggerable_types": triggerable_types,
        "next_order": next_order,
        "field_types": DoctorIntakeQuestion.FieldType.choices,
    })


@login_required
def intake_question_add(request, template_id):
    """Add a question to an intake form template."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    import json as _json

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    apt_type_id = template.appointment_type_id

    if request.method != "POST":
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    question_text_ar = request.POST.get("question_text_ar", "").strip()
    field_type = request.POST.get("field_type", DoctorIntakeQuestion.FieldType.TEXT)
    is_required = request.POST.get("is_required") == "on"
    placeholder = request.POST.get("placeholder", "").strip()
    help_text_content = request.POST.get("help_text_content", "").strip()

    # Parse order (auto-increment to end)
    existing_max = template.questions.order_by("-order").values_list("order", flat=True).first()
    order = (existing_max + 1) if existing_max is not None else 0

    # Choices for SELECT/MULTISELECT
    choices = []
    if field_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
        raw = request.POST.get("choices_raw", "")
        choices = [c.strip() for c in raw.splitlines() if c.strip()]
        if len(choices) < 2:
            _messages.error(request, "يجب إضافة خيارَيْن على الأقل للحقول القائمة.")
            return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    try:
        DoctorIntakeQuestion.objects.create(
            template=template,
            question_text=question_text_ar,
            question_text_ar=question_text_ar,
            field_type=field_type,
            is_required=is_required,
            order=order,
            placeholder=placeholder,
            help_text_content=help_text_content,
            choices=choices,
        )
        _messages.success(request, "تمت إضافة السؤال.")
    except Exception as e:
        _messages.error(request, f"خطأ أثناء إضافة السؤال: {e}")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_question_edit(request, template_id, question_id):
    """Edit an existing intake question."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        question_text_ar = request.POST.get("question_text_ar", "").strip()
        field_type = request.POST.get("field_type", question.field_type)
        is_required = request.POST.get("is_required") == "on"
        placeholder = request.POST.get("placeholder", "").strip()
        help_text_content = request.POST.get("help_text_content", "").strip()

        choices = question.choices
        if field_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
            raw = request.POST.get("choices_raw", "")
            choices = [c.strip() for c in raw.splitlines() if c.strip()]
            if len(choices) < 2:
                _messages.error(request, "يجب إضافة خيارَيْن على الأقل للحقول القائمة.")
                return redirect(_reverse("doctors:intake_question_edit", args=[template_id, question_id]))

        question.question_text = question_text_ar
        question.question_text_ar = question_text_ar
        question.field_type = field_type
        question.is_required = is_required
        question.placeholder = placeholder
        question.help_text_content = help_text_content
        question.choices = choices
        question.save()
        _messages.success(request, "تم تحديث السؤال.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    return render(request, "doctors/intake_question_form.html", {
        "template": template,
        "question": question,
        "appointment_type": template.appointment_type,
        "field_types": DoctorIntakeQuestion.FieldType.choices,
        "choices_raw": "\n".join(question.choices) if question.choices else "",
        "is_edit": True,
    })


@login_required
def intake_question_delete(request, template_id, question_id):
    """Delete a question from an intake form template."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        question.delete()
        _messages.success(request, "تم حذف السؤال.")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_followup_add(request, template_id, question_id):
    """Add a follow-up (conditional) question triggered when source has a specific answer."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    from django.db import transaction

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    source_question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method != "POST":
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    triggerable_types = (
        DoctorIntakeQuestion.FieldType.CHECKBOX,
        DoctorIntakeQuestion.FieldType.SELECT,
        DoctorIntakeQuestion.FieldType.MULTISELECT,
    )
    if source_question.field_type not in triggerable_types:
        _messages.error(request, "لا يمكن إضافة سؤال فرعي لهذا النوع من الأسئلة.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    trigger_value = request.POST.get("trigger_value", "").strip()
    followup_text = request.POST.get("followup_text_ar", "").strip()
    followup_type = request.POST.get("followup_field_type", DoctorIntakeQuestion.FieldType.TEXT)
    followup_required = request.POST.get("followup_is_required") == "on"
    followup_placeholder = request.POST.get("followup_placeholder", "").strip()

    if not trigger_value:
        _messages.error(request, "يجب تحديد قيمة المشغّل.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    if not followup_text:
        _messages.error(request, "نص السؤال الفرعي مطلوب.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    followup_choices = []
    if followup_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
        raw = request.POST.get("followup_choices_raw", "")
        followup_choices = [c.strip() for c in raw.splitlines() if c.strip()]
        if len(followup_choices) < 2:
            _messages.error(request, "يجب إضافة خيارَين على الأقل للحقول القائمة.")
            return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    try:
        with transaction.atomic():
            existing_max = template.questions.order_by("-order").values_list("order", flat=True).first()
            order = (existing_max + 1) if existing_max is not None else 0

            followup_question = DoctorIntakeQuestion.objects.create(
                template=template,
                question_text=followup_text,
                question_text_ar=followup_text,
                field_type=followup_type,
                is_required=followup_required,
                order=order,
                placeholder=followup_placeholder,
                choices=followup_choices,
            )

            DoctorIntakeRule.objects.create(
                source_question=source_question,
                expected_value=trigger_value,
                operator=DoctorIntakeRule.Operator.EQUALS,
                target_question=followup_question,
                action=DoctorIntakeRule.Action.SHOW,
            )

        _messages.success(request, "تمت إضافة السؤال الفرعي.")
    except Exception as e:
        _messages.error(request, f"خطأ أثناء الإضافة: {e}")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_rule_delete(request, template_id, rule_id):
    """Delete a follow-up rule and its orphaned target question."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    rule = get_object_or_404(
        DoctorIntakeRule,
        pk=rule_id,
        source_question__template=template,
    )
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        target_question = rule.target_question
        rule.delete()
        if not target_question.rules_as_target.exists():
            target_question.delete()
        _messages.success(request, "تم حذف السؤال الفرعي.")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))
