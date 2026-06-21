from django.urls import path
from . import views

app_name = 'secretary'

urlpatterns = [
    # --- Dashboard ---
    path('', views.dashboard, name='dashboard'),

    # --- Appointment Management ---
    path('appointments/', views.appointments_list, name='appointments'),
    path('appointments/create/', views.create_appointment, name='create_appointment'),
    path('appointments/walk-in/', views.register_walk_in, name='register_walk_in'),
    path('appointments/<int:appointment_id>/edit/', views.edit_appointment, name='edit_appointment'),
    path('appointments/<int:appointment_id>/cancel/', views.cancel_appointment, name='cancel_appointment'),
    path('appointments/<int:appointment_id>/checkin/', views.checkin_appointment, name='checkin_appointment'),
    path('appointments/<int:appointment_id>/status/', views.update_appointment_status, name='update_appointment_status'),
    path('appointments/<int:appointment_id>/accept-new-patient/', views.accept_new_patient_request, name='accept_new_patient_request'),
    path('appointments/<int:appointment_id>/reject-new-patient/', views.reject_new_patient_request, name='reject_new_patient_request'),
    path('appointments/<int:appointment_id>/register-new-patient-only/', views.register_new_patient_only, name='register_new_patient_only'),
    path('appointments/<int:appointment_id>/overview/', views.appointment_overview, name='appointment_overview'),
    path('appointments/<int:appointment_id>/notes/add/', views.appointment_note_add, name='appointment_note_add'),
    path('appointments/<int:appointment_id>/notes/<int:note_id>/delete/', views.appointment_note_delete, name='appointment_note_delete'),
    path('appointments/<int:appointment_id>/intake/', views.appointment_intake_partial, name='appointment_intake_partial'),
    path('appointments/<int:appointment_id>/remove-from-queue/', views.remove_from_queue, name='remove_from_queue'),
    path('calendar/', views.calendar_view, name='calendar'),

    # --- Calendar JSON feed (FullCalendar) ---
    path('appointments.json', views.appointments_json, name='appointments_json'),

    # --- Waiting Room ---
    path('waiting-room/', views.waiting_room, name='waiting_room'),
    path('waiting-room/display/', views.waiting_room_display, name='waiting_room_display'),
    path('waiting-room/checkin/', views.checkin_search, name='checkin_search'),
    path('htmx/waiting-room-confirmed/', views.waiting_room_confirmed_htmx, name='waiting_room_confirmed_htmx'),
    path('htmx/waiting-room-checkedin/', views.waiting_room_checkedin_htmx, name='waiting_room_checkedin_htmx'),
    path('htmx/waiting-room-inprogress/', views.waiting_room_inprogress_htmx, name='waiting_room_inprogress_htmx'),
    path('htmx/reorder-queue/', views.reorder_queue, name='reorder_queue'),

    # --- Patient Management ---
    path('patients/', views.patient_list, name='patient_list'),
    path('patients/new/', views.create_new_patient, name='create_new_patient'),
    path('patients/<int:patient_id>/', views.patient_detail, name='patient_detail'),
    path('patients/<int:patient_id>/notes/add/', views.patient_note_add, name='patient_note_add'),
    path('patients/<int:patient_id>/notes/<int:note_id>/delete/', views.patient_note_delete, name='patient_note_delete'),
    path('patients/<int:patient_id>/edit/', views.edit_patient, name='edit_patient'),
    path('patients/<int:patient_id>/remove-block/', views.remove_patient_block, name='remove_patient_block'),
    path('patients/htmx/search/', views.patient_list_htmx, name='patient_list_htmx'),
    path('patients/search/', views.patient_search_htmx, name='patient_search'),
    path('patients/<int:patient_id>/card/', views.patient_detail_htmx, name='patient_card'),

    # --- Billing ---
    path('billing/', views.billing_invoices, name='billing_invoices'),
    path('billing/debts/', views.patient_debts, name='patient_debts'),
    path('billing/appointment/<int:appointment_id>/start/', views.start_billing, name='start_billing'),
    path('billing/invoice/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('billing/invoice/<int:invoice_id>/charges/add/', views.invoice_add_charge, name='invoice_add_charge'),
    path('billing/invoice/<int:invoice_id>/charges/<int:item_id>/delete/', views.invoice_remove_charge, name='invoice_remove_charge'),
    path('billing/invoice/<int:invoice_id>/payment/', views.invoice_record_payment, name='invoice_record_payment'),
    path('htmx/patient-debt/', views.patient_debt_badge_htmx, name='patient_debt_badge_htmx'),

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
    path('settings/clinic/', views.settings_clinic, name='settings_clinic'),

    # --- Invitation Flow ---
    path('invites/', views.secretary_invitations_inbox, name='secretary_invitations_inbox'),
    path('invites/<int:invitation_id>/accept/', views.accept_invitation_view, name='accept_invitation'),
    path('invites/<int:invitation_id>/reject/', views.reject_invitation_view, name='reject_invitation'),
    path('invites/accept/<uuid:token>/', views.guest_accept_invitation_view, name='guest_accept_invitation'),

    # --- HTMX Endpoints ---
    path('htmx/doctor-status/', views.doctor_status_htmx, name='doctor_status_htmx'),
    path('htmx/todays-appointments/', views.todays_appointments_htmx, name='todays_appointments_htmx'),
    path('htmx/time-slots/', views.get_time_slots_htmx, name='time_slots_htmx'),
    path('htmx/doctor-types/', views.get_doctor_types_htmx, name='doctor_types_htmx'),
    path('htmx/doctor-working-days/', views.doctor_working_days_json, name='doctor_working_days_json'),
    path('htmx/walkin-patient-appointments/', views.patient_walkin_appointments_htmx, name='walkin_patient_appointments_htmx'),
]