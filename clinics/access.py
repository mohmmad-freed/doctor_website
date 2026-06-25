"""
Shared authorization helpers for clinic-scoped resources.

Centralises the "active staff of this clinic" check that already gates the
doctor workspace (``doctors.views._ws_access``) and the secretary portal
(``@secretary_required`` → ``staff.clinic``) so the protected-media download
views reuse the exact same rule instead of inventing a new one.
"""
from clinics.models import ClinicStaff


def user_is_active_clinic_staff(user, clinic_id):
    """True if ``user`` holds a non-revoked staff membership at ``clinic_id``."""
    if not (user and user.is_authenticated):
        return False
    return ClinicStaff.objects.filter(
        user=user, clinic_id=clinic_id, revoked_at__isnull=True
    ).exists()


def user_can_access_clinic_file(user, clinic_id, patient_id):
    """
    Authorize access to a clinic-scoped patient file (medical record /
    appointment attachment).

    Allowed for the owning patient themselves, or any active staff member of
    the file's clinic (doctor / main doctor / secretary). Everyone else is
    denied.
    """
    if not (user and user.is_authenticated):
        return False
    if user.id == patient_id:
        return True
    return user_is_active_clinic_staff(user, clinic_id)
