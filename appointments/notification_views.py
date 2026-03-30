"""
Notification Center views (Context-Isolated).

Six endpoints:
1. patient_notifications        — patient portal notifications
2. doctor_notifications         — doctor portal notifications
3. secretary_notifications      — secretary portal notifications
4. clinic_owner_notifications   — clinic owner portal notifications
5. mark_notification_read       — mark a single notification as read (POST, ownership-enforced)
6. mark_all_notifications_read  — mark context-specific notifications as read (POST)

Security:
- @login_required on all views.
- Ownership enforced at ORM level (patient=request.user).
- No cross-user, cross-role, or cross-tenant leakage possible.
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages

from appointments.models import AppointmentNotification


def _resolve_appointment_url(notification):
    """
    Return the best URL to link a notification to based strictly on its context.
    """
    if not notification.appointment_id:
        return None

    from django.urls import reverse

    if notification.context_role == AppointmentNotification.ContextRole.PATIENT:
        return reverse("patients:my_appointments")
    if notification.context_role == AppointmentNotification.ContextRole.DOCTOR:
        return reverse("doctors:appointments")
    if notification.context_role == AppointmentNotification.ContextRole.SECRETARY:
        return reverse("secretary:appointments")
    if notification.context_role == AppointmentNotification.ContextRole.CLINIC_OWNER:
        # Link back to the specific clinic's dashboard
        return reverse("clinics:my_clinic", kwargs={"clinic_id": notification.appointment.clinic_id})
    return None


def _render_notifications(request, context_role, template, base_template):
    user = request.user
    notifications_qs = (
        AppointmentNotification.objects.filter(patient=user, context_role=context_role)
        .select_related("appointment__clinic", "appointment__doctor")
        .order_by("-created_at")
    )

    unread_count = notifications_qs.filter(is_read=False).count()
    total_count = notifications_qs.count()

    notifications = list(notifications_qs)
    for notif in notifications:
        notif.target_url = _resolve_appointment_url(notif)

    context = {
        "notifications": notifications,
        "unread_count": unread_count,
        "total_count": total_count,
        "read_count": total_count - unread_count,
        "base_template": base_template,
        "context_role": context_role,
    }
    return render(request, template, context)


@login_required
def patient_notifications(request):
    if not request.user.has_role("PATIENT"):
        return redirect("accounts:home")
    return _render_notifications(
        request,
        AppointmentNotification.ContextRole.PATIENT,
        "appointments/notifications_center_patient.html",
        None,
    )


@login_required
def doctor_notifications(request):
    if not (request.user.has_role("DOCTOR") or request.user.has_role("MAIN_DOCTOR")):
        return redirect("accounts:home")
    return _render_notifications(
        request,
        AppointmentNotification.ContextRole.DOCTOR,
        "appointments/notifications_center_staff.html",
        "doctors/base_doctor.html",
    )


@login_required
def secretary_notifications(request):
    if not request.user.has_role("SECRETARY"):
        return redirect("accounts:home")
    return _render_notifications(
        request,
        AppointmentNotification.ContextRole.SECRETARY,
        "appointments/notifications_center_staff.html",
        "secretary/base_secretary.html",
    )


@login_required
def clinic_owner_notifications(request):
    """Clinic Owner notification center — strictly CLINIC_OWNER context."""
    if not request.user.has_role("MAIN_DOCTOR"):
        return redirect("accounts:home")
    return _render_notifications(
        request,
        AppointmentNotification.ContextRole.CLINIC_OWNER,
        "appointments/notifications_center_owner.html",
        "accounts/base.html",
    )


@login_required
@require_POST
def mark_notification_read(request, pk):
    """Mark a single notification as read. Ownership strictly enforced."""
    notif = get_object_or_404(AppointmentNotification, pk=pk, patient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save(update_fields=["is_read"])

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if next_url:
        return redirect(next_url)
    return redirect("accounts:home")


@login_required
@require_POST
def mark_all_notifications_read(request):
    """Mark every unread notification for the context as read."""
    context_role = request.POST.get("context_role")
    
    qs = AppointmentNotification.objects.filter(patient=request.user, is_read=False)
    if context_role:
        qs = qs.filter(context_role=context_role)
        
    updated = qs.update(is_read=True)

    if updated:
        messages.success(request, "تم تحديد جميع الإشعارات كمقروءة.")
        
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if next_url:
        return redirect(next_url)
    return redirect("accounts:home")
