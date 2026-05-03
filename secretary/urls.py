from django.urls import path
from . import views

app_name = 'secretary'

urlpatterns = [
    # --- Dashboard ---
    path('', views.dashboard, name='dashboard'),

    # --- Appointment Management ---
    path('appointments/', views.appointments_list, name='appointments'),
    path('appointments/create/', views.create_appointment, name='create_appointment'),
    path('appointments/<int:appointment_id>/', views.appointment_detail, name='appointment_detail'),
    path('appointments/<int:appointment_id>/edit/', views.edit_appointment, name='edit_appointment'),
    path('appointments/<int:appointment_id>/cancel/', views.cancel_appointment, name='cancel_appointment'),
    path('appointments/<int:appointment_id>/checkin/', views.checkin_appointment, name='checkin_appointment'),
    path('appointments/<int:appointment_id>/status/', views.update_appointment_status, name='update_appointment_status'),
    path('calendar/', views.calendar_view, name='calendar'),

    # --- Calendar JSON feed (FullCalendar) ---
    path('appointments.json', views.appointments_json, name='appointments_json'),

    # --- Waiting Room ---
    path('waiting-room/', views.waiting_room, name='waiting_room'),
    path('waiting-room/display/', views.waiting_room_display, name='waiting_room_display'),
    path('waiting-room/checkin/', views.checkin_search, name='checkin_search'),
    path('htmx/waiting-room-confirmed/', views.waiting_room_confirmed_htmx, name='waiting_room_confirmed_htmx'),
    path('htmx/waiting-room-checkedin/', views.waiting_room_checkedin_htmx, name='waiting_room_checkedin_htmx'),

    # --- Patient Management ---
    path('patients/', views.patient_list, name='patient_list'),
    path('patients/new/', views.create_new_patient, name='create_new_patient'),
    path('patients/<int:patient_id>/', views.patient_detail, name='patient_detail'),
    path('patients/<int:patient_id>/edit/', views.edit_patient, name='edit_patient'),
    path('patients/htmx/search/', views.patient_list_htmx, name='patient_list_htmx'),
    path('patients/search/', views.patient_search_htmx, name='patient_search'),
    path('patients/<int:patient_id>/card/', views.patient_detail_htmx, name='patient_card'),

    # --- Billing ---
    path('billing/', views.billing_invoices, name='billing_invoices'),
    path('billing/daily-summary/', views.daily_summary, name='daily_summary'),

    # --- Reports ---
    path('reports/', views.reports_index, name='reports_index'),
    path('reports/daily/', views.report_daily, name='report_daily'),
    path('reports/visits/', views.report_visits, name='report_visits'),
    path('reports/noshows/', views.report_noshows, name='report_noshows'),
    path('reports/doctors/', views.report_doctors, name='report_doctors'),

    # --- Doctor Schedule ---
    path('schedule/', views.doctor_schedule, name='doctor_schedule'),
    path('schedule/block/', views.block_doctor_time, name='block_doctor_time'),
    path('schedule/block/<int:exception_id>/delete/', views.delete_doctor_block, name='delete_doctor_block'),

    # --- Account ---
    path('profile/', views.settings_profile, name='settings_profile'),

    # --- Invitation Flow ---
    path('invites/', views.secretary_invitations_inbox, name='secretary_invitations_inbox'),
    path('invites/<int:invitation_id>/accept/', views.accept_invitation_view, name='accept_invitation'),
    path('invites/<int:invitation_id>/reject/', views.reject_invitation_view, name='reject_invitation'),
    path('invites/accept/<uuid:token>/', views.guest_accept_invitation_view, name='guest_accept_invitation'),

    # --- HTMX Endpoints ---
    path('htmx/doctor-status/', views.doctor_status_htmx, name='doctor_status_htmx'),
    path('htmx/time-slots/', views.get_time_slots_htmx, name='time_slots_htmx'),
    path('htmx/doctor-types/', views.get_doctor_types_htmx, name='doctor_types_htmx'),
    path('htmx/doctor-working-days/', views.doctor_working_days_json, name='doctor_working_days_json'),
]