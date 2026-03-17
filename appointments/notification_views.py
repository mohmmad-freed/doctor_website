"""
Notification Center views.

Three endpoints:
1. notifications_center   — paginated list of the current user's notifications
2. mark_notification_read — mark a single notification as read (POST, ownership-enforced)
3. mark_all_notifications_read — mark all of current user's unread notifications as read (POST)

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


def _resolve_appointment_url(notification, user):
    """
    Return the best URL to link a notification to, or None.

    Patients → my_appointments page.
    Doctors  → appointments list.
    Secretaries → appointments list.
    """
    if not notification.appointment_id:
        return None

    from django.urls import reverse

    if user.has_role("PATIENT"):
        return reverse("patients:my_appointments")
    if user.has_role("DOCTOR") or user.has_role("MAIN_DOCTOR"):
        return reverse("doctors:appointments")
    if user.has_role("SECRETARY"):
        return reverse("secretary:appointments")
    return None


@login_required
def notifications_center(request):
    """Paginated notification inbox for the current user."""
    notifications_qs = (
        AppointmentNotification.objects.filter(patient=request.user)
        .select_related(
            "appointment__clinic",
            "appointment__doctor",
        )
        .order_by("-created_at")
    )

    unread_count = notifications_qs.filter(is_read=False).count()
    total_count = notifications_qs.count()

    # Annotate each notification with its target URL (done in Python to avoid
    # template logic being aware of role → URL mapping).
    notifications = list(notifications_qs)
    for notif in notifications:
        notif.target_url = _resolve_appointment_url(notif, request.user)

    # If the user has a PATIENT role, always show the patient-portal layout.
    # Multi-role users (e.g. PATIENT + MAIN_DOCTOR) are treated as patients
    # here because the notification bell lives in the patient dashboard navbar.
    if request.user.has_role("PATIENT"):
        template = "appointments/notifications_center_patient.html"
    else:
        template = "appointments/notifications_center_staff.html"

    context = {
        "notifications": notifications,
        "unread_count": unread_count,
        "total_count": total_count,
    }
    return render(request, template, context)


@login_required
@require_POST
def mark_notification_read(request, pk):
    """Mark a single notification as read. Ownership strictly enforced."""
    notif = get_object_or_404(AppointmentNotification, pk=pk, patient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save(update_fields=["is_read"])

    # Honour explicit next param, then HTTP_REFERER, then fallback to center.
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if next_url:
        return redirect(next_url)
    return redirect("appointments:notifications_center")


@login_required
@require_POST
def mark_all_notifications_read(request):
    """Mark every unread notification for the current user as read."""
    updated = AppointmentNotification.objects.filter(
        patient=request.user,
        is_read=False,
    ).update(is_read=True)

    if updated:
        messages.success(request, "تم تحديد جميع الإشعارات كمقروءة.")
    return redirect("appointments:notifications_center")
