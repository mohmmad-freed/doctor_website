from django.urls import path
from . import views, api_views, notification_views

app_name = "appointments"

urlpatterns = [
    # --- Notification Center ---
    path(
        "notifications/patient/",
        notification_views.patient_notifications,
        name="patient_notifications",
    ),
    path(
        "notifications/doctor/",
        notification_views.doctor_notifications,
        name="doctor_notifications",
    ),
    path(
        "notifications/secretary/",
        notification_views.secretary_notifications,
        name="secretary_notifications",
    ),
    path(
        "notifications/clinic-owner/",
        notification_views.clinic_owner_notifications,
        name="clinic_owner_notifications",
    ),
    path(
        "notifications/<int:pk>/read/",
        notification_views.mark_notification_read,
        name="mark_notification_read",
    ),
    path(
        "notifications/mark-all-read/",
        notification_views.mark_all_notifications_read,
        name="mark_all_notifications_read",
    ),

    # --- Template Views (Patient-facing) ---
    path(
        "book/<int:clinic_id>/",
        views.book_appointment_view,
        name="book_appointment",
    ),
    path(
        "confirmation/<int:appointment_id>/",
        views.booking_confirmation,
        name="booking_confirmation",
    ),

    # --- HTMX Partials ---
    path(
        "<int:clinic_id>/htmx/appointment-types/",
        views.load_appointment_types,
        name="htmx_appointment_types",
    ),
    path(
        "<int:clinic_id>/htmx/slots/",
        views.load_available_slots,
        name="htmx_slots",
    ),

    # --- API Endpoints ---
    path(
        "api/book/",
        api_views.BookAppointmentAPIView.as_view(),
        name="api_book_appointment",
    ),
    
    path(
    "<int:clinic_id>/htmx/intake-form/",
    views.load_intake_form,
    name="htmx_intake_form",
    ),
]