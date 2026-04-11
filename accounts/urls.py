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
    path(
        "register/patient/phone/",
        views.register_patient_phone,
        name="register_patient_phone",
    ),
    path(
        "register/patient/verify/",
        views.register_patient_verify,
        name="register_patient_verify",
    ),
    path(
        "register/patient/details/",
        views.register_patient_details,
        name="register_patient_details",
    ),
    path(
        "register/patient/email/",
        views.register_patient_email,
        name="register_patient_email",
    ),
    # Email verification
    path(
        "send-email-verification/",
        views.send_email_verification,
        name="send_email_verification",
    ),
    path("verify-email/<str:token>/", views.verify_email, name="verify_email"),
    # Clinic owner registration (3-stage wizard)
    path(
        "register/clinic/step-1/",
        views.register_clinic_step1,
        name="register_clinic_step1",
    ),
    path(
        "register/clinic/step-2/",
        views.register_clinic_step2,
        name="register_clinic_step2",
    ),
    path(
        "register/clinic/step-3/",
        views.register_clinic_step3,
        name="register_clinic_step3",
    ),
    path(
        "register/clinic/verify-phone/",
        views.register_clinic_verify_phone,
        name="register_clinic_verify_phone",
    ),
    path(
        "register/clinic/verify-email/",
        views.register_clinic_verify_email,
        name="register_clinic_verify_email",
    ),
    # Legacy single-page registration view (kept for backwards compat with existing tests).
    # New user-facing traffic uses the 3-stage wizard (register_clinic_step1 above).
    path(
        "register/main-doctor/",
        views.register_main_doctor,
        name="register_main_doctor",
    ),
    # Change Phone Number
    path(
        "profile/change-phone/", views.change_phone_request, name="change_phone_request"
    ),
    path(
        "profile/change-phone/verify/",
        views.change_phone_verify,
        name="change_phone_verify",
    ),
    # Change Email (link-based, legacy)
    path(
        "profile/change-email/", views.change_email_request, name="change_email_request"
    ),
    path(
        "profile/change-email/verify/<str:token>/",
        views.verify_change_email,
        name="verify_change_email",
    ),
    # Change Email (OTP-based)
    path(
        "profile/change-email/otp/",
        views.change_email_otp_request,
        name="change_email_otp_request",
    ),
    path(
        "profile/change-email/otp/verify/",
        views.change_email_otp_verify,
        name="change_email_otp_verify",
    ),
    # Forgot Password
    path(
        "forgot-password/",
        views.forgot_password_phone,
        name="forgot_password_phone",
    ),
    path(
        "forgot-password/verify/",
        views.forgot_password_verify,
        name="forgot_password_verify",
    ),
    path(
        "forgot-password/reset/",
        views.forgot_password_reset,
        name="forgot_password_reset",
    ),
    # Language preference
    path("set-language/", views.set_language_preference, name="set_language"),
    # API Endpoints
    path("api/login/", api_views.MyTokenObtainPairView.as_view(), name="api_login"),
    path("api/logout/", api_views.LogoutAPIView.as_view(), name="api_logout"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
