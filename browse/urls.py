"""Public, anonymous-accessible catalog of clinics and doctors.

Guest browse mode (Phase 1): read-only, no @login_required, no PATIENT role
check. These views deliberately reuse the *querysets* of the patient-facing
browse (active-clinic + verified-doctor rules) but expose only whitelisted,
public-safe fields — never owner/staff PII, internal status, tokens, or prices.
"""
from django.urls import path

from . import views

app_name = "browse"

urlpatterns = [
    path("", views.clinic_list, name="index"),
    path("clinics/<int:clinic_id>/", views.clinic_detail, name="clinic_detail"),
    path("doctors/<int:doctor_id>/", views.doctor_detail, name="doctor_detail"),
]
