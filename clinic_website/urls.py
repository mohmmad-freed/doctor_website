"""
URL configuration for clinic_website project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path
from patients.views import PatientProfileAPIView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("patients/", include("patients.urls")),
    path("doctors/", include("doctors.urls")),
    path("secretary/", include("secretary.urls")),
    path("clinics/", include("clinics.urls")),
    path("appointments/", include("appointments.urls")),
    # API key modules
    path(
        "api/patient/profile/",
        PatientProfileAPIView.as_view(),
        name="patient_profile_api",
    ),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
