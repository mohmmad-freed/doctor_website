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
from django.utils.translation import gettext_lazy as _

from appointments.models import AppointmentNotification


def _resolve_appointment_url(notification):
    """
    Return the best URL to link a notification to based strictly on its context.
    """
    from django.urls import reverse

    # Procurement notifications carry no appointment/subject_patient — route by
    # purchase_request to the right portal's purchase-requests page.
    if not notification.appointment_id and notification.purchase_request_id:
        if notification.context_role == AppointmentNotification.ContextRole.SECRETARY:
            return reverse("secretary:purchase_requests")
        if notification.context_role == AppointmentNotification.ContextRole.CLINIC_OWNER:
            return reverse(
                "clinics:purchase_requests_list",
                kwargs={"clinic_id": notification.purchase_request.clinic_id},
            )

    # Patient-scoped notifications (e.g. a profile note) have no appointment but
    # carry subject_patient — route to that patient's profile in the right portal.
    if not notification.appointment_id:
        if notification.subject_patient_id:
            if notification.context_role == AppointmentNotification.ContextRole.SECRETARY:
                return reverse(
                    "secretary:patient_detail",
                    kwargs={"patient_id": notification.subject_patient_id},
                )
            if notification.context_role == AppointmentNotification.ContextRole.DOCTOR:
                return reverse(
                    "doctors:patient_workspace",
                    kwargs={"patient_id": notification.subject_patient_id},
                )
        return None

    if notification.context_role == AppointmentNotification.ContextRole.PATIENT:
        return reverse("patients:my_appointments")
    if notification.context_role == AppointmentNotification.ContextRole.DOCTOR:
        # Land the doctor on the patient-scoped overview for this exact appointment
        # (instead of the unfiltered full appointments list).
        return reverse(
            "doctors:appointment_overview",
            kwargs={"appointment_id": notification.appointment_id},
        )
    if notification.context_role == AppointmentNotification.ContextRole.SECRETARY:
        # Land the secretary on the patient-scoped overview for this appointment
        # (clinic-wide patient timeline + action controls).
        return reverse(
            "secretary:appointment_overview",
            kwargs={"appointment_id": notification.appointment_id},
        )
    if notification.context_role == AppointmentNotification.ContextRole.CLINIC_OWNER:
        # Link back to the specific clinic's dashboard
        return reverse("clinics:my_clinic", kwargs={"clinic_id": notification.appointment.clinic_id})
    return None


def _render_notifications(request, context_role, template, base_template):
    user = request.user
    notifications_qs = (
        AppointmentNotification.objects.filter(patient=user, context_role=context_role)
        .select_related("appointment__clinic", "appointment__doctor", "subject_patient", "purchase_request")
        .order_by("-created_at")
    )

    unread_count = notifications_qs.filter(is_read=False).count()
    total_count = notifications_qs.count()

    from django.urls import reverse

    notifications = list(notifications_qs)
    for notif in notifications:
        # Route link-based notifications through `open_notification` so the
        # notification is marked read when the user clicks "view appointment".
        dest = _resolve_appointment_url(notif)
        notif.target_url = (
            reverse("appointments:open_notification", args=[notif.pk]) if dest else None
        )
        notif.modal_url = None

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
def open_notification(request, pk):
    """
    Mark a notification as read, then redirect to its appointment destination.

    Used by the "view appointment" link so that opening a notification clears its
    unread state. Ownership strictly enforced. Falls back to the home page if the
    notification has no resolvable destination.
    """
    notif = get_object_or_404(AppointmentNotification, pk=pk, patient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save(update_fields=["is_read"])

    dest = _resolve_appointment_url(notif)
    return redirect(dest or "accounts:home")


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
        messages.success(request, _("تم تحديد جميع الإشعارات كمقروءة."))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if next_url:
        return redirect(next_url)
    return redirect("accounts:home")


@login_required
@require_POST
def delete_notifications(request):
    """
    Hard-delete notifications for the current context. Ownership strictly enforced.

    One endpoint serves all three delete actions, distinguished by ``mode``:
    - ``"read"``     — delete every read notification for the context.
    - ``"selected"`` — delete the notifications whose ids are POSTed in ``ids``
                       (a single-item list for the per-card trash button, or many
                       for the multi-select bulk action).
    """
    context_role = request.POST.get("context_role")
    mode = request.POST.get("mode")

    qs = AppointmentNotification.objects.filter(patient=request.user)
    if context_role:
        qs = qs.filter(context_role=context_role)

    if mode == "read":
        qs = qs.filter(is_read=True)
    else:  # "selected"
        qs = qs.filter(pk__in=request.POST.getlist("ids"))

    deleted, _unused = qs.delete()

    if deleted:
        messages.success(request, _("تم حذف الإشعارات المحددة."))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if next_url:
        return redirect(next_url)
    return redirect("accounts:home")
