from datetime import date as _date

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from accounts.otp_utils import request_otp, verify_otp, is_in_cooldown, get_remaining_resends
from accounts.email_utils import send_email_otp, verify_email_otp, is_email_otp_in_cooldown
from .models import Clinic, ClinicSubscription, ClinicVerification, ClinicStaff


_ARABIC_MONTHS = [
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]


def _get_appointments_context(clinic, month, year):
    from appointments.models import Appointment

    month = max(1, min(12, int(month)))
    year = int(year)

    appointments = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date__year=year,
            appointment_date__month=month,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_date", "appointment_time")
    )

    total = appointments.count()
    stats = {
        "total": total,
        "completed": appointments.filter(status="COMPLETED").count(),
        "cancelled": appointments.filter(status="CANCELLED").count(),
        "no_shows": appointments.filter(status="NO_SHOW").count(),
        "pending": appointments.filter(status__in=["PENDING", "CONFIRMED"]).count(),
    }

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    return {
        "appointments": appointments,
        "appt_month": month,
        "appt_year": year,
        "appt_month_name": _ARABIC_MONTHS[month],
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "appt_stats": stats,
    }


# ============================================
# HELPERS
# ============================================

def get_owner_clinic_or_404(request, clinic_id):
    """Return a clinic owned by the current user or raise 404."""
    return get_object_or_404(Clinic, id=clinic_id, main_doctor=request.user, is_active=True)


# ============================================
# CLINIC LIST + DASHBOARD
# ============================================

@login_required
def my_clinics(request):
    """List all clinics owned by the current user."""
    clinics = Clinic.objects.filter(main_doctor=request.user, is_active=True)
    return render(request, "clinics/my_clinics.html", {"clinics": clinics})


