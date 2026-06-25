"""Audit-trail emit helper for the clinic activity log.

A single choke-point so every call site is one line and the trusted-proxy
client-IP logic lives in exactly one place (reused from accounts.ratelimit).
"""

from accounts.ratelimit import client_ip
from clinics.models import ActivityLog


def log_activity(
    *,
    actor,
    clinic,
    action,
    target=None,
    target_type="",
    target_id=None,
    request=None,
    ip=None,
    metadata=None,
):
    """Write one :class:`~clinics.models.ActivityLog` row.

    ``target`` is a convenience: pass a model instance and its class name + pk
    are derived automatically. When ``request`` is given the client IP is
    resolved via :func:`accounts.ratelimit.client_ip`; service-layer callers
    with no request in scope may pass ``ip=`` directly instead.

    Emitted inside the caller's transaction where one is open, so the trail
    can never desync from the write it records.
    """
    if target is not None:
        target_type = target_type or target.__class__.__name__
        if target_id is None:
            target_id = getattr(target, "pk", None)

    resolved_ip = ip if ip is not None else (client_ip(request) if request is not None else None)

    return ActivityLog.objects.create(
        actor=actor,
        clinic=clinic,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=resolved_ip,
        metadata=metadata or {},
    )
