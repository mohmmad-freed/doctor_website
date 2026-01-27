from django.urls import path
from . import views

app_name = 'clinics'

urlpatterns = [
    path('', views.my_clinic, name='my_clinic'),  # For main doctors
    path('staff/', views.manage_staff, name='manage_staff'),
    path('staff/add/', views.add_staff, name='add_staff'),
    path('staff/<int:staff_id>/remove/', views.remove_staff, name='remove_staff'),
]