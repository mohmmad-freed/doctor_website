from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone

from accounts.otp_utils import request_otp, verify_otp, is_in_cooldown, get_remaining_resends
from accounts.email_utils import send_email_otp, verify_email_otp, is_email_otp_in_cooldown
from .models import Clinic, ClinicSubscription, ClinicVerification


@login_required
def my_clinic(request):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    subscription = getattr(clinic, "subscription", None)
    return render(request, "clinics/my_clinic.html", {
        "clinic": clinic,
        "subscription": subscription,
    })


@login_required
def manage_staff(request):
    return HttpResponse("Manage Clinic Staff - Coming Soon!")


@login_required
def add_staff(request):
    return HttpResponse("Add Staff Member - Coming Soon!")


@login_required
def remove_staff(request, staff_id):
    return HttpResponse(f"Remove Staff {staff_id} - Coming Soon!")


# ============================================
# CLINIC CHANNEL VERIFICATION FLOW
# ============================================

def _activate_clinic_if_ready(clinic, verification):
    """Set clinic status to ACTIVE when all required channels are verified."""
    if verification.is_fully_verified and clinic.status == "PENDING":
        clinic.status = "ACTIVE"
        clinic.save()


@login_required
def verify_owner_phone(request):
    """Step 1: Verify clinic owner's personal phone via SMS OTP."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Skip if already verified
    if verification.owner_phone_verified_at:
        return redirect(verification.next_pending_step() or "clinics:my_clinic")

    phone = request.user.phone
    welcome_name = request.session.pop("clinic_welcome_name", None)

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = request_otp(phone)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect("clinics:verify_owner_phone")

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_otp(phone, entered_otp)
        if success:
            verification.owner_phone_verified_at = timezone.now()
            verification.save()
            # Pre-send OTP for step 2 (owner email)
            send_email_otp(request.user.email, request.user.name)
            return redirect("clinics:verify_owner_email")
        messages.error(request, msg)

    return render(request, "clinics/verify_owner_phone.html", {
        "phone": phone,
        "cooldown": is_in_cooldown(phone),
        "remaining_resends": get_remaining_resends(phone),
        "step": 1,
        "total_steps": 4,
        "welcome_name": welcome_name,
    })


@login_required
def verify_owner_email(request):
    """Step 2: Verify clinic owner's email via email OTP."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guard
    if not verification.owner_phone_verified_at:
        return redirect("clinics:verify_owner_phone")

    # Skip if already verified
    if verification.owner_email_verified_at:
        return redirect(verification.next_pending_step() or "clinics:my_clinic")

    email = request.user.email

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = send_email_otp(email, request.user.name)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect("clinics:verify_owner_email")

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_email_otp(email, entered_otp)
        if success:
            verification.owner_email_verified_at = timezone.now()
            verification.save()
            # Pre-send OTP for step 3 (clinic phone)
            request_otp(clinic.phone)
            return redirect("clinics:verify_clinic_phone")
        messages.error(request, msg)

    return render(request, "clinics/verify_owner_email.html", {
        "email": email,
        "cooldown": is_email_otp_in_cooldown(email),
        "step": 2,
        "total_steps": 4,
    })


@login_required
def verify_clinic_phone(request):
    """Step 3: Verify clinic's phone number via SMS OTP."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guards
    if not verification.owner_phone_verified_at:
        return redirect("clinics:verify_owner_phone")
    if not verification.owner_email_verified_at:
        return redirect("clinics:verify_owner_email")

    # Skip if already verified
    if verification.clinic_phone_verified_at:
        return redirect(verification.next_pending_step() or "clinics:my_clinic")

    phone = clinic.phone

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = request_otp(phone)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect("clinics:verify_clinic_phone")

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_otp(phone, entered_otp)
        if success:
            verification.clinic_phone_verified_at = timezone.now()
            verification.save()
            if clinic.email:
                # Pre-send OTP for step 4 (clinic email)
                send_email_otp(clinic.email, clinic.name)
                return redirect("clinics:verify_clinic_email")
            # No clinic email — activate now if all required steps done
            _activate_clinic_if_ready(clinic, verification)
            messages.success(request, "تم التحقق من جميع القنوات! عيادتك أصبحت نشطة.")
            return redirect("clinics:my_clinic")
        messages.error(request, msg)

    return render(request, "clinics/verify_clinic_phone.html", {
        "phone": phone,
        "cooldown": is_in_cooldown(phone),
        "remaining_resends": get_remaining_resends(phone),
        "step": 3,
        "total_steps": 4,
        "has_clinic_email": bool(clinic.email),
    })


@login_required
def verify_clinic_email(request):
    """Step 4 (optional): Verify clinic's email address via email OTP."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    verification = getattr(clinic, "verification", None)
    if not verification:
        return redirect("accounts:home")

    # Sequential guards
    if not verification.owner_phone_verified_at:
        return redirect("clinics:verify_owner_phone")
    if not verification.owner_email_verified_at:
        return redirect("clinics:verify_owner_email")
    if not verification.clinic_phone_verified_at:
        return redirect("clinics:verify_clinic_phone")

    # Step only applicable when clinic has an email
    if not clinic.email:
        _activate_clinic_if_ready(clinic, verification)
        return redirect("clinics:my_clinic")

    # Skip if already verified
    if verification.clinic_email_verified_at:
        _activate_clinic_if_ready(clinic, verification)
        return redirect("clinics:my_clinic")

    email = clinic.email

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            success, msg = send_email_otp(email, clinic.name)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect("clinics:verify_clinic_email")

        entered_otp = request.POST.get("otp", "").strip()
        success, msg = verify_email_otp(email, entered_otp)
        if success:
            verification.clinic_email_verified_at = timezone.now()
            verification.save()
            _activate_clinic_if_ready(clinic, verification)
            messages.success(request, "تم التحقق من جميع القنوات! عيادتك أصبحت نشطة.")
            return redirect("clinics:my_clinic")
        messages.error(request, msg)

    return render(request, "clinics/verify_clinic_email.html", {
        "email": email,
        "cooldown": is_email_otp_in_cooldown(email),
        "step": 4,
        "total_steps": 4,
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
def clinic_working_hours_list_view(request):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
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
        "days": days
    })

@login_required
def clinic_working_hours_create_view(request):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
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
            
    return redirect("clinics:working_hours_list")

@login_required
def clinic_working_hours_update_view(request, id):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
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
            
    return redirect("clinics:working_hours_list")

@login_required
def clinic_working_hours_delete_view(request, id):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    instance = get_object_or_404(ClinicWorkingHours, id=id, clinic=clinic)
    
    if request.method == "POST":
        delete_working_hours(instance)
        messages.success(request, "تم حذف ساعات العمل بنجاح.")
        
    return redirect("clinics:working_hours_list")


# ============================================
# CLINIC COMPLIANCE SETTINGS
# ============================================
from .services import get_clinic_compliance_settings, update_clinic_compliance_settings


@login_required
def compliance_settings_view(request):
    """Display current compliance settings for the clinic owner."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    settings = get_clinic_compliance_settings(clinic)
    return render(request, "clinics/compliance_settings.html", {
        "clinic": clinic,
        "settings": settings,
    })


@login_required
def compliance_settings_update_view(request):
    """Update compliance settings (POST only)."""
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)

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

    return redirect("clinics:compliance_settings")
