from django.urls import path
from . import views, api_views

app_name = "doctors"

urlpatterns = [
    # --- Template Views (staff) ---
    path("", views.dashboard, name="dashboard"),
    path("appointments/", views.appointments_list, name="appointments"),
    path(
        "appointments/<int:appointment_id>/",
        views.appointment_detail,
        name="appointment_detail",
    ),
    path("patients/", views.patients_list, name="patients"),
    # --- Patient-facing template views ---
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
    # --- API Endpoints ---
    path(
        "api/specialties/",
        api_views.SpecialtyListAPIView.as_view(),
        name="api_specialties",
    ),
    path(
        "api/list/",
        api_views.DoctorListAPIView.as_view(),
        name="api_doctor_list",
    ),
    path(
        "api/by-specialty/<int:specialty_id>/",
        api_views.DoctorsBySpecialtyAPIView.as_view(),
        name="api_doctors_by_specialty",
    ),
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