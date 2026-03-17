"""
Context processor: injects unread_notification_count into every template context.

Used by navbar bell badges across all dashboard types.
Safe for anonymous users (returns 0).
"""

from appointments.models import AppointmentNotification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {}
    count = AppointmentNotification.objects.filter(
        patient=request.user,
        is_read=False,
    ).count()
    return {"unread_notification_count": count}
