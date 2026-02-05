from django.urls import path
from . import views, api_views
from rest_framework_simplejwt.views import TokenRefreshView

app_name = "accounts"

urlpatterns = [
    path("", views.landing_page, name="landing"),
    path("dashboard/", views.home_redirect, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    
    # Patient registration (3 steps)
    path("register/patient/phone/", views.register_patient_phone, name="register_patient_phone"),
    path("register/patient/verify/", views.register_patient_verify, name="register_patient_verify"),
    path("register/patient/details/", views.register_patient_details, name="register_patient_details"),
    
    # Email verification
    path("send-email-verification/", views.send_email_verification, name="send_email_verification"),
    path("verify-email/<str:token>/", views.verify_email, name="verify_email"),
    
    # Main doctor registration
    path("register/main-doctor/", views.register_main_doctor, name="register_main_doctor"),
    
    # API Endpoints
    path("api/login/", api_views.MyTokenObtainPairView.as_view(), name="api_login"),
    path("api/logout/", api_views.LogoutAPIView.as_view(), name="api_logout"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]