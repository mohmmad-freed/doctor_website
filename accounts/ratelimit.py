"""
Lightweight, cache-backed failure throttle for authentication paths.

Defense-in-depth only: this caps how many *failed* attempts a given identity
(a phone for login, a user id for password changes, a client IP for the
per-IP layer) may make inside a window. It is NOT the primary auth control —
the password is.

Design notes:
- Two layers per login attempt: per-account (phone) and per-IP. The account
  layer stops someone hammering one victim; the IP layer stops password-spray
  across many accounts from one source and stops a single victim's account
  from being the only thing an attacker can lock.
- Escalating lockout: the first time an identity trips the limit it is blocked
  for a short window; repeated breaches escalate the block duration via a
  persistent "strikes" counter (LOGIN_LOCKOUT_LADDER). Strikes decay after a
  day of good behaviour, and every block auto-releases when its TTL elapses —
  there is no permanent lockout.
- Fail-open: every cache operation is wrapped so a Redis outage can never lock
  users out or 500 the login page. If the cache is unavailable we simply stop
  throttling until it recovers (and log it).
- Keyed by phone (login) / user id (password change) / IP. Per-account keying
  means someone who knows a victim's phone could trip a temporary block on
  them; that is the standard, time-bounded trade-off for brute-force
  protection, and the IP layer means the account block is not the only control.
"""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# ── Tunables (overridable via settings / env; see clinic_website/settings.py) ─
LOGIN_MAX_ATTEMPTS = getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
LOGIN_WINDOW_SECONDS = getattr(settings, "LOGIN_WINDOW_SECONDS", 15 * 60)

# Per-IP layer — higher cap, since one IP can legitimately be many users (NAT).
LOGIN_IP_MAX_ATTEMPTS = getattr(settings, "LOGIN_IP_MAX_ATTEMPTS", 20)
LOGIN_IP_WINDOW_SECONDS = getattr(settings, "LOGIN_IP_WINDOW_SECONDS", 15 * 60)

PW_CHANGE_MAX_ATTEMPTS = getattr(settings, "PW_CHANGE_MAX_ATTEMPTS", 5)
PW_CHANGE_WINDOW_SECONDS = getattr(settings, "PW_CHANGE_WINDOW_SECONDS", 15 * 60)

# Escalating block durations applied once an identity trips the limit. The Nth
# breach uses LADDER[min(N - 1, len - 1)] seconds, so repeat offenders are held
# progressively longer (15 min → 1 h → 24 h by default).
LOGIN_LOCKOUT_LADDER = getattr(
    settings, "LOGIN_LOCKOUT_LADDER", (15 * 60, 60 * 60, 24 * 60 * 60)
)
# How long a "strike" (escalation level) is remembered before it decays.
STRIKES_TTL_SECONDS = getattr(settings, "LOGIN_STRIKES_TTL_SECONDS", 24 * 60 * 60)


def _key(scope, ident):
    """Rolling failure counter (kept for backward compatibility of key names)."""
    return f"throttle:{scope}:{ident}"


def _block_key(scope, ident):
    """Explicit, escalation-aware block marker."""
    return f"throttle:block:{scope}:{ident}"


def _strikes_key(scope, ident):
    """Persistent count of how many times this identity has tripped the limit."""
    return f"throttle:strikes:{scope}:{ident}"


def client_ip(request):
    """Best-effort client IP.

    In production the app sits behind a trusted reverse proxy (see
    SECURE_PROXY_SSL_HEADER), so the left-most X-Forwarded-For entry is the
    real client. In local dev XFF is absent and we fall back to REMOTE_ADDR.
    Only trust XFF because deployment is proxy-fronted; if the app were ever
    exposed directly, clients could spoof this header.
    """
    if request is None:
        return "unknown"
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _block_seconds_for_strikes(strikes):
    """Map a 1-based strike count to a block duration from the ladder."""
    ladder = LOGIN_LOCKOUT_LADDER or (LOGIN_WINDOW_SECONDS,)
    idx = min(max(strikes, 1) - 1, len(ladder) - 1)
    return ladder[idx]


def is_blocked(scope, ident, limit):
    """True if this identity is currently blocked.

    Blocked either because an escalated block marker is active, or because the
    rolling failure counter has already reached ``limit`` in its window.
    """
    try:
        if cache.get(_block_key(scope, ident)):
            return True
        return (cache.get(_key(scope, ident)) or 0) >= limit
    except Exception:  # cache down → don't block anyone
        logger.warning("[throttle] cache read failed for %s:%s — failing open", scope, ident)
        return False


def register_failure(scope, ident, window_seconds, limit=None):
    """Count one failed attempt. Returns the new running total (0 if cache is down).

    When ``limit`` is supplied and this failure is the one that reaches it, an
    escalating block is applied: the strike counter is bumped and a block
    marker is set whose TTL grows with the number of strikes. Callers that do
    not pass ``limit`` keep the original fixed-window behaviour.
    """
    key = _key(scope, ident)
    try:
        # add() only sets the key (and the window TTL) if it doesn't exist yet.
        if cache.add(key, 1, timeout=window_seconds):
            total = 1
        else:
            try:
                total = cache.incr(key)
            except ValueError:
                # Key expired between add() and incr() — restart the window.
                cache.set(key, 1, timeout=window_seconds)
                total = 1

        # Escalate exactly on the failure that crosses the threshold.
        if limit is not None and total == limit:
            _escalate_block(scope, ident)

        return total
    except Exception:
        logger.warning("[throttle] cache write failed for %s:%s — failing open", scope, ident)
        return 0


def _escalate_block(scope, ident):
    """Bump the strike counter and (re)arm the block marker with a longer TTL."""
    skey = _strikes_key(scope, ident)
    try:
        if cache.add(skey, 1, timeout=STRIKES_TTL_SECONDS):
            strikes = 1
        else:
            try:
                strikes = cache.incr(skey)
            except ValueError:
                cache.set(skey, 1, timeout=STRIKES_TTL_SECONDS)
                strikes = 1
            # Refresh the decay window so escalation level persists while abused.
            cache.touch(skey, STRIKES_TTL_SECONDS)

        block_seconds = _block_seconds_for_strikes(strikes)
        cache.set(_block_key(scope, ident), 1, timeout=block_seconds)
        logger.warning(
            "[throttle] %s:%s blocked for %ss (strike %s)",
            scope, ident, block_seconds, strikes,
        )
    except Exception:
        logger.warning("[throttle] escalation failed for %s:%s — failing open", scope, ident)


def clear_failures(scope, ident):
    """Reset all counters after a successful attempt."""
    try:
        cache.delete_many([
            _key(scope, ident),
            _block_key(scope, ident),
            _strikes_key(scope, ident),
        ])
    except Exception:
        pass
