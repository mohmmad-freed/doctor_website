from django.urls import path
from . import views

app_name = 'secretary'

urlpatterns = [
    # --- Secretary Dashboard (stub — planned) ---
    path('', views.dashboard, name='dashboard'),

    # --- Secretary Appointment Management ---
    path('appointments/', views.appointments_list, name='appointments'),
    path('appointments/create/', views.create_appointment, name='create_appointment'),
    path('appointments/<int:appointment_id>/edit/', views.edit_appointment, name='edit_appointment'),
    path('appointments/<int:appointment_id>/cancel/', views.cancel_appointment, name='cancel_appointment'),

    # --- Secretary Invitation Flow ---
    path('invites/', views.secretary_invitations_inbox, name='secretary_invitations_inbox'),
    path('invites/<int:invitation_id>/accept/', views.accept_invitation_view, name='accept_invitation'),
    path('invites/<int:invitation_id>/reject/', views.reject_invitation_view, name='reject_invitation'),
    path('invites/accept/<uuid:token>/', views.guest_accept_invitation_view, name='guest_accept_invitation'),

    # --- Patient Registration ---
    path('patients/register/', views.register_patient, name='register_patient'),
    path('patients/register/submit/', views.register_patient_submit, name='register_patient_submit'),
    path('patients/search/', views.patient_search_htmx, name='patient_search'),
    path('patients/<int:patient_id>/card/', views.patient_detail_htmx, name='patient_card'),
]