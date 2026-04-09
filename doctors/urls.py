from django.urls import path
from . import views, api_views, catalog_views

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
    # --- Patient Workspace ---
    path("patients/<int:patient_id>/", views.patient_workspace, name="patient_workspace"),
    path("patients/<int:patient_id>/notes/add/", views.ws_note_add, name="ws_note_add"),
    path("patients/<int:patient_id>/notes/<int:note_id>/edit/", views.ws_note_edit, name="ws_note_edit"),
    path("patients/<int:patient_id>/notes/<int:note_id>/delete/", views.ws_note_delete, name="ws_note_delete"),
    path("patients/<int:patient_id>/orders/add/", views.ws_order_add, name="ws_order_add"),
    path("patients/<int:patient_id>/orders/<int:order_id>/update/", views.ws_order_update, name="ws_order_update"),
    path("patients/<int:patient_id>/orders/<int:order_id>/edit/", views.ws_order_edit, name="ws_order_edit"),
    path("patients/<int:patient_id>/orders/<int:order_id>/delete/", views.ws_order_delete, name="ws_order_delete"),
    path("patients/<int:patient_id>/prescriptions/add/", views.ws_prescription_add, name="ws_prescription_add"),
    path("patients/<int:patient_id>/prescriptions/<int:rx_id>/print/", views.ws_prescription_print, name="ws_prescription_print"),
    path("patients/<int:patient_id>/prescriptions/<int:rx_id>/delete/", views.ws_prescription_delete, name="ws_prescription_delete"),
    path("patients/<int:patient_id>/prescriptions/from-order/<int:order_id>/", views.ws_prescription_from_order, name="ws_prescription_from_order"),
    path("patients/<int:patient_id>/prescriptions/<int:rx_id>/toggle-active/", views.ws_prescription_toggle_active, name="ws_prescription_toggle_active"),
    path("patients/<int:patient_id>/prescriptions/print-active/", views.ws_prescription_print_active, name="ws_prescription_print_active"),
    path("patients/<int:patient_id>/records/upload/", views.ws_record_upload, name="ws_record_upload"),
    path("patients/<int:patient_id>/records/<int:record_id>/delete/", views.ws_record_delete, name="ws_record_delete"),
    # --- Schedule Follow-up (doctor-side appointment creation) ---
    path("patients/<int:patient_id>/schedule-followup/", views.ws_schedule_followup, name="ws_schedule_followup"),
    path("patients/<int:patient_id>/schedule-followup/slots/", views.htmx_followup_slots, name="htmx_followup_slots"),
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
        "profile/verify-phone/",
        views.doctor_verify_phone_view,
        name="verify_phone",
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
    # --- Order Catalog (MAIN_DOCTOR only) ---
    path("order-catalog/", catalog_views.order_catalog, name="order_catalog"),
    path("order-catalog/families/create/", catalog_views.drug_family_create, name="drug_family_create"),
    path("order-catalog/families/<int:family_id>/edit/", catalog_views.drug_family_edit, name="drug_family_edit"),
    path("order-catalog/families/<int:family_id>/delete/", catalog_views.drug_family_delete, name="drug_family_delete"),
    path("order-catalog/drugs/create/", catalog_views.drug_product_create, name="drug_product_create"),
    path("order-catalog/drugs/<int:product_id>/edit/", catalog_views.drug_product_edit, name="drug_product_edit"),
    path("order-catalog/drugs/<int:product_id>/delete/", catalog_views.drug_product_delete, name="drug_product_delete"),
    path("order-catalog/items/create/", catalog_views.catalog_item_create, name="catalog_item_create"),
    path("order-catalog/items/<int:item_id>/edit/", catalog_views.catalog_item_edit, name="catalog_item_edit"),
    path("order-catalog/items/<int:item_id>/delete/", catalog_views.catalog_item_delete, name="catalog_item_delete"),
    # --- Order Catalog HTMX search (patient workspace) ---
    path("patients/<int:patient_id>/catalog/drugs/", views.htmx_catalog_drug_search, name="htmx_catalog_drug_search"),
    path("patients/<int:patient_id>/catalog/items/", views.htmx_catalog_nondrug_search, name="htmx_catalog_nondrug_search"),
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
    # --- Clinical Note Templates ---
    path(
        "clinical-note-templates/",
        views.clinical_note_templates,
        name="clinical_note_templates",
    ),
    path(
        "clinical-note-templates/create/",
        views.clinical_note_template_create,
        name="clinical_note_template_create",
    ),
    path(
        "clinical-note-templates/<int:template_id>/edit/",
        views.clinical_note_template_edit,
        name="clinical_note_template_edit",
    ),
    path(
        "clinical-note-templates/<int:template_id>/activate/",
        views.clinical_note_template_activate,
        name="clinical_note_template_activate",
    ),
    path(
        "clinical-note-templates/<int:template_id>/delete/",
        views.clinical_note_template_delete,
        name="clinical_note_template_delete",
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