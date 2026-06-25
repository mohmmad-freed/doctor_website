"""
Tests for staff MFA (two-factor) — accounts/mfa_utils.py + the login gate and
the enrollment/challenge views.

Covers:
  - mfa_utils core: secret encrypt/decrypt, TOTP verify, backup codes, enable/
    disable lifecycle, trusted-device token.
  - Login gate: MFA-disabled users are unaffected; MFA-enabled staff are
    deferred to the challenge step and only logged in after a valid 2nd factor.
  - Challenge: TOTP / backup-code / SMS-fallback paths, throttling, remember-
    device cookie, and pending-state expiry.
  - Enrollment: staff-only setup, confirm-before-enable, disable re-auth.
"""

from unittest.mock import patch

import pyotp
from django.test import TestCase, Client, RequestFactory, override_settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.urls import reverse

from django.conf import settings

from clinics.models import Clinic, ClinicStaff
from accounts import mfa_utils

User = get_user_model()

TEST_KEY = "unit-test-mfa-key"


@override_settings(MFA_SECRET_KEY=TEST_KEY)
class MfaUtilsTests(TestCase):
    def setUp(self):
        mfa_utils._fernet_cache = None  # don't leak a Fernet built with another key
        self.user = User.objects.create_user(
            phone="0591300001", password="pass1234", name="Staff",
            role="SECRETARY", roles=["SECRETARY"], is_verified=True,
        )

    def test_secret_encrypt_decrypt_roundtrip(self):
        secret = mfa_utils.generate_totp_secret()
        token = mfa_utils.encrypt_secret(secret)
        self.assertNotEqual(token, secret)               # not plaintext
        self.assertEqual(mfa_utils.decrypt_secret(token), secret)

    def test_decrypt_garbage_returns_none(self):
        self.assertIsNone(mfa_utils.decrypt_secret("not-a-valid-token"))

    def test_verify_totp_accepts_current_rejects_wrong(self):
        secret = mfa_utils.generate_totp_secret()
        self.assertTrue(mfa_utils.verify_totp(secret, pyotp.TOTP(secret).now()))
        self.assertFalse(mfa_utils.verify_totp(secret, "000000"))
        # A code from a different secret must not validate.
        other = mfa_utils.generate_totp_secret()
        self.assertFalse(mfa_utils.verify_totp(secret, pyotp.TOTP(other).now()))

    def test_enable_then_disable_lifecycle(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.user, secret)
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_enabled)
        self.assertTrue(self.user.mfa_totp_secret)
        self.assertEqual(mfa_utils.user_totp_secret(self.user), secret)
        salt_before = self.user.mfa_device_salt
        self.assertTrue(salt_before)

        mfa_utils.generate_backup_codes(self.user, count=10)
        self.assertEqual(mfa_utils.unused_backup_code_count(self.user), 10)

        mfa_utils.disable_mfa(self.user)
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_enabled)
        self.assertEqual(self.user.mfa_totp_secret, "")
        self.assertEqual(mfa_utils.unused_backup_code_count(self.user), 0)
        self.assertNotEqual(self.user.mfa_device_salt, salt_before)  # salt rotated

    def test_backup_code_single_use(self):
        codes = mfa_utils.generate_backup_codes(self.user, count=3)
        self.assertEqual(len(codes), 3)
        self.assertTrue(mfa_utils.verify_and_consume_backup_code(self.user, codes[0]))
        # Reuse fails.
        self.assertFalse(mfa_utils.verify_and_consume_backup_code(self.user, codes[0]))
        self.assertEqual(mfa_utils.unused_backup_code_count(self.user), 2)
        # Dash/case-insensitive normalization still matches.
        self.assertTrue(
            mfa_utils.verify_and_consume_backup_code(self.user, codes[1].replace("-", "").lower())
        )

    def test_trusted_device_token_roundtrip_and_revocation(self):
        rf = RequestFactory()
        mfa_utils.ensure_device_salt(self.user)
        token = mfa_utils.make_trusted_device_token(self.user)

        req = rf.get("/")
        req.COOKIES[mfa_utils.settings.MFA_TRUSTED_DEVICE_COOKIE] = token
        self.assertTrue(mfa_utils.is_trusted_device(req, self.user))

        # Rotating the salt invalidates the previously issued cookie.
        mfa_utils.revoke_trusted_devices(self.user)
        self.assertFalse(mfa_utils.is_trusted_device(req, self.user))


