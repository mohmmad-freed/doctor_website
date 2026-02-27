from patients.models import PatientProfile


def ensure_patient_profile(user):
    """
    Get-or-create a PatientProfile for *user*.

    Safe to call multiple times — if a profile already exists it is returned
    unchanged.  The DB-level unique constraint on PatientProfile.user (OneToOneField)
    guarantees no duplicates even under concurrent calls.

    Returns:
        (profile, created) — created is True only when a new row was inserted.
    """
    return PatientProfile.objects.get_or_create(user=user)
