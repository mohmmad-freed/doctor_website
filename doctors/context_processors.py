"""
Doctor-specific context processor.

Injects `pending_invitations_count` for doctor/main_doctor users so the
navigation badge stays accurate across all doctor pages without requiring
each view to query it individually.
"""


def doctor_context(request):
    if not request.user.is_authenticated:
        return {}

    roles = request.user.roles or []
    if "DOCTOR" not in roles and "MAIN_DOCTOR" not in roles:
        return {}

    try:
        from clinics.models import ClinicInvitation
        from accounts.backends import PhoneNumberAuthBackend

        normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(
            request.user.phone
        )
        count = ClinicInvitation.objects.filter(
            doctor_phone=normalized_phone, status="PENDING"
        ).count()
        return {"pending_invitations_count": count}
    except Exception:
        return {"pending_invitations_count": 0}
