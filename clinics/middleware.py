import re
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.http import HttpResponseForbidden


class ClinicIsolationMiddleware:
    """
    Middleware to enforce strict tenant isolation rules.
    1. Superusers: Bypass all checks.
    2. Patients: Blocked from staff paths (/doctors, /secretary, /clinics).
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
            # Superusers see everything, no specific clinic scope forced unless we add a switcher later
            return self.get_response(request)

        user = request.user
        path = request.path

        # 2. Patient Logic
        role = getattr(user, "role", None)
        if role == "PATIENT":
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
                    # Accessed via reverse relation from Clinic model
                    clinic = user.owned_clinic.first()

                elif role in ["DOCTOR", "SECRETARY"]:
                    # Accessed via ClinicStaff model
                    # We use .first() assuming a user is currently active in one clinic
                    # (Architecture says M2M is possible for doctors but simplicity first per instructions)
                    # Ideally we might need a session switcher if they belong to multiple,
                    # but for now we pick the first one to enforce isolation.
                    from clinics.models import ClinicStaff

                    staff_entry = (
                        ClinicStaff.objects.filter(user=user, is_active=True)
                        .select_related("clinic")
                        .first()
                    )
                    if staff_entry:
                        clinic = staff_entry.clinic

            except Exception:
                # Fallthrough to 403 if DB error
                pass

            if not clinic:
                # Staff member without a clinic cannot function in this system
                return HttpResponseForbidden(
                    "Access Denied: You are not assigned to any active clinic."
                )

            # Attach clinic to request for use in Views
            request.clinic = clinic
            request.clinic_id = clinic.id

        return self.get_response(request)
