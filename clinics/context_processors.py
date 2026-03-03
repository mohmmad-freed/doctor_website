from .models import Clinic


def clinic_switcher(request):
    """
    Exposes owned_clinics and selected_clinic to all templates.
    Only runs for authenticated MAIN_DOCTOR users.
    """
    if not request.user.is_authenticated:
        return {}
    if getattr(request.user, "role", None) != "MAIN_DOCTOR":
        return {}

    clinics = list(
        Clinic.objects.filter(main_doctor=request.user, is_active=True).only("id", "name")
    )

    # Determine the currently active clinic: URL kwargs → session → None
    current_id = None
    if hasattr(request, "resolver_match") and request.resolver_match:
        current_id = request.resolver_match.kwargs.get("clinic_id")
    if current_id is None:
        current_id = request.session.get("selected_clinic_id")

    selected_clinic = next((c for c in clinics if c.id == current_id), None)

    return {
        "owned_clinics": clinics,
        "selected_clinic": selected_clinic,
    }
