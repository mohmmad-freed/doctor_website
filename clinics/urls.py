from django.urls import path
from . import views

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
]