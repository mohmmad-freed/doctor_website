from datetime import date

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.http import HttpResponseForbidden

from appointments.models import Appointment, AppointmentType

User = get_user_model()


def _require_secretary(request):
    """Return the secretary's ClinicStaff record, or None if not a secretary."""
    from clinics.models import ClinicStaff
    return ClinicStaff.objects.filter(
        user=request.user, role="SECRETARY", is_active=True
    ).select_related("clinic").first()


@login_required
def dashboard(request):
    """Secretary daily overview: today's appointments for their assigned clinic."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    today = date.today()
    todays_appointments = (
        Appointment.objects.filter(clinic=clinic, appointment_date=today)
        .exclude(status=Appointment.Status.CANCELLED)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_time")
    )
    upcoming_count = Appointment.objects.filter(
        clinic=clinic,
        appointment_date__gte=today,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
    ).count()

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]
    return render(request, "secretary/dashboard.html", {
        "clinic": clinic,
        "todays_appointments": todays_appointments,
        "today": today,
        "upcoming_count": upcoming_count,
        "terminal_statuses": terminal_statuses,
    })


@login_required
def appointments_list(request):
    """Full appointment list for the secretary's clinic with basic status filter."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")

    qs = (
        Appointment.objects.filter(clinic=clinic)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("-appointment_date", "appointment_time")
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_filter:
        try:
            from datetime import datetime
            filter_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            qs = qs.filter(appointment_date=filter_date)
        except ValueError:
            pass

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]
    return render(request, "secretary/appointments_list.html", {
        "clinic": clinic,
        "appointments": qs,
        "status_choices": Appointment.Status.choices,
        "current_status": status_filter,
        "current_date": date_filter,
        "terminal_statuses": terminal_statuses,
    })


@login_required
def create_appointment(request):
    """Secretary books an appointment on behalf of a patient."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    from clinics.models import ClinicStaff as CS
    doctors_qs = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR", "MAIN_DOCTOR"], is_active=True
    ).select_related("user")
    if clinic.main_doctor:
        doctor_users = [clinic.main_doctor] + [s.user for s in doctors_qs if s.user != clinic.main_doctor]
    else:
        doctor_users = [s.user for s in doctors_qs]

    appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    # Build the set of valid doctor IDs for this clinic (for server-side validation)
    valid_doctor_ids = {u.id for u in doctor_users}

    if request.method == "POST":
        from appointments.services import book_appointment, BookingError
        try:
            patient_phone = request.POST.get("patient_phone", "").strip()
            doctor_id = int(request.POST.get("doctor_id") or 0)
            type_id = int(request.POST.get("appointment_type_id") or 0)
            date_str = request.POST.get("appointment_date", "").strip()
            time_str = request.POST.get("appointment_time", "").strip()
            reason = request.POST.get("reason", "").strip()

            if not all([patient_phone, doctor_id, type_id, date_str, time_str]):
                messages.error(request, "يرجى ملء جميع الحقول المطلوبة.")
                return redirect("secretary:create_appointment")

            # S-02: Validate doctor belongs to this clinic
            if doctor_id not in valid_doctor_ids:
                messages.error(request, "الطبيب المحدد لا ينتمي إلى هذه العيادة.")
                return redirect("secretary:create_appointment")

            from datetime import datetime as dt_cls
            appt_date = dt_cls.strptime(date_str, "%Y-%m-%d").date()
            appt_time = dt_cls.strptime(time_str, "%H:%M").time()

            # S-01: Look up patient by phone (never trust a raw user ID from the form)
            normalized = PhoneNumberAuthBackend.normalize_phone_number(patient_phone)
            try:
                patient = User.objects.get(phone=normalized)
            except User.DoesNotExist:
                messages.error(request, "لا يوجد مريض مسجل بهذا الرقم.")
                return redirect("secretary:create_appointment")

            # S-01: Ensure the user is actually a patient
            patient_roles = patient.roles or []
            if patient.role != "PATIENT" and "PATIENT" not in patient_roles:
                messages.error(request, "المستخدم المحدد ليس مريضاً.")
                return redirect("secretary:create_appointment")

            appointment = book_appointment(
                patient=patient,
                doctor_id=doctor_id,
                clinic_id=clinic.id,
                appointment_type_id=type_id,
                appointment_date=appt_date,
                appointment_time=appt_time,
                reason=reason,
            )
            # Record that the secretary created this booking
            appointment.created_by = request.user
            appointment.save(update_fields=["created_by"])

            messages.success(request, "تم حجز الموعد بنجاح.")
            return redirect("secretary:appointments")
        except BookingError as e:
            messages.error(request, e.message)
        except Exception as e:
            messages.error(request, f"حدث خطأ: {e}")

    return render(request, "secretary/create_appointment.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "appointment_types": appointment_types,
    })


@login_required
def edit_appointment(request, appointment_id):
    """Secretary reschedules or updates an appointment."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=clinic)

    # Block editing of terminal or in-progress appointments (S-07/S-08)
    _NON_EDITABLE = {
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
        Appointment.Status.CHECKED_IN,
        Appointment.Status.IN_PROGRESS,
    }
    if appointment.status in _NON_EDITABLE:
        messages.error(request, "لا يمكن تعديل هذا الموعد في حالته الحالية.")
        return redirect("secretary:appointments")

    appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    if request.method == "POST":
        try:
            new_type_id = int(request.POST.get("appointment_type_id") or 0)
            new_date_str = request.POST.get("appointment_date", "").strip()
            new_time_str = request.POST.get("appointment_time", "").strip()
            new_reason = request.POST.get("reason", "").strip()

            if not all([new_type_id, new_date_str, new_time_str]):
                messages.error(request, "يرجى ملء جميع الحقول المطلوبة.")
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            from datetime import datetime as dt_cls
            new_date = dt_cls.strptime(new_date_str, "%Y-%m-%d").date()
            new_time = dt_cls.strptime(new_time_str, "%H:%M").time()

            # S-06: Prevent rescheduling to a past date
            if new_date < date.today():
                messages.error(request, "لا يمكن جدولة موعد في تاريخ ماضٍ.")
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            new_type = get_object_or_404(AppointmentType, id=new_type_id, clinic=clinic, is_active=True)

            # S-05: Check for slot conflicts with other confirmed appointments for the same doctor
            # (only if date, time, or doctor changes)
            date_or_time_changed = (
                new_date != appointment.appointment_date
                or new_time != appointment.appointment_time
            )
            if date_or_time_changed and appointment.doctor_id:
                conflict = Appointment.objects.filter(
                    doctor_id=appointment.doctor_id,
                    appointment_date=new_date,
                    appointment_time=new_time,
                    status__in=[
                        Appointment.Status.CONFIRMED,
                        Appointment.Status.CHECKED_IN,
                        Appointment.Status.IN_PROGRESS,
                    ],
                ).exclude(pk=appointment.pk).exists()
                if conflict:
                    messages.error(request, "هذا الوقت محجوز بالفعل لدى هذا الطبيب. يرجى اختيار وقت آخر.")
                    return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            old_date = appointment.appointment_date
            old_time = appointment.appointment_time

            appointment.appointment_type = new_type
            appointment.appointment_date = new_date
            appointment.appointment_time = new_time
            if new_reason:
                appointment.reason = new_reason
            appointment.save(update_fields=["appointment_type", "appointment_date", "appointment_time", "reason", "updated_at"])

            # Notify patient if date/time changed
            if date_or_time_changed:
                from django.db import transaction as _txn
                from appointments.services.appointment_notification_service import (
                    notify_appointment_rescheduled_by_staff,
                )
                _txn.on_commit(
                    lambda: notify_appointment_rescheduled_by_staff(appointment, old_date, old_time)
                )

            messages.success(request, "تم تحديث الموعد بنجاح.")
            return redirect("secretary:appointments")
        except Exception as e:
            messages.error(request, f"حدث خطأ: {e}")

    return render(request, "secretary/edit_appointment.html", {
        "clinic": clinic,
        "appointment": appointment,
        "appointment_types": appointment_types,
    })


