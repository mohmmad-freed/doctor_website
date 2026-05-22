import datetime as _dt
import re

from django import template
from django.utils.translation import get_language

from secretary.timefmt import format_clock

register = template.Library()

# Matches a bare 24-hour HH:MM token (not part of a longer number). Notification
# messages embed times this way; dates use "-" so they never match.
_HHMM_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


@register.filter(name="clock")
def clock(value, use_12h=False):
    """Render a time/datetime as 24h or 12h per the secretary's preference.

    Usage: ``{{ appt.appointment_time|clock:CLOCK_12H }}`` where ``CLOCK_12H``
    comes from the time_format context processor.
    """
    return format_clock(value, bool(use_12h), get_language() or "ar")


@register.filter(name="clock_text")
def clock_text(message, use_12h=False):
    """Reformat any embedded 24h HH:MM times in free text per the preference.

    Used for stored notification messages, whose time is baked in as 24h at
    creation. When the viewer prefers 12h, each time token becomes ``h:MM ص/م``
    or ``h:MM AM/PM``. No-op for 24h.
    """
    if not use_12h or not message:
        return message
    lang = get_language() or "ar"

    def _repl(m):
        return format_clock(_dt.time(int(m.group(1)), int(m.group(2))), True, lang)

    return _HHMM_RE.sub(_repl, str(message))