@override_settings(MFA_SECRET_KEY=TEST_KEY, ENFORCE_PHONE_VERIFICATION=True)
class MfaLoginGateTests(TestCase):
    def setUp(self):
        cache.clear()
        mfa_utils._fernet_cache = None
        self.client = Client()

        self.owner = User.objects.create_user(
            phone="0591300010", password="pass1234", name="Owner",
            role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"], is_verified=True,
        )
        self.clinic = Clinic.objects.create(
            name="Clinic", address="St", phone="0591300011",
            main_doctor=self.owner, is_active=True,
        )
        self.secretary = User.objects.create_user(
            phone="0591300012", password="pass1234", name="Sec",
            role="SECRETARY", roles=["SECRETARY"], is_verified=True,
        )
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.secretary, role="SECRETARY", is_active=True,
        )
        self.patient = User.objects.create_user(
            phone="0591300013", password="pass1234", name="Pat",
            role="PATIENT", roles=["PATIENT"], is_verified=True,
        )

    def _login(self, phone, password="pass1234"):
        return self.client.post(reverse("accounts:login"), {"phone": phone, "password": password})

    def test_mfa_disabled_user_logs_in_single_step(self):
        resp = self._login(self.patient.phone)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_mfa_enabled_staff_is_deferred_to_challenge(self):
        mfa_utils.enable_mfa(self.secretary, mfa_utils.generate_totp_secret())
        resp = self._login(self.secretary.phone)
        self.assertRedirects(resp, reverse("accounts:mfa_challenge"))
        # Not yet authenticated — only a half-auth marker is stored.
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session["mfa_pending_user_id"], self.secretary.id)

    def test_challenge_with_valid_totp_logs_in(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        self._login(self.secretary.phone)
        resp = self.client.post(reverse("accounts:mfa_challenge"),
                                {"method": "totp", "code": pyotp.TOTP(secret).now()})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.secretary.id)
        self.assertNotIn("mfa_pending_user_id", self.client.session)

    def test_challenge_with_backup_code_logs_in(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        codes = mfa_utils.generate_backup_codes(self.secretary, count=5)
        self._login(self.secretary.phone)
        resp = self.client.post(reverse("accounts:mfa_challenge"),
                                {"method": "backup", "code": codes[0]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(mfa_utils.unused_backup_code_count(self.secretary), 4)

    def test_challenge_sms_fallback_path(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        self._login(self.secretary.phone)
        with patch("accounts.views.verify_otp", return_value=(True, "ok")) as vo:
            resp = self.client.post(reverse("accounts:mfa_challenge"),
                                    {"method": "sms", "code": "123456"})
        vo.assert_called_once()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(MFA_MAX_ATTEMPTS=3, MFA_IP_MAX_ATTEMPTS=1000)
    def test_challenge_wrong_code_throttles(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        self._login(self.secretary.phone)
        for _ in range(3):
            self.client.post(reverse("accounts:mfa_challenge"),
                             {"method": "totp", "code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)
        # Now blocked: even a correct code is refused this window.
        self.client.post(reverse("accounts:mfa_challenge"),
                         {"method": "totp", "code": pyotp.TOTP(secret).now()})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_device_skips_challenge_next_time(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        self._login(self.secretary.phone)
        self.client.post(reverse("accounts:mfa_challenge"),
                         {"method": "totp", "code": pyotp.TOTP(secret).now(),
                          "remember_device": "1"})
        self.assertIn("_auth_user_id", self.client.session)

        # Carry only the trusted-device cookie to a fresh browser (a new Client;
        # note Client.logout() would wipe the whole cookie jar, so we don't use it).
        cookie_name = settings.MFA_TRUSTED_DEVICE_COOKIE
        token = self.client.cookies[cookie_name].value
        other = Client()
        other.cookies[cookie_name] = token
        resp = other.post(reverse("accounts:login"),
                          {"phone": self.secretary.phone, "password": "pass1234"})
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp.url, reverse("accounts:mfa_challenge"))
        self.assertIn("_auth_user_id", other.session)

    def test_challenge_without_pending_marker_redirects_to_login(self):
        resp = self.client.get(reverse("accounts:mfa_challenge"))
        self.assertRedirects(resp, reverse("accounts:login"))

    @override_settings(MFA_PENDING_TTL_SECONDS=0)
    def test_expired_pending_marker_redirects_to_login(self):
        mfa_utils.enable_mfa(self.secretary, mfa_utils.generate_totp_secret())
        self._login(self.secretary.phone)
        resp = self.client.get(reverse("accounts:mfa_challenge"))
        self.assertRedirects(resp, reverse("accounts:login"))


@override_settings(MFA_SECRET_KEY=TEST_KEY)
class MfaEnrollmentTests(TestCase):
    def setUp(self):
        cache.clear()
        mfa_utils._fernet_cache = None
        self.client = Client()
        self.owner = User.objects.create_user(
            phone="0591300020", password="pass1234", name="Owner",
            role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"], is_verified=True,
        )
        self.clinic = Clinic.objects.create(
            name="Clinic", address="St", phone="0591300021",
            main_doctor=self.owner, is_active=True,
        )
        self.secretary = User.objects.create_user(
            phone="0591300022", password="pass1234", name="Sec",
            role="SECRETARY", roles=["SECRETARY"], is_verified=True,
        )
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.secretary, role="SECRETARY", is_active=True,
        )
        self.patient = User.objects.create_user(
            phone="0591300023", password="pass1234", name="Pat",
            role="PATIENT", roles=["PATIENT"], is_verified=True,
        )

    def test_patient_cannot_access_setup(self):
        self.client.force_login(self.patient)
        resp = self.client.get(reverse("accounts:mfa_setup"))
        self.assertEqual(resp.status_code, 302)  # bounced to home
        self.patient.refresh_from_db()
        self.assertFalse(self.patient.mfa_enabled)

    def test_staff_enrollment_confirm_before_enable(self):
        self.client.force_login(self.secretary)
        # GET seeds a candidate secret in the session but does not enable yet.
        self.client.get(reverse("accounts:mfa_setup"))
        secret = self.client.session["mfa_setup_secret"]
        self.secretary.refresh_from_db()
        self.assertFalse(self.secretary.mfa_enabled)

        # Wrong code does not enable.
        self.client.post(reverse("accounts:mfa_setup"), {"code": "000000"})
        self.secretary.refresh_from_db()
        self.assertFalse(self.secretary.mfa_enabled)

        # Correct code enables + redirects to backup codes (shown once). Don't
        # let assertRedirects fetch the target — that GET would consume the codes.
        resp = self.client.post(reverse("accounts:mfa_setup"),
                                {"code": pyotp.TOTP(secret).now()})
        self.assertRedirects(resp, reverse("accounts:mfa_backup_codes"),
                             fetch_redirect_response=False)
        self.secretary.refresh_from_db()
        self.assertTrue(self.secretary.mfa_enabled)

        page = self.client.get(reverse("accounts:mfa_backup_codes"))
        self.assertEqual(len(page.context["codes"]), 10)
        # Codes are shown only once: a reload no longer carries them.
        again = self.client.get(reverse("accounts:mfa_backup_codes"))
        self.assertIsNone(again.context["codes"])

    def test_disable_requires_reauth(self):
        secret = mfa_utils.generate_totp_secret()
        mfa_utils.enable_mfa(self.secretary, secret)
        self.client.force_login(self.secretary)

        # Wrong password keeps MFA on.
        self.client.post(reverse("accounts:mfa_disable"), {"password": "wrong"})
        self.secretary.refresh_from_db()
        self.assertTrue(self.secretary.mfa_enabled)

        # Correct password disables.
        resp = self.client.post(reverse("accounts:mfa_disable"), {"password": "pass1234"})
        self.assertEqual(resp.status_code, 302)
        self.secretary.refresh_from_db()
        self.assertFalse(self.secretary.mfa_enabled)
