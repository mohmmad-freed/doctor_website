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
    # --- Doctor Invitations Flow ---
    path(
        "invites/",
        views.doctor_invitations_inbox,
        name="doctor_invitations_inbox",
    ),
    path(
        "invites/<int:invitation_id>/accept/",
        views.accept_invitation_view,
        name="accept_invitation",
    ),
    path(
        "invites/<int:invitation_id>/reject/",
        views.reject_invitation_view,
        name="reject_invitation",
    ),
    path(
        "invites/accept/<uuid:token>/",
        views.guest_accept_invitation_view,
        name="guest_accept_invitation",
    ),
    # --- Doctor Verification Flow ---
    path(
        "verification/status/",
        views.doctor_verification_status,
        name="verification_status",
    ),
    path(
        "verification/upload/",
        views.doctor_upload_credentials,
        name="upload_credentials",
    ),
    path(
        "verification/credential/<int:credential_id>/upload/",
        views.doctor_upload_clinic_credential,
        name="upload_clinic_credential",
    ),
    # --- Doctor Profile ---
    path(
        "profile/",
        views.doctor_profile_view,
        name="doctor_profile",
    ),
    path(
        "profile/edit/",
        views.doctor_edit_profile_view,
        name="doctor_edit_profile",
    ),
    # --- Doctor Schedule Management ---
    path(
        "my-schedule/",
        views.my_schedule,
        name="my_schedule",
    ),
    # --- Doctor Appointment Types (self-service) ---
    path(
        "my-appointment-types/",
        views.my_appointment_types,
        name="my_appointment_types",
    ),
    # --- Intake Form Builder ---
    path(
        "intake-forms/<int:appointment_type_id>/",
        views.intake_form_builder,
        name="intake_form_builder",
    ),
    path(
        "intake-forms/template/<int:template_id>/questions/add/",
        views.intake_question_add,
        name="intake_question_add",
    ),
    path(
        "intake-forms/template/<int:template_id>/questions/<int:question_id>/edit/",
        views.intake_question_edit,
        name="intake_question_edit",
    ),
    path(
        "intake-forms/template/<int:template_id>/questions/<int:question_id>/delete/",
        views.intake_question_delete,
        name="intake_question_delete",
    ),
    path(
        "intake-forms/template/<int:template_id>/questions/<int:question_id>/followup/add/",
        views.intake_followup_add,
        name="intake_followup_add",
    ),
    path(
        "intake-forms/template/<int:template_id>/rules/<int:rule_id>/delete/",
        views.intake_rule_delete,
        name="intake_rule_delete",
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