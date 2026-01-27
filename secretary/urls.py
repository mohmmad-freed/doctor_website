from django.urls import path
from . import views

app_name = 'secretary'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('appointments/', views.appointments_list, name='appointments'),
    path('appointments/create/', views.create_appointment, name='create_appointment'),
    path('appointments/<int:appointment_id>/edit/', views.edit_appointment, name='edit_appointment'),
]