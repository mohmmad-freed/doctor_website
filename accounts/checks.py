"""Deploy-time system checks for security feature flags.

Registered as a *deploy* check (like Django's own SECURE_* warnings), so it
runs on `manage.py check --deploy` — the deploy pipeline — but NOT during
`test` / `migrate` / `runserver` (the test runner forces DEBUG=False, which
would otherwise trip this everywhere). This lets a development `.env`, where
the brute-force / OTP defenses are deliberately relaxed, be caught before it
ships to production.
"""

from django.conf import settings
from django.core.checks import Error, register


@register("security", deploy=True)
def insecure_flags_in_production(app_configs, **kwargs):
    """Fail loud if a security flag is disabled while DEBUG is off (prod)."""
    errors = []

    # Only meaningful in production-like deploys.
    if settings.DEBUG:
        return errors

    if not getattr(settings, "ENFORCE_OTP_LIMITS", True):
        errors.append(
            Error(
                "ENFORCE_OTP_LIMITS is disabled while DEBUG=False.",
                hint="Set ENFORCE_OTP_LIMITS=1 in the production environment so "
                "OTP daily-resend limits are enforced.",
                id="accounts.E001",
            )
        )

    if not getattr(settings, "ENFORCE_PHONE_VERIFICATION", True):
        errors.append(
            Error(
                "ENFORCE_PHONE_VERIFICATION is disabled while DEBUG=False.",
                hint="Set ENFORCE_PHONE_VERIFICATION=1 in the production "
                "environment so unverified accounts cannot log in.",
                id="accounts.E002",
            )
        )

    return errors
