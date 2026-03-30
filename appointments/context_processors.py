"""
Context processor: injects context-specific unread_notification_count into every template.

Used by navbar bell badges across all dashboard types.
Safe for anonymous users (returns 0).
"""

from appointments.models import AppointmentNotification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {}
        
    qs = AppointmentNotification.objects.filter(
        patient=request.user,
        is_read=False,
    )
    
    patient_count = qs.filter(context_role=AppointmentNotification.ContextRole.PATIENT).count()
    doctor_count = qs.filter(context_role=AppointmentNotification.ContextRole.DOCTOR).count()
    secretary_count = qs.filter(context_role=AppointmentNotification.ContextRole.SECRETARY).count()
    clinic_owner_count = qs.filter(context_role=AppointmentNotification.ContextRole.CLINIC_OWNER).count()
    
    return {
        "unread_patient_notification_count": patient_count,
        "unread_doctor_notification_count": doctor_count,
        "unread_secretary_notification_count": secretary_count,
        "unread_clinic_owner_notification_count": clinic_owner_count,
        "unread_notification_count": patient_count + doctor_count + secretary_count + clinic_owner_count,
    }