@login_required
def cancel_appointment(request, appointment_id):
    """Secretary cancels an appointment."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    if request.method == "POST":
        from appointments.services.patient_appointments_service import cancel_appointment_by_staff
        try:
            cancel_appointment_by_staff(appointment_id, staff)
            messages.success(request, "تم إلغاء الموعد بنجاح.")
        except Exception as e:
            messages.error(request, f"حدث خطأ أثناء الإلغاء: {e}")

    return redirect("secretary:appointments")


# ============================================
# SECRETARY INVITATIONS FLOW
# ============================================

from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from clinics.models import ClinicInvitation
from clinics.services import accept_invitation, reject_invitation
from accounts.backends import PhoneNumberAuthBackend


@login_required
def secretary_invitations_inbox(request):
    """View pending invitations for the logged-in secretary."""
    user = request.user
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    
    invitations = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, 
        role="SECRETARY",
        status="PENDING"
    ).select_related('clinic', 'invited_by').order_by('-created_at')
    
    return render(request, "secretary/invitations_inbox.html", {
        "invitations": invitations,
    })


@login_required
def accept_invitation_view(request, invitation_id):
    """Action to accept a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")

    # Verify this invitation belongs to the logged-in user's phone (prevents IDOR)
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    if normalized_phone != invitation.doctor_phone:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة."
        })

    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, f"تم الانضمام بنجاح إلى عيادة {staff.clinic.name} بصفة سكرتير/ة.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    return redirect(reverse("secretary:secretary_invitations_inbox"))


@login_required
def reject_invitation_view(request, invitation_id):
    """Action to reject a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")

    # Verify this invitation belongs to the logged-in user's phone (prevents IDOR)
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    if normalized_phone != invitation.doctor_phone:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة."
        })

    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, "تم رفض الدعوة.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")

    return redirect(reverse("secretary:secretary_invitations_inbox"))


def guest_accept_invitation_view(request, token):
    """
    Public endpoint for SMS link. 
    Shows generic error if token invalid/expired.
    If valid, redirects to login/reg storing token in session.
    """
    try:
        invitation = ClinicInvitation.objects.get(token=token, role="SECRETARY")
    except ClinicInvitation.DoesNotExist:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "رابط الدعوة غير صالح أو قد تم استخدامه مسبقاً."
        })
        
    if invitation.status != "PENDING" or invitation.is_expired:
         return render(request, "secretary/invalid_invitation.html", {
            "error": "انتهت صلاحية هذه الدعوة أو لم تعد متاحة."
        })
        
    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
             # Already logged in as the right user, redirect to inbox to accept
             return redirect(reverse("secretary:secretary_invitations_inbox"))
        else:
             # Logged in as someone else (wrong phone)
             return render(request, "secretary/invalid_invitation.html", {
                "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة. يرجى تسجيل الدخول بالحساب الصحيح."
            })
            
    # Unauthenticated but token is valid: store generic next url and redirect to login
    request.session["pending_invitation_token"] = str(token)  # UUID must be str for JSON session
    request.session["pending_invitation_app"] = "secretary"
    
    messages.info(request, "يرجى تسجيل الدخول أو إنشاء حساب جديد لقبول دعوة الانضمام للعيادة كـ سكرتير/ة.")
    return redirect(reverse("accounts:login"))