from django.urls import path
from . import views, appointment_types_views

app_name = 'clinics'

urlpatterns = [
    path('', views.my_clinic, name='my_clinic'),  # For main doctors
    path('staff/', views.manage_staff, name='manage_staff'),
    path('staff/add/', views.add_staff, name='add_staff'),
    path('staff/<int:staff_id>/remove/', views.remove_staff, name='remove_staff'),
    # 4-step verification flow after clinic owner signup
    path('verify/owner-phone/', views.verify_owner_phone, name='verify_owner_phone'),
    path('verify/owner-email/', views.verify_owner_email, name='verify_owner_email'),
    path('verify/clinic-phone/', views.verify_clinic_phone, name='verify_clinic_phone'),
    path('verify/clinic-email/', views.verify_clinic_email, name='verify_clinic_email'),
    
    # Appointment Types Management
    path('appointment-types/', appointment_types_views.appointment_types_list, name='appointment_types_list'),
    path('appointment-types/create/', appointment_types_views.appointment_type_create, name='appointment_type_create'),
    path('appointment-types/<int:type_id>/edit/', appointment_types_views.appointment_type_update, name='appointment_type_update'),
    path('appointment-types/<int:type_id>/toggle/', appointment_types_views.appointment_type_toggle, name='appointment_type_toggle'),
]