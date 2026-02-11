import re
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.http import HttpResponseForbidden


# Patient-facing paths under /doctors/ that patients ARE allowed to access
PATIENT_ALLOWED_DOCTOR_PATHS = re.compile(
    r"^/doctors/\d+/(availability|appointment-types)/"
    r"|^/doctors/api/\d+/(availability|available-slots|appointment-types)/"
)


class ClinicIsolationMiddleware:
    """
    Middleware to enforce strict tenant isolation rules.
    1. Superusers: Bypass all checks.
    2. Patients: Blocked from staff paths (/doctors, /secretary, /clinics),
       EXCEPT patient-facing pages like doctor availability and appointment types.
    3. Staff: Must be associated with a Clinic. sets request.clinic and request.clinic_id.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Default: No clinic context
        request.clinic = None
        request.clinic_id = None

        if not request.user.is_authenticated:
            return self.get_response(request)

        # 1. Superuser Bypass
        if request.user.is_superuser:
            return self.get_response(request)

        user = request.user
        path = request.path

        # 2. Patient Logic
        role = getattr(user, "role", None)
        if role == "PATIENT":
            # Allow patient-facing doctor pages
            if PATIENT_ALLOWED_DOCTOR_PATHS.match(path):
                return self.get_response(request)

            # Block access to staff areas
            if any(
                path.startswith(prefix)
                for prefix in ["/doctors/", "/secretary/", "/clinics/"]
            ):
                return HttpResponseForbidden(
                    "Patients are not authorized to view this area."
                )

            # Patients are global, no clinic scope needed on request
            return self.get_response(request)

        # 3. Staff Logic (Main Doctor, Doctor, Secretary)
        if role in ["MAIN_DOCTOR", "DOCTOR", "SECRETARY"]:

            # Exemptions for staff (e.g. logout) to prevent lockouts
            if (
                path == "/accounts/logout/"
                or path.startswith("/admin/")
                or path.startswith("/static/")
                or path.startswith("/media/")
            ):
                return self.get_response(request)

            clinic = None

            try:
                if role == "MAIN_DOCTOR":
                    clinic = user.owned_clinic.first()

                elif role in ["DOCTOR", "SECRETARY"]:
                    from clinics.models import ClinicStaff

                    staff_entry = (
                        ClinicStaff.objects.filter(user=user, is_active=True)
                        .select_related("clinic")
                        .first()
                    )
                    if staff_entry:
                        clinic = staff_entry.clinic

            except Exception:
                pass

            if not clinic:
                return HttpResponseForbidden(
                    "Access Denied: You are not assigned to any active clinic."
                )

            # Attach clinic to request for use in Views
            request.clinic = clinic
            request.clinic_id = clinic.id

        return self.get_response(request)