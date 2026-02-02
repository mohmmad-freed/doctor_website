from django.urls import path
from . import views, api_views
from rest_framework_simplejwt.views import TokenRefreshView

app_name = "accounts"

urlpatterns = [
    path("", views.home_redirect, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("register/patient/", views.register_patient, name="register_patient"),
    path(
        "register/main-doctor/", views.register_main_doctor, name="register_main_doctor"
    ),
    # API Endpoints
    path("api/login/", api_views.MyTokenObtainPairView.as_view(), name="api_login"),
    path("api/logout/", api_views.LogoutAPIView.as_view(), name="api_logout"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