@login_required
def my_clinic(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    request.session["selected_clinic_id"] = clinic.id
    subscription = getattr(clinic, "subscription", None)

    # Staff — all active members, doctors first
    staff_qs = (
        ClinicStaff.objects.filter(clinic=clinic, is_active=True)
        .select_related("user", "user__doctor_profile")
        .prefetch_related("user__doctor_profile__doctor_specialties__specialty")
        .order_by("added_at")
    )
    clinic_owner = next((s for s in staff_qs if s.role == "MAIN_DOCTOR"), None)
    doctors = [s for s in staff_qs if s.role == "DOCTOR"]
    secretaries = [s for s in staff_qs if s.role == "SECRETARY"]

    # Appointments — default to current month
    today = _date.today()
    month = request.GET.get("month", today.month)
    year = request.GET.get("year", today.year)
    appt_ctx = _get_appointments_context(clinic, month, year)

    return render(request, "clinics/my_clinic.html", {
        "clinic": clinic,
        "subscription": subscription,
        "clinic_owner": clinic_owner,
        "doctors": doctors,
        "secretaries": secretaries,
        **appt_ctx,
    })


@login_required
def doctor_schedule_panel(request, clinic_id, staff_id):
    """HTMX endpoint: return doctor working hours drawer content."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    staff = get_object_or_404(ClinicStaff, id=staff_id, clinic=clinic, is_active=True)

    from doctors.models import DoctorAvailability
    availability = list(
        DoctorAvailability.objects
        .filter(doctor=staff.user, clinic=clinic, is_active=True)
        .order_by("day_of_week", "start_time")
    )

    days_map = dict(DoctorAvailability.DAY_CHOICES)
    grouped = {}
    for slot in availability:
        day = slot.day_of_week
        if day not in grouped:
            grouped[day] = {"name": days_map[day], "slots": []}
        grouped[day]["slots"].append(slot)

    return render(request, "clinics/partials/doctor_schedule_drawer.html", {
        "staff": staff,
        "grouped_availability": grouped,
        "has_availability": bool(availability),
    })


@login_required
def appointments_panel_view(request, clinic_id):
    """HTMX endpoint: return the appointments panel partial for a given month/year."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    today = _date.today()
    month = request.GET.get("month", today.month)
    year = request.GET.get("year", today.year)
    appt_ctx = _get_appointments_context(clinic, month, year)
    return render(request, "clinics/partials/appointments_panel.html", {
        "clinic": clinic,
        **appt_ctx,
    })


@login_required
def owner_profile(request):
    """Clinic owner profile — view and edit personal info."""
    user = request.user
    from accounts.models import City
    cities = City.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        city_id = request.POST.get("city", "")

        errors = {}
        if not name:
            errors["name"] = "الاسم مطلوب."

        if not errors:
            user.name = name
            if email:
                from django.core.validators import validate_email
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    validate_email(email)
                    if email != user.email:
                        user.email = email
                        user.email_verified = False
                except DjangoValidationError:
                    errors["email"] = "البريد الإلكتروني غير صحيح."
            else:
                user.email = None
                user.email_verified = False

            if not errors:
                if city_id:
                    from accounts.models import City as CityModel
                    try:
                        user.city = CityModel.objects.get(id=city_id)
                    except CityModel.DoesNotExist:
                        user.city = None
                else:
                    user.city = None
                user.save(update_fields=["name", "email", "email_verified", "city"])
                messages.success(request, "تم حفظ التغييرات بنجاح.")
                return redirect("clinics:owner_profile")

        return render(request, "clinics/owner_profile.html", {
            "cities": cities,
            "errors": errors,
            "form_data": request.POST,
        })

    owned_clinics = Clinic.objects.filter(main_doctor=user, is_active=True).order_by("name")
    return render(request, "clinics/owner_profile.html", {
        "cities": cities,
        "owned_clinics": owned_clinics,
    })


@login_required
def switch_clinic(request, clinic_id):
    """Set the active clinic in session and redirect to its dashboard."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    request.session["selected_clinic_id"] = clinic.id
    return redirect(reverse("clinics:my_clinic", kwargs={"clinic_id": clinic.id}))


@login_required
def manage_staff(request, clinic_id):
    get_owner_clinic_or_404(request, clinic_id)
    return HttpResponse("Manage Clinic Staff - Coming Soon!")


@login_required
def add_staff(request, clinic_id):
    get_owner_clinic_or_404(request, clinic_id)
    return HttpResponse("Add Staff Member - Coming Soon!")


@login_required
def remove_staff(request, clinic_id, staff_id):
    get_owner_clinic_or_404(request, clinic_id)
    return HttpResponse(f"Remove Staff {staff_id} - Coming Soon!")


# ============================================
# CLINIC INVITATIONS FLOW
# ============================================

from .models import ClinicInvitation
from .forms import ClinicInvitationForm, SecretaryInvitationForm
from .services import create_invitation, cancel_invitation

@login_required
def invitations_list(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    invitations = ClinicInvitation.objects.filter(clinic=clinic).select_related('invited_by').prefetch_related('specialties').order_by('-created_at')
    
    return render(request, "clinics/invitations_list.html", {
        "clinic": clinic,
        "invitations": invitations,
    })


@login_required
def create_invitation_view(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    
    if request.method == "POST":
        form = ClinicInvitationForm(request.POST)
        if form.is_valid():
            try:
                create_invitation(clinic, request.user, form.cleaned_data, request=request)
                messages.success(request, "تم إرسال الدعوة بنجاح.")
                return redirect(reverse("clinics:invitations_list", kwargs={"clinic_id": clinic_id}))
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'messages'):
                    err_msg = " ".join(e.messages)
                messages.error(request, f"خطأ: {err_msg}")
    else:
        form = ClinicInvitationForm()

    return render(request, "clinics/create_invitation.html", {
        "clinic": clinic,
        "form": form,
    })


@login_required
def create_secretary_invitation_view(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    
    if request.method == "POST":
        form = SecretaryInvitationForm(request.POST)
        if form.is_valid():
            try:
                create_invitation(clinic, request.user, form.cleaned_data, role="SECRETARY", request=request)
                messages.success(request, "تم إرسال دعوة السكرتير/ة بنجاح.")
                return redirect(reverse("clinics:invitations_list", kwargs={"clinic_id": clinic_id}))
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'messages'):
                    err_msg = " ".join(e.messages)
                messages.error(request, f"خطأ: {err_msg}")
    else:
        form = SecretaryInvitationForm()

    return render(request, "clinics/create_secretary_invitation.html", {
        "clinic": clinic,
        "form": form,
    })


@login_required
def cancel_invitation_view(request, clinic_id, invitation_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, clinic=clinic)
    
    if request.method == "POST":
        try:
            cancel_invitation(invitation, request.user)
            messages.success(request, "تم إلغاء الدعوة بنجاح.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    # Redirecting back to invitations list since this might be an HTMX call or normal POST
    return redirect(reverse("clinics:invitations_list", kwargs={"clinic_id": clinic_id}))


# ============================================
# CLINIC CHANNEL VERIFICATION FLOW
# ============================================


def _activate_clinic_if_ready(clinic, verification):
    """Set clinic status to ACTIVE when all required channels are verified."""
    if verification.is_fully_verified and clinic.status == "PENDING":
        clinic.status = "ACTIVE"
        clinic.save()


@login_required
def verify_owner_phone(request, clinic_id):
    """Step 1: Verify clinic owner's personal phone via SMS OTP."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Skip if already verified
    if verification.owner_phone_verified_at:
        next_step = verification.next_pending_step(clinic_id)
        return redirect(next_step or reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))

    phone = request.user.phone
    welcome_name = request.session.pop("clinic_welcome_name", None)

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = request_otp(phone)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect(reverse("clinics:verify_owner_phone", kwargs={"clinic_id": clinic_id}))

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_otp(phone, entered_otp)
        if success:
            verification.owner_phone_verified_at = timezone.now()
            verification.save()
            # Pre-send OTP for step 2 (owner email)
            send_email_otp(request.user.email, request.user.name)
            return redirect(reverse("clinics:verify_owner_email", kwargs={"clinic_id": clinic_id}))
        messages.error(request, msg)

    return render(request, "clinics/verify_owner_phone.html", {
        "phone": phone,
        "cooldown": is_in_cooldown(phone),
        "remaining_resends": get_remaining_resends(phone),
        "step": 1,
        "total_steps": 4,
        "welcome_name": welcome_name,
        "clinic": clinic,
    })


