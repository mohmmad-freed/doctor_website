from rest_framework import permissions


class IsPatient(permissions.BasePermission):
    """
    Allows access only to authenticated users with the 'PATIENT' role.
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "PATIENT"
        )
