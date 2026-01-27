from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('', views.home_redirect, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    path('register/patient/', views.register_patient, name='register_patient'),
    path('register/main-doctor/', views.register_main_doctor, name='register_main_doctor'),
]