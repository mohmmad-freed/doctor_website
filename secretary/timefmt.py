"""Shared time-of-day formatting honoring the secretary's 24h/12h preference.

Used by the ``clock`` template filter, by views that emit time strings into
CSV/JSON, so all three layers format identically.
"""
import datetime as _dt

from django.utils import timezone


def format_clock(value, use_12h, lang="ar"):
    """Format a time/datetime as ``HH:MM`` (24h) or ``h:MM ص/م`` / ``h:MM AM/PM`` (12h).

    Aware datetimes (e.g. checked_in_at, created_at) are localized first, the
    same way Django's ``|time`` filter does, so the hour is correct in the
    project timezone rather than UTC.
    """
    if value is None:
        return ""
    if isinstance(value, _dt.datetime) and timezone.is_aware(value):
        value = timezone.localtime(value)
    if not use_12h:
        return value.strftime("%H:%M")
    hour = value.hour % 12 or 12
    if str(lang).startswith("ar"):
        marker = "ص" if value.hour < 12 else "م"
    else:
        marker = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {marker}"