@login_required
def verify_owner_email(request, clinic_id):
    """Step 2: Verify clinic owner's email via email OTP."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guard
    if not verification.owner_phone_verified_at:
        return redirect(reverse("clinics:verify_owner_phone", kwargs={"clinic_id": clinic_id}))

    # Skip if already verified
    if verification.owner_email_verified_at:
        next_step = verification.next_pending_step(clinic_id)
        return redirect(next_step or reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))

    email = request.user.email

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = send_email_otp(email, request.user.name)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect(reverse("clinics:verify_owner_email", kwargs={"clinic_id": clinic_id}))

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_email_otp(email, entered_otp)
        if success:
            verification.owner_email_verified_at = timezone.now()
            verification.save()
            # Pre-send OTP for step 3 (clinic phone)
            request_otp(clinic.phone)
            return redirect(reverse("clinics:verify_clinic_phone", kwargs={"clinic_id": clinic_id}))
        messages.error(request, msg)

    return render(request, "clinics/verify_owner_email.html", {
        "email": email,
        "cooldown": is_email_otp_in_cooldown(email),
        "step": 2,
        "total_steps": 4,
        "clinic": clinic,
    })


@login_required
def verify_clinic_phone(request, clinic_id):
    """Step 3: Verify clinic's phone number via SMS OTP."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guards
    if not verification.owner_phone_verified_at:
        return redirect(reverse("clinics:verify_owner_phone", kwargs={"clinic_id": clinic_id}))
    if not verification.owner_email_verified_at:
        return redirect(reverse("clinics:verify_owner_email", kwargs={"clinic_id": clinic_id}))

    # Skip if already verified
    if verification.clinic_phone_verified_at:
        next_step = verification.next_pending_step(clinic_id)
        return redirect(next_step or reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))

    phone = clinic.phone

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = request_otp(phone)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect(reverse("clinics:verify_clinic_phone", kwargs={"clinic_id": clinic_id}))

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_otp(phone, entered_otp)
        if success:
            verification.clinic_phone_verified_at = timezone.now()
            verification.save()
            if clinic.email:
                # Pre-send OTP for step 4 (clinic email)
                send_email_otp(clinic.email, clinic.name)
                return redirect(reverse("clinics:verify_clinic_email", kwargs={"clinic_id": clinic_id}))
            # No clinic email — activate now if all required steps done
            _activate_clinic_if_ready(clinic, verification)
            messages.success(request, "تم التحقق من جميع القنوات! عيادتك أصبحت نشطة.")
            return redirect(reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))
        messages.error(request, msg)

    return render(request, "clinics/verify_clinic_phone.html", {
        "phone": phone,
        "cooldown": is_in_cooldown(phone),
        "remaining_resends": get_remaining_resends(phone),
        "step": 3,
        "total_steps": 4,
        "has_clinic_email": bool(clinic.email),
        "clinic": clinic,
    })


