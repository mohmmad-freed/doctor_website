"""Reusable validators for account fields."""

import re

from django.core.exceptions import ValidationError

# Characters that never legitimately appear in a person's name. Rejecting angle
# brackets and control characters keeps HTML/script payloads out of
# CustomUser.name — a defense-in-depth layer behind output escaping, for staff
# portals that re-highlight or otherwise re-render names client-side.
_NAME_DISALLOWED_RE = re.compile(r"[<>\x00-\x1f\x7f]")

# Default message (Arabic) — call sites may override to match their language.
NAME_DISALLOWED_MESSAGE = "الاسم يحتوي على رموز غير مسموحة."


def name_has_disallowed_chars(value):
    """Return True if ``value`` contains angle brackets or control characters."""
    return bool(_NAME_DISALLOWED_RE.search(value or ""))


def validate_human_name(value, message=None):
    """Raise ``ValidationError`` if the name contains disallowed characters."""
    if name_has_disallowed_chars(value):
        raise ValidationError(message or NAME_DISALLOWED_MESSAGE)
