from django.urls import path
from . import views

app_name = 'doctors'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('appointments/', views.appointments_list, name='appointments'),
    path('appointments/<int:appointment_id>/', views.appointment_detail, name='appointment_detail'),
    path('patients/', views.patients_list, name='patients'),
]