@login_required
def verify_clinic_email(request, clinic_id):
    """Step 4 (optional): Verify clinic's email address via email OTP."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guards
    if not verification.owner_phone_verified_at:
        return redirect(reverse("clinics:verify_owner_phone", kwargs={"clinic_id": clinic_id}))
    if not verification.owner_email_verified_at:
        return redirect(reverse("clinics:verify_owner_email", kwargs={"clinic_id": clinic_id}))
    if not verification.clinic_phone_verified_at:
        return redirect(reverse("clinics:verify_clinic_phone", kwargs={"clinic_id": clinic_id}))

    # Step only applicable when clinic has an email
    if not clinic.email:
        _activate_clinic_if_ready(clinic, verification)
        return redirect(reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))

    # Skip if already verified
    if verification.clinic_email_verified_at:
        _activate_clinic_if_ready(clinic, verification)
        return redirect(reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))

    email = clinic.email

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = send_email_otp(email, clinic.name)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect(reverse("clinics:verify_clinic_email", kwargs={"clinic_id": clinic_id}))

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_email_otp(email, entered_otp)
        if success:
            verification.clinic_email_verified_at = timezone.now()
            verification.save()
            _activate_clinic_if_ready(clinic, verification)
            messages.success(request, "تم التحقق من جميع القنوات! عيادتك أصبحت نشطة.")
            return redirect(reverse("clinics:my_clinic", kwargs={"clinic_id": clinic_id}))
        messages.error(request, msg)

    return render(request, "clinics/verify_clinic_email.html", {
        "email": email,
        "cooldown": is_email_otp_in_cooldown(email),
        "step": 4,
        "total_steps": 4,
        "clinic": clinic,
    })

# ============================================
# CLINIC WORKING HOURS
# ============================================
from django.utils.dateparse import parse_time
from .models import ClinicWorkingHours
from .services import (
    create_working_hours,
    update_working_hours,
    delete_working_hours,
    get_clinic_working_hours
)

@login_required
def clinic_working_hours_list_view(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    working_hours = get_clinic_working_hours(clinic)

    # Group by weekday for easier display
    days = ClinicWorkingHours.DAY_CHOICES
    schedule = []
    for day_val, day_name in days:
        day_hours = [wh for wh in working_hours if wh.weekday == day_val]
        schedule.append({
            'day_val': day_val,
            'day_name': day_name,
            'hours': day_hours,
            'is_closed': any(wh.is_closed for wh in day_hours) if day_hours else False
        })

    return render(request, "clinics/working_hours.html", {
        "clinic": clinic,
        "schedule": schedule,
        "days": days,
    })

@login_required
def clinic_working_hours_create_view(request, clinic_id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    if request.method == "POST":
        weekday = request.POST.get("weekday")
        is_closed = request.POST.get("is_closed") == "on"
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")

        try:
            weekday = int(weekday)
            if is_closed:
                start_time = None
                end_time = None
            else:
                if not start_time_str or not end_time_str:
                    from django.core.exceptions import ValidationError
                    raise ValidationError("Start time and end time are required.")

                start_time = parse_time(start_time_str)
                end_time = parse_time(end_time_str)

            create_working_hours(clinic, weekday, start_time, end_time, is_closed)
            messages.success(request, "تمت إضافة ساعات العمل بنجاح.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'message_dict'):
                err_msg = " ".join([f"{k}: {', '.join(v)}" for k, v in e.message_dict.items()])
            elif hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    return redirect(reverse("clinics:working_hours_list", kwargs={"clinic_id": clinic_id}))

@login_required
def clinic_working_hours_update_view(request, clinic_id, id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    instance = get_object_or_404(ClinicWorkingHours, id=id, clinic=clinic)

    if request.method == "POST":
        is_closed = request.POST.get("is_closed") == "on"
        start_time_str = request.POST.get("start_time")
        end_time_str = request.POST.get("end_time")

        try:
            if is_closed:
                start_time = None
                end_time = None
            else:
                if not start_time_str or not end_time_str:
                    from django.core.exceptions import ValidationError
                    raise ValidationError("Start time and end time are required.")
                start_time = parse_time(start_time_str)
                end_time = parse_time(end_time_str)

            update_working_hours(instance, start_time, end_time, is_closed)
            messages.success(request, "تم تحديث ساعات العمل بنجاح.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'message_dict'):
                err_msg = " ".join([f"{k}: {', '.join(v)}" for k, v in e.message_dict.items()])
            elif hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    return redirect(reverse("clinics:working_hours_list", kwargs={"clinic_id": clinic_id}))

@login_required
def clinic_working_hours_delete_view(request, clinic_id, id):
    clinic = get_owner_clinic_or_404(request, clinic_id)
    instance = get_object_or_404(ClinicWorkingHours, id=id, clinic=clinic)

    if request.method == "POST":
        delete_working_hours(instance)
        messages.success(request, "تم حذف ساعات العمل بنجاح.")

    return redirect(reverse("clinics:working_hours_list", kwargs={"clinic_id": clinic_id}))


# ============================================
# CLINIC COMPLIANCE SETTINGS
# ============================================
from .services import get_clinic_compliance_settings, update_clinic_compliance_settings


@login_required
def compliance_settings_view(request, clinic_id):
    """Display current compliance settings for the clinic owner."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    settings = get_clinic_compliance_settings(clinic)
    return render(request, "clinics/compliance_settings.html", {
        "clinic": clinic,
        "settings": settings,
    })


@login_required
def compliance_settings_update_view(request, clinic_id):
    """Update compliance settings (POST only)."""
    clinic = get_owner_clinic_or_404(request, clinic_id)

    if request.method == "POST":
        try:
            max_no_show_count = int(request.POST.get("max_no_show_count", 3))
            forgiveness_enabled = request.POST.get("forgiveness_enabled") == "on"
            forgiveness_days_raw = request.POST.get("forgiveness_days")
            forgiveness_days = int(forgiveness_days_raw) if forgiveness_days_raw and forgiveness_enabled else None

            update_clinic_compliance_settings(
                clinic=clinic,
                max_no_show_count=max_no_show_count,
                forgiveness_enabled=forgiveness_enabled,
                forgiveness_days=forgiveness_days,
            )
            messages.success(request, "تم تحديث إعدادات الامتثال بنجاح.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'message_dict'):
                err_msg = " ".join([f"{k}: {', '.join(v)}" for k, v in e.message_dict.items()])
            elif hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    return redirect(reverse("clinics:compliance_settings", kwargs={"clinic_id": clinic_id}))


# ============================================
# ADD CLINIC FLOW (for already-authenticated clinic owners)
# ============================================
from .forms import AddClinicCodeForm, AddClinicDetailsForm
from .models import ClinicActivationCode


@login_required
def add_clinic_code_view(request):
    """Step 1: Logged-in clinic owner enters an activation code for a new clinic."""
    if request.method == "POST":
        form = AddClinicCodeForm(
            request.POST,
            user_phone=request.user.phone,
            user_national_id=request.user.national_id,
        )
        if form.is_valid():
            request.session["add_clinic"] = {
                "activation_code": form.cleaned_data["activation_code"],
            }
            return redirect(reverse("clinics:add_clinic_details"))
    else:
        form = AddClinicCodeForm(
            user_phone=request.user.phone,
            user_national_id=request.user.national_id,
        )
    return render(request, "clinics/add_clinic_code.html", {"form": form})


@login_required
def add_clinic_details_view(request):
    """Step 2: Logged-in clinic owner fills in clinic info and creates the clinic."""
    reg = request.session.get("add_clinic")
    if not reg or "activation_code" not in reg:
        return redirect(reverse("clinics:add_clinic_code"))

    if request.method == "POST":
        if request.POST.get("action") == "back":
            return redirect(reverse("clinics:add_clinic_code"))

        form = AddClinicDetailsForm(request.POST)
        if form.is_valid():
            from .services import create_clinic_for_main_doctor as _create_clinic
            try:
                activation_code_obj = ClinicActivationCode.objects.get(
                    code=reg["activation_code"], is_used=False
                )
                clinic = _create_clinic(
                    user=request.user,
                    cleaned_data=form.cleaned_data,
                    activation_code_obj=activation_code_obj,
                    owner_verified_at=timezone.now(),
                )
                del request.session["add_clinic"]
                messages.success(request, f"تم إنشاء عيادة \"{clinic.name}\" بنجاح!")
                return redirect(reverse("clinics:my_clinics"))
            except ClinicActivationCode.DoesNotExist:
                messages.error(request, "رمز التفعيل لم يعد صالحاً.")
                return redirect(reverse("clinics:add_clinic_code"))
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'messages'):
                    err_msg = " ".join(e.messages)
                messages.error(request, f"خطأ: {err_msg}")
    else:
        form = AddClinicDetailsForm()

    return render(request, "clinics/add_clinic_details.html", {"form": form})


