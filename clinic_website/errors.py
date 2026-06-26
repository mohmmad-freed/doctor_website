"""
Custom error handlers for production (DEBUG=False).

Django's built-in error views render bare, unbranded pages. More importantly,
with DEBUG=True an unhandled exception renders the technical 500 page, which
dumps the full traceback AND the settings (SECRET_KEY, DB credentials, API
keys). The deploy contract is DEBUG=False in production; these handlers then
render branded, bilingual (AR/EN) pages instead of the defaults.

Context-safety note for handler500: it renders WITHOUT a request, so the
template context processors configured in settings.py (clinic_switcher,
unread_notifications, doctor_context, …) — any of which can hit the DB and raise
again while we are already handling a server error — do NOT run. The error
templates are also standalone (they don't extend accounts/base.html) for the
same reason.
"""

from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseServerError,
)
from django.template.loader import render_to_string


def _lang(request):
    """Page language for the error template ("en" | "ar"), defaulting to ar."""
    return "en" if getattr(request, "LANGUAGE_CODE", "ar") == "en" else "ar"


def handler400(request, exception=None):
    return HttpResponseBadRequest(render_to_string("400.html", {"lang": _lang(request)}))


def handler403(request, exception=None):
    return HttpResponseForbidden(render_to_string("403.html", {"lang": _lang(request)}))


def handler404(request, exception=None):
    return HttpResponseNotFound(render_to_string("404.html", {"lang": _lang(request)}))


def handler500(request):
    # No `request=` passed to render_to_string → context processors don't run, so
    # a DB-dependent processor can't raise a second exception mid-500.
    return HttpResponseServerError(render_to_string("500.html", {"lang": _lang(request)}))
