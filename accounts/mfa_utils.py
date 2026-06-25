"""
Two-factor (MFA) helpers for staff logins.

Primary factor stays phone + password (PhoneNumberAuthBackend). This module adds
an opt-in *second* factor for staff:

  - TOTP (RFC 6238) as the primary 2nd factor (authenticator app). The shared
    secret is encrypted at rest with Fernet — never stored in plaintext.
  - One-time backup codes (hashed) for the lost-device case.
  - A signed "remember this device" cookie that lets a known browser skip the
    challenge, revocable per-user by rotating ``mfa_device_salt``.

The phone-OTP fallback at the challenge step reuses accounts/otp_utils.py
(request_otp / verify_otp) and is wired in the view, not here.

Design notes:
  - Secret-at-rest key: ``settings.MFA_SECRET_KEY`` (any random string; a Fernet
    key is derived from it via SHA-256). In DEBUG it falls back to SECRET_KEY so
    local dev works without extra config. In production MFA_SECRET_KEY must be
    set and kept STABLE — changing it makes every stored TOTP secret
    undecryptable and forces re-enrollment.
  - Backup codes are high-entropy random, so an unsalted SHA-256 is sufficient
    and lookups are always user-scoped.
"""

import base64
import hashlib
import io
import logging
import secrets

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core import signing
from django.utils import timezone

logger = logging.getLogger(__name__)

ISSUER_NAME = "Clinic"
TOTP_VALID_WINDOW = 1  # accept the adjacent step each side (±30s) for clock drift
_TRUSTED_DEVICE_SIGNING_SALT = "accounts.mfa.trusted-device"

# Backup codes: base32 (Crockford-ish, no padding) in groups for readability.
_BACKUP_CODE_BYTES = 5  # 5 bytes -> 8 base32 chars (~40 bits) per code


# ── Fernet key / secret encryption ──────────────────────────────────────────
_fernet_cache = None


def _key_material():
    material = getattr(settings, "MFA_SECRET_KEY", "") or ""
    if not material:
        if getattr(settings, "DEBUG", False):
            material = settings.SECRET_KEY or ""
        if not material:
            raise ImproperlyConfigured(
                "MFA_SECRET_KEY is not set. Set it (any random string) to enable "
                "encryption of stored TOTP secrets."
            )
    return material


def _fernet():
    """Return a cached Fernet built from a SHA-256 digest of the key material.

    Deriving the 32-byte Fernet key from a hash lets operators use any random
    string for MFA_SECRET_KEY instead of a pre-formatted Fernet key.
    """
    global _fernet_cache
    if _fernet_cache is None:
        digest = hashlib.sha256(_key_material().encode("utf-8")).digest()
        _fernet_cache = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet_cache