# ============================================
# CLINIC DOCTOR CREDENTIAL REVIEW
# ============================================

from doctors.models import ClinicDoctorCredential


@login_required
def clinic_credentials_list(request, clinic_id):
    """
    Clinic owner page: list all doctor credentials for this clinic
    with ability to approve or reject each one.
    """
    clinic = get_owner_clinic_or_404(request, clinic_id)

    credentials = (
        ClinicDoctorCredential.objects.filter(clinic=clinic)
        .select_related("doctor", "specialty", "reviewed_by")
        .order_by("-updated_at")
    )

    return render(request, "clinics/credentials_review.html", {
        "clinic": clinic,
        "credentials": credentials,
    })


@login_required
def clinic_credential_approve(request, clinic_id, credential_id):
    """Clinic owner approves a doctor credential."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    credential = get_object_or_404(
        ClinicDoctorCredential, id=credential_id, clinic=clinic
    )

    if request.method == "POST":
        from django.utils import timezone as _tz
        credential.credential_status = "CREDENTIALS_VERIFIED"
        credential.reviewed_by = request.user
        credential.reviewed_at = _tz.now()
        credential.rejection_reason = ""
        credential.save()

        # Notify doctor via email
        try:
            from accounts.email_utils import send_verification_approved_email
            if credential.doctor.email:
                spec_name = credential.specialty.name_ar if credential.specialty else "عام"
                send_verification_approved_email(
                    credential.doctor.email,
                    credential.doctor.name,
                    f"اعتماد مؤهلات ({spec_name}) في {clinic.name}",
                )
        except Exception:
            pass  # Email failure shouldn't block approval

        messages.success(
            request,
            f"تم اعتماد مؤهلات د. {credential.doctor.name} بنجاح."
        )

    return redirect(reverse("clinics:credentials_list", kwargs={"clinic_id": clinic_id}))


@login_required
def clinic_credential_reject(request, clinic_id, credential_id):
    """Clinic owner rejects a doctor credential with a reason."""
    clinic = get_owner_clinic_or_404(request, clinic_id)
    credential = get_object_or_404(
        ClinicDoctorCredential, id=credential_id, clinic=clinic
    )

    if request.method == "POST":
        from django.utils import timezone as _tz
        reason = request.POST.get("rejection_reason", "").strip()
        if not reason:
            messages.error(request, "يجب إدخال سبب الرفض.")
            return redirect(reverse("clinics:credentials_list", kwargs={"clinic_id": clinic_id}))

        credential.credential_status = "CREDENTIALS_REJECTED"
        credential.reviewed_by = request.user
        credential.reviewed_at = _tz.now()
        credential.rejection_reason = reason
        credential.save()

        # Notify doctor via email
        try:
            from accounts.email_utils import send_verification_rejected_email
            if credential.doctor.email:
                spec_name = credential.specialty.name_ar if credential.specialty else "عام"
                send_verification_rejected_email(
                    credential.doctor.email,
                    credential.doctor.name,
                    f"مؤهلات ({spec_name}) في {clinic.name}",
                    reason,
                )
        except Exception:
            pass  # Email failure shouldn't block rejection

        messages.success(
            request,
            f"تم رفض مؤهلات د. {credential.doctor.name}."
        )

    return redirect(reverse("clinics:credentials_list", kwargs={"clinic_id": clinic_id}))


# ============================================
# REPORTS & ANALYTICS
# ============================================

@login_required
def reports_view(request):
    """Aggregate analytics dashboard for all clinics owned by this user."""
    import json
    from datetime import date as _date_type
    from appointments.models import Appointment
    from patients.models import PatientProfile
    from django.db.models import Count

    # ── Clinics ──────────────────────────────────────────────────────────────
    clinics = Clinic.objects.filter(main_doctor=request.user, is_active=True).order_by("name")
    clinic_ids = list(clinics.values_list("id", flat=True))

    if not clinic_ids:
        return render(request, "clinics/reports.html", {"no_clinics": True, "clinics": clinics})

    base_qs = Appointment.objects.filter(clinic_id__in=clinic_ids)

    # ── KPI totals ────────────────────────────────────────────────────────────
    total_appointments    = base_qs.count()
    total_unique_patients = base_qs.values("patient").distinct().count()
    total_active_doctors  = ClinicStaff.objects.filter(
        clinic_id__in=clinic_ids, role="DOCTOR", is_active=True
    ).count()

    status_counts = dict(
        base_qs.values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    completed = status_counts.get("COMPLETED", 0)
    cancelled = status_counts.get("CANCELLED", 0)
    no_show   = status_counts.get("NO_SHOW", 0)
    pending   = status_counts.get("PENDING", 0) + status_counts.get("CONFIRMED", 0)

    completion_rate = round(completed / total_appointments * 100, 1) if total_appointments else 0
    no_show_rate    = round(no_show   / total_appointments * 100, 1) if total_appointments else 0
    cancel_rate     = round(cancelled / total_appointments * 100, 1) if total_appointments else 0

    # ── Monthly trend (last 12 months) ────────────────────────────────────────
    def _months_ago(base, n):
        m, y = base.month - n, base.year
        while m <= 0:
            m += 12; y -= 1
        return _date_type(y, m, 1)

    today = timezone.now().date()
    months_seq = [_months_ago(today, i) for i in range(11, -1, -1)]
    monthly_raw = (
        base_qs
        .filter(appointment_date__gte=months_seq[0])
        .values("appointment_date__year", "appointment_date__month")
        .annotate(n=Count("id"))
    )
    monthly_dict   = {(r["appointment_date__year"], r["appointment_date__month"]): r["n"] for r in monthly_raw}
    monthly_labels = [f"{_ARABIC_MONTHS[m.month]} {m.year}" for m in months_seq]
    monthly_data   = [monthly_dict.get((m.year, m.month), 0) for m in months_seq]

    # ── Day-of-week distribution (week_day: 1=Sun … 7=Sat) ───────────────────
    dow_raw = (
        base_qs
        .values("appointment_date__week_day")
        .annotate(n=Count("id"))
    )
    dow_map    = {r["appointment_date__week_day"]: r["n"] for r in dow_raw}
    arabic_days = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
    dow_labels = arabic_days
    dow_data   = [dow_map.get(i + 1, 0) for i in range(7)]

    # ── Peak hours ────────────────────────────────────────────────────────────
    hour_raw = (
        base_qs
        .values("appointment_time__hour")
        .annotate(n=Count("id"))
    )
    hour_map    = {r["appointment_time__hour"]: r["n"] for r in hour_raw}
    hour_labels = [f"{h:02d}:00" for h in range(8, 22)]
    hour_data   = [hour_map.get(h, 0) for h in range(8, 22)]

    # ── Top doctors ───────────────────────────────────────────────────────────
    doctor_qs = (
        base_qs
        .exclude(doctor=None)
        .values("doctor__name")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    doctor_labels = [r["doctor__name"] for r in doctor_qs]
    doctor_data   = [r["n"] for r in doctor_qs]

    # ── Top appointment types ─────────────────────────────────────────────────
    type_qs = (
        base_qs
        .exclude(appointment_type=None)
        .values("appointment_type__name_ar", "appointment_type__name")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    type_labels = [r["appointment_type__name_ar"] or r["appointment_type__name"] for r in type_qs]
    type_data   = [r["n"] for r in type_qs]

    # ── Patient gender breakdown ──────────────────────────────────────────────
    patient_ids = list(base_qs.values_list("patient_id", flat=True).distinct())
    gender_raw  = (
        PatientProfile.objects
        .filter(user_id__in=patient_ids)
        .values("gender")
        .annotate(n=Count("id"))
    )
    gender_label_map = {"M": "ذكر", "F": "أنثى", "": "غير محدد"}
    gender_labels = [gender_label_map.get(r["gender"], "غير محدد") for r in gender_raw]
    gender_data   = [r["n"] for r in gender_raw]
    profiled_count = sum(gender_data)
    no_profile_count = len(patient_ids) - profiled_count
    if no_profile_count > 0:
        gender_labels.append("بدون ملف")
        gender_data.append(no_profile_count)

    # ── New vs returning patients ─────────────────────────────────────────────
    pat_counts         = base_qs.values("patient").annotate(n=Count("id"))
    new_patients       = sum(1 for r in pat_counts if r["n"] == 1)
    returning_patients = sum(1 for r in pat_counts if r["n"] > 1)

    # ── Revenue estimate ──────────────────────────────────────────────────────
    revenue_qs = list(
        base_qs
        .filter(status="COMPLETED", appointment_type__price__gt=0)
        .values("appointment_type__name_ar", "appointment_type__name", "appointment_type__price")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )
    total_revenue = sum(float(r["appointment_type__price"]) * r["count"] for r in revenue_qs)
    revenue_by_type = sorted(
        [
            {
                "name": r["appointment_type__name_ar"] or r["appointment_type__name"],
                "count": r["count"],
                "revenue": float(r["appointment_type__price"]) * r["count"],
            }
            for r in revenue_qs
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )

    # ── Per-clinic breakdown ──────────────────────────────────────────────────
    clinic_stats = []
    for c in clinics:
        c_qs    = base_qs.filter(clinic=c)
        c_total = c_qs.count()
        c_done  = c_qs.filter(status="COMPLETED").count()
        c_can   = c_qs.filter(status="CANCELLED").count()
        c_ns    = c_qs.filter(status="NO_SHOW").count()
        c_docs  = ClinicStaff.objects.filter(clinic=c, role="DOCTOR",    is_active=True).count()
        c_secs  = ClinicStaff.objects.filter(clinic=c, role="SECRETARY", is_active=True).count()
        clinic_stats.append({
            "clinic": c,
            "total": c_total,
            "completed": c_done,
            "cancelled": c_can,
            "no_show": c_ns,
            "doctors": c_docs,
            "secretaries": c_secs,
            "completion_rate": round(c_done / c_total * 100, 1) if c_total else 0,
            "no_show_rate":    round(c_ns   / c_total * 100, 1) if c_total else 0,
            "cancel_rate":     round(c_can  / c_total * 100, 1) if c_total else 0,
        })

    # ── Status chart ──────────────────────────────────────────────────────────
    status_label_map = {
        "PENDING": "معلق", "CONFIRMED": "مؤكد", "CHECKED_IN": "حضر",
        "IN_PROGRESS": "جارٍ", "COMPLETED": "مكتمل",
        "CANCELLED": "ملغى", "NO_SHOW": "لم يحضر",
    }
    status_labels_chart = [status_label_map.get(s, s) for s in status_counts]
    status_data_chart   = list(status_counts.values())

    context = {
        "clinics": clinics,
        "no_clinics": False,
        # KPIs
        "total_appointments":    total_appointments,
        "total_unique_patients": total_unique_patients,
        "total_active_doctors":  total_active_doctors,
        "total_clinics":         len(clinic_ids),
        "completed":             completed,
        "cancelled":             cancelled,
        "no_show":               no_show,
        "pending":               pending,
        "completion_rate":       completion_rate,
        "no_show_rate":          no_show_rate,
        "cancel_rate":           cancel_rate,
        "total_revenue":         total_revenue,
        "new_patients":          new_patients,
        "returning_patients":    returning_patients,
        # Tables
        "clinic_stats":    clinic_stats,
        "revenue_by_type": revenue_by_type,
        # Chart JSON
        "monthly_labels_json":       json.dumps(monthly_labels,        ensure_ascii=False),
        "monthly_data_json":         json.dumps(monthly_data),
        "status_labels_json":        json.dumps(status_labels_chart,   ensure_ascii=False),
        "status_data_json":          json.dumps(status_data_chart),
        "dow_labels_json":           json.dumps(dow_labels,            ensure_ascii=False),
        "dow_data_json":             json.dumps(dow_data),
        "hour_labels_json":          json.dumps(hour_labels),
        "hour_data_json":            json.dumps(hour_data),
        "doctor_labels_json":        json.dumps(doctor_labels,         ensure_ascii=False),
        "doctor_data_json":          json.dumps(doctor_data),
        "type_labels_json":          json.dumps(type_labels,           ensure_ascii=False),
        "type_data_json":            json.dumps(type_data),
        "gender_labels_json":        json.dumps(gender_labels,         ensure_ascii=False),
        "gender_data_json":          json.dumps(gender_data),
        "new_returning_labels_json": json.dumps(["مرضى جدد", "مرضى متكررون"], ensure_ascii=False),
        "new_returning_data_json":   json.dumps([new_patients, returning_patients]),
    }
    return render(request, "clinics/reports.html", context)
