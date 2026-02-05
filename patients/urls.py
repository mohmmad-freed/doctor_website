from django.urls import path
from . import views

app_name = "patients"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("clinics/", views.clinics_list, name="clinics_list"),
    path("appointments/", views.my_appointments, name="my_appointments"),
    path(
        "appointments/book/<int:clinic_id>/",
        views.book_appointment,
        name="book_appointment",
    ),
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
]
