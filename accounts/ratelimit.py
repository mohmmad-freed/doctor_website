"""
Lightweight, cache-backed failure throttle for authentication paths.

Defense-in-depth only: this caps how many *failed* attempts a given identity
(a phone for login, a user id for password changes) may make inside a fixed
window. It is NOT the primary auth control — the password is.

Design notes:
- Fixed window: the TTL is set on the first failure and is not extended by
  later failures, so a blocked identity is automatically released when the
  window elapses (no permanent lockout).
- Fail-open: every cache operation is wrapped so a Redis outage can never
  lock users out or 500 the login page. If the cache is unavailable we simply
  stop throttling until it recovers (and log it).
- Keyed by phone (login) / user id (password change). Throttling per-account
  means someone who knows a victim's phone could trip a temporary block on
  them; the window is short and only failed attempts count, which is the
  standard, time-bounded trade-off for brute-force protection.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

PW_CHANGE_MAX_ATTEMPTS = 5
PW_CHANGE_WINDOW_SECONDS = 15 * 60


def _key(scope, ident):
    return f"throttle:{scope}:{ident}"


def is_blocked(scope, ident, limit):
    """True if this identity has already reached ``limit`` failures in the window."""
    try:
        return (cache.get(_key(scope, ident)) or 0) >= limit
    except Exception:  # cache down → don't block anyone
        logger.warning("[throttle] cache read failed for %s:%s — failing open", scope, ident)
        return False


def register_failure(scope, ident, window_seconds):
    """Count one failed attempt. Returns the new running total (0 if cache is down)."""
    key = _key(scope, ident)
    try:
        # add() only sets the key (and the window TTL) if it doesn't exist yet.
        if cache.add(key, 1, timeout=window_seconds):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            # Key expired between add() and incr() — restart the window.
            cache.set(key, 1, timeout=window_seconds)
            return 1
    except Exception:
        logger.warning("[throttle] cache write failed for %s:%s — failing open", scope, ident)
        return 0


def clear_failures(scope, ident):
    """Reset the counter after a successful attempt."""
    try:
        cache.delete(_key(scope, ident))
    except Exception:
        pass
