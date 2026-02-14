from django.urls import path
from . import views, api_views

app_name = "appointments"

urlpatterns = [
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
]