def encrypt_secret(secret: str) -> str:
    """Encrypt a TOTP secret for storage. Returns a urlsafe token string."""
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """Decrypt a stored TOTP secret. Returns None if it can't be decrypted."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("[MFA] failed to decrypt a stored TOTP secret")
        return None


# ── TOTP ─────────────────────────────────────────────────────────────────────
def generate_totp_secret() -> str:
    """Generate a fresh random base32 TOTP secret (plaintext, not yet stored)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_label: str) -> str:
    """otpauth:// URI for QR provisioning into an authenticator app."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_label, issuer_name=ISSUER_NAME
    )


def verify_totp(secret: str, code: str) -> bool:
    """True if *code* is valid for *secret* (within the drift window)."""
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    if not code.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW)
    except Exception:  # malformed secret, etc.
        logger.warning("[MFA] TOTP verify raised", exc_info=True)
        return False


def qr_data_uri(provisioning_uri_str: str) -> str:
    """Render a provisioning URI to a base64 PNG data URI for an <img> tag."""
    img = qrcode.make(provisioning_uri_str)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ── Enrollment lifecycle ─────────────────────────────────────────────────────
def enable_mfa(user, secret: str):
    """Persist the (encrypted) secret and mark MFA enabled. Caller saves nothing else."""
    user.mfa_totp_secret = encrypt_secret(secret)
    user.mfa_enabled = True
    user.mfa_enrolled_at = timezone.now()
    ensure_device_salt(user, save=False)
    user.save(
        update_fields=[
            "mfa_totp_secret",
            "mfa_enabled",
            "mfa_enrolled_at",
            "mfa_device_salt",
        ]
    )


def disable_mfa(user):
    """Turn MFA off: clear the secret, drop backup codes, revoke trusted devices."""
    user.mfa_enabled = False
    user.mfa_totp_secret = ""
    user.mfa_enrolled_at = None
    user.mfa_device_salt = _new_salt()  # rotate -> invalidates trusted-device cookies
    user.save(
        update_fields=[
            "mfa_enabled",
            "mfa_totp_secret",
            "mfa_enrolled_at",
            "mfa_device_salt",
        ]
    )
    user.mfa_backup_codes.all().delete()


def user_totp_secret(user) -> str | None:
    """Decrypted TOTP secret for a user, or None."""
    return decrypt_secret(user.mfa_totp_secret)


# ── Backup codes ─────────────────────────────────────────────────────────────
def _normalize_backup_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _format_backup_code(raw: str) -> str:
    """Display form, grouped for readability (e.g. ABCD-EFGH)."""
    return f"{raw[:4]}-{raw[4:]}" if len(raw) > 4 else raw


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(_normalize_backup_code(code).encode("utf-8")).hexdigest()


def generate_backup_codes(user, count: int | None = None) -> list[str]:
    """(Re)generate one-time backup codes for *user*.

    Deletes any existing codes, stores the new ones hashed, and returns the
    plaintext list to show the user ONCE. They are unrecoverable afterwards.
    """
    from .models import StaffMfaBackupCode

    if count is None:
        count = getattr(settings, "MFA_BACKUP_CODE_COUNT", 10)

    user.mfa_backup_codes.all().delete()
    plaintext = []
    rows = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(_BACKUP_CODE_BYTES)).decode("ascii").rstrip("=")
        plaintext.append(_format_backup_code(raw))
        rows.append(StaffMfaBackupCode(user=user, code_hash=hash_backup_code(raw)))
    StaffMfaBackupCode.objects.bulk_create(rows)
    return plaintext


def verify_and_consume_backup_code(user, code: str) -> bool:
    """Consume a matching unused backup code. True if one was found & marked used."""
    if not code:
        return False
    match = user.mfa_backup_codes.filter(
        code_hash=hash_backup_code(code), used_at__isnull=True
    ).first()
    if not match:
        return False
    match.used_at = timezone.now()
    match.save(update_fields=["used_at"])
    return True


def unused_backup_code_count(user) -> int:
    return user.mfa_backup_codes.filter(used_at__isnull=True).count()


# ── Trusted-device cookie ────────────────────────────────────────────────────
def _new_salt() -> str:
    return secrets.token_hex(16)


def ensure_device_salt(user, save: bool = True) -> str:
    """Guarantee the user has a device salt; create one if missing."""
    if not user.mfa_device_salt:
        user.mfa_device_salt = _new_salt()
        if save:
            user.save(update_fields=["mfa_device_salt"])
    return user.mfa_device_salt


def make_trusted_device_token(user) -> str:
    """Signed token binding a browser to this user + current device salt."""
    return signing.dumps(
        {"uid": user.pk, "salt": ensure_device_salt(user)},
        salt=_TRUSTED_DEVICE_SIGNING_SALT,
    )


def is_trusted_device(request, user) -> bool:
    """True if the request carries a valid, unexpired trusted-device cookie."""
    cookie_name = getattr(settings, "MFA_TRUSTED_DEVICE_COOKIE", "mfa_device")
    raw = request.COOKIES.get(cookie_name)
    if not raw or not user.mfa_device_salt:
        return False
    try:
        data = signing.loads(
            raw,
            salt=_TRUSTED_DEVICE_SIGNING_SALT,
            max_age=getattr(settings, "MFA_TRUSTED_DEVICE_MAX_AGE", 30 * 24 * 60 * 60),
        )
    except signing.BadSignature:
        return False
    return data.get("uid") == user.pk and data.get("salt") == user.mfa_device_salt


def set_trusted_device_cookie(response, user):
    """Attach a trusted-device cookie to *response* (HttpOnly, SameSite=Lax)."""
    response.set_cookie(
        getattr(settings, "MFA_TRUSTED_DEVICE_COOKIE", "mfa_device"),
        make_trusted_device_token(user),
        max_age=getattr(settings, "MFA_TRUSTED_DEVICE_MAX_AGE", 30 * 24 * 60 * 60),
        httponly=True,
        secure=not getattr(settings, "DEBUG", False),
        samesite="Lax",
    )
    return response


def revoke_trusted_devices(user):
    """Rotate the device salt so every existing trusted-device cookie is voided."""
    user.mfa_device_salt = _new_salt()
    user.save(update_fields=["mfa_device_salt"])
