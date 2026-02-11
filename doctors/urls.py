from django.urls import path
from . import views, api_views

app_name = "doctors"

urlpatterns = [
    # --- Template Views (existing) ---
    path("", views.dashboard, name="dashboard"),
    path("appointments/", views.appointments_list, name="appointments"),
    path(
        "appointments/<int:appointment_id>/",
        views.appointment_detail,
        name="appointment_detail",
    ),
    path("patients/", views.patients_list, name="patients"),
    # --- Patient-facing template views (new) ---
    path(
        "<int:doctor_id>/availability/",
        views.doctor_availability_view,
        name="doctor_availability",
    ),
    path(
        "<int:doctor_id>/appointment-types/",
        views.doctor_appointment_types_view,
        name="doctor_appointment_types",
    ),
    # --- API Endpoints (new) ---
    path(
        "api/<int:doctor_id>/availability/",
        api_views.DoctorAvailabilityListAPIView.as_view(),
        name="api_doctor_availability",
    ),
    path(
        "api/<int:doctor_id>/available-slots/",
        api_views.DoctorAvailableSlotsAPIView.as_view(),
        name="api_doctor_available_slots",
    ),
    path(
        "api/<int:doctor_id>/appointment-types/",
        api_views.DoctorAppointmentTypesAPIView.as_view(),
        name="api_doctor_appointment_types",
    ),
]