from django.urls import path
from . import views, appointment_types_views

app_name = 'clinics'

urlpatterns = [
    # Clinic owner landing — list all owned clinics
    path('', views.my_clinics, name='my_clinics'),

    # Add a new clinic (for already-authenticated clinic owners)
    path('add/', views.add_clinic_code_view, name='add_clinic_code'),
    path('add/details/', views.add_clinic_details_view, name='add_clinic_details'),

    # Clinic switching — sets selected clinic in session, redirects to its dashboard
    path('switch/<int:clinic_id>/', views.switch_clinic, name='switch_clinic'),

    # Per-clinic dashboard and management (all require clinic_id)
    path('<int:clinic_id>/', views.my_clinic, name='my_clinic'),
    path('<int:clinic_id>/staff/', views.manage_staff, name='manage_staff'),
    path('<int:clinic_id>/staff/add/', views.add_staff, name='add_staff'),
    path('<int:clinic_id>/staff/<int:staff_id>/remove/', views.remove_staff, name='remove_staff'),

    # Clinic Invitations
    path('<int:clinic_id>/invitations/', views.invitations_list, name='invitations_list'),
    path('<int:clinic_id>/invitations/create/', views.create_invitation_view, name='create_invitation'),
    path('<int:clinic_id>/invitations/create-secretary/', views.create_secretary_invitation_view, name='create_secretary_invitation'),
    path('<int:clinic_id>/invitations/<int:invitation_id>/cancel/', views.cancel_invitation_view, name='cancel_invitation'),

    # 4-step post-signup verification flow

    path('<int:clinic_id>/verify/owner-phone/', views.verify_owner_phone, name='verify_owner_phone'),
    path('<int:clinic_id>/verify/owner-email/', views.verify_owner_email, name='verify_owner_email'),
    path('<int:clinic_id>/verify/clinic-phone/', views.verify_clinic_phone, name='verify_clinic_phone'),
    path('<int:clinic_id>/verify/clinic-email/', views.verify_clinic_email, name='verify_clinic_email'),

    # Appointment Types Management
    path('<int:clinic_id>/appointment-types/', appointment_types_views.appointment_types_list, name='appointment_types_list'),
    path('<int:clinic_id>/appointment-types/create/', appointment_types_views.appointment_type_create, name='appointment_type_create'),
    path('<int:clinic_id>/appointment-types/<int:type_id>/edit/', appointment_types_views.appointment_type_update, name='appointment_type_update'),
    path('<int:clinic_id>/appointment-types/<int:type_id>/toggle/', appointment_types_views.appointment_type_toggle, name='appointment_type_toggle'),

    # Clinic Working Hours
    path('<int:clinic_id>/settings/working-hours/', views.clinic_working_hours_list_view, name='working_hours_list'),
    path('<int:clinic_id>/settings/working-hours/create/', views.clinic_working_hours_create_view, name='working_hours_create'),
    path('<int:clinic_id>/settings/working-hours/<int:id>/update/', views.clinic_working_hours_update_view, name='working_hours_update'),
    path('<int:clinic_id>/settings/working-hours/<int:id>/delete/', views.clinic_working_hours_delete_view, name='working_hours_delete'),

    # Compliance Settings
    path('<int:clinic_id>/settings/compliance/', views.compliance_settings_view, name='compliance_settings'),
    path('<int:clinic_id>/settings/compliance/update/', views.compliance_settings_update_view, name='compliance_settings_update'),
]
