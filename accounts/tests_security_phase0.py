"""
Phase 0 auth security fixes — regression tests.

Covers, deterministically (OTP mocked, isolated locmem cache so no real SMS is
sent and the dev Redis is never touched):

  Fix 1  No plaintext password persisted in the clinic-owner wizard session.
  Fix 2  Legacy register_main_doctor retired (no unverified account-takeover path).
         -> see also accounts/tests.py MainDoctorSignupTest.
  Fix 3  Login `next` is validated (no open redirect) AND survives the POST.
  Fix 4  No account-enumeration oracle in patient signup step 1 or forgot-password.
  Fix 5  book_appointment_view is @login_required (clean redirect, not a 500).

The harness forces DEBUG=False and .env points OTP at a real SMS provider, so any
unmocked request_otp would hit the network — every test here mocks it.
"""
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache
from django.contrib.auth.hashers import check_password
from datetime import timedelta

from accounts.models import CustomUser, City
from accounts.forms import ForgotPasswordPhoneForm
from clinics.models import Clinic, ClinicActivationCode
from doctors.models import Specialty

# Isolated in-process cache: ratelimit/OTP bookkeeping can't leak across tests or
# into the developer's Redis, and cache.clear() in setUp is free + safe.
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_patient(phone, password="StrongPass123!", **extra):
    """Create a verified PATIENT who can log in via the phone backend."""
    user = CustomUser.objects.create_user(phone=phone, name="Test Patient", password=password)
    user.role = "PATIENT"
    user.roles = ["PATIENT"]
    user.is_verified = True
    for k, v in extra.items():
        setattr(user, k, v)
    user.save()
    return user


# ===========================================================================
# Fix 5 — book_appointment_view requires login
# ===========================================================================
@override_settings(CACHES=LOCMEM)
class BookAppointmentLoginRequiredTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("appointments:book_appointment", kwargs={"clinic_id": 999999})

    def test_anonymous_redirected_to_login_not_500(self):
        """Anonymous GET → 302 to /login/?next=…  (previously 500 on AnonymousUser.has_role)."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        self.assertIn("next=", resp["Location"])

    def test_authenticated_patient_passes_login_gate(self):
        """A logged-in patient clears @login_required and reaches the view body.

        With a non-existent clinic that means a clean 404 — crucially NOT a redirect
        to login and NOT a 500."""
        make_patient("0599000010")
        self.client.login(username="0599000010", password="StrongPass123!")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)


# ===========================================================================
# Fix 3 — login `next` validated and carried across the POST
# ===========================================================================
@override_settings(CACHES=LOCMEM, ENFORCE_PHONE_VERIFICATION=False)
class LoginNextHandlingTest(TestCase):
    SAFE_NEXT = "/appointments/book/5/?doctor_id=3"

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.login_url = reverse("accounts:login")
        self.user = make_patient("0599000020")

    def test_get_renders_safe_next_as_hidden_field(self):
        resp = self.client.get(self.login_url, {"next": self.SAFE_NEXT})
        self.assertEqual(resp.status_code, 200)
        # The login form's own hidden field carries the validated target verbatim.
        # (Asserting the exact field value avoids matching the navbar language
        # form, which also renders a name="next" field from get_full_path.)
        self.assertContains(resp, 'name="next" value="/appointments/book/5/?doctor_id=3"')

    def test_get_strips_offsite_next(self):
        resp = self.client.get(self.login_url, {"next": "https://evil.example.com/x"})
        self.assertEqual(resp.status_code, 200)
        # Offsite target is stripped to "" in the login field (never rendered as
        # its value). It may appear url-encoded elsewhere (the language form's
        # get_full_path, guarded separately), so we assert on the exact field value.
        self.assertContains(resp, 'name="next" value=""')
        self.assertNotContains(resp, 'name="next" value="https://evil.example.com/x"')

    def test_post_redirects_to_safe_next(self):
        resp = self.client.post(self.login_url, {
            "phone": "0599000020",
            "password": "StrongPass123!",
            "next": self.SAFE_NEXT,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], self.SAFE_NEXT)

    def test_post_ignores_offsite_next(self):
        resp = self.client.post(self.login_url, {
            "phone": "0599000020",
            "password": "StrongPass123!",
            "next": "https://evil.example.com/x",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp["Location"])

    def test_post_ignores_protocol_relative_next(self):
        resp = self.client.post(self.login_url, {
            "phone": "0599000020",
            "password": "StrongPass123!",
            "next": "//evil.example.com/x",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp["Location"])


# ===========================================================================
# Fix 4 — no account-enumeration oracle (patient signup step 1)
# ===========================================================================
@override_settings(CACHES=LOCMEM)
class SignupEnumerationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("accounts:register_patient_phone")
        self.verify_url = reverse("accounts:register_patient_verify")
        self.existing = make_patient("0599000030")

    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_existing_and_new_phone_get_identical_response(self, mock_req):
        new_resp = self.client.post(self.url, {"phone": "0599000031"})
        self.assertRedirects(new_resp, self.verify_url, fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("registration_phone"), "0599000031")

        self.client = Client()  # fresh session
        exist_resp = self.client.post(self.url, {"phone": "0599000030"})
        self.assertRedirects(exist_resp, self.verify_url, fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("registration_phone"), "0599000030")

        # Same on-screen outcome (same redirect target) — no enumeration oracle.
        self.assertEqual(new_resp["Location"], exist_resp["Location"])

    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_existing_phone_issues_no_verifiable_otp(self, mock_req):
        """A registered number advances identically but gets NO real OTP, so it
        can't be driven through the flow to create a duplicate account."""
        self.client.post(self.url, {"phone": "0599000030"})
        mock_req.assert_not_called()

    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_new_phone_issues_real_otp(self, mock_req):
        self.client.post(self.url, {"phone": "0599000031"})
        mock_req.assert_called_once()


# ===========================================================================
# Fix 4 — no account-enumeration oracle (forgot password)
# ===========================================================================
@override_settings(CACHES=LOCMEM)
class ForgotPasswordEnumerationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("accounts:forgot_password_phone")
        self.verify_url = reverse("accounts:forgot_password_verify")
        self.existing = make_patient("0599000040")

    def test_form_no_longer_rejects_unknown_number(self):
        """The existence check was the oracle; the form now accepts any valid number."""
        self.assertTrue(ForgotPasswordPhoneForm(data={"phone": "0599000099"}).is_valid())
        # Bad format is still rejected.
        self.assertFalse(ForgotPasswordPhoneForm(data={"phone": "123"}).is_valid())

    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_known_and_unknown_phone_get_identical_response(self, mock_req):
        known = self.client.post(self.url, {"phone": "0599000040"})
        self.assertRedirects(known, self.verify_url, fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("reset_phone"), "0599000040")

        self.client = Client()
        unknown = self.client.post(self.url, {"phone": "0599000041"})
        self.assertRedirects(unknown, self.verify_url, fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("reset_phone"), "0599000041")

        self.assertEqual(known["Location"], unknown["Location"])

    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_otp_sent_only_to_existing_account(self, mock_req):
        self.client.post(self.url, {"phone": "0599000040"})
        mock_req.assert_called_once()

        mock_req.reset_mock()
        self.client = Client()
        self.client.post(self.url, {"phone": "0599000041"})
        mock_req.assert_not_called()


# ===========================================================================
# Fix 1 — no plaintext password in the clinic-owner wizard session
# ===========================================================================
@override_settings(CACHES=LOCMEM)
class ClinicWizardPasswordTest(TestCase):
    OWNER_PHONE = "0599000050"
    OWNER_NID = "456456456"
    CODE = "PWHASH001"
    PASSWORD = "StrongPass123!"

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.city = City.objects.create(name="Nablus")
        self.specialty = Specialty.objects.create(name="GP", name_ar="عام")
        self.activation_code = ClinicActivationCode.objects.create(
            code=self.CODE,
            clinic_name="عيادة",
            phone=self.OWNER_PHONE,
            national_id=self.OWNER_NID,
            plan_type="MONTHLY",
            subscription_expires_at=timezone.now() + timedelta(days=30),
            max_doctors=2,
        )

    def _step1(self):
        return self.client.post(reverse("accounts:register_clinic_step1"), {
            "activation_code": self.CODE,
            "phone": self.OWNER_PHONE,
            "national_id": self.OWNER_NID,
        })

    def _step2(self):
        return self.client.post(reverse("accounts:register_clinic_step2"), {
            "name": "محمد أحمد",
            "email": "owner@test.com",
            "password": self.PASSWORD,
            "confirm_password": self.PASSWORD,
        })

    def _step3(self):
        with patch("accounts.views.request_otp", return_value=(True, "sent")):
            return self.client.post(reverse("accounts:register_clinic_step3"), {
                "clinic_name": "عيادة الأمل",
                "clinic_address": "شارع 1",
                "clinic_city": self.city.id,
                "specialties": [self.specialty.id],
                "clinic_description": "وصف",
            })

    def test_session_stores_hash_not_cleartext(self):
        self._step1()
        self._step2()
        reg = self.client.session["clinic_reg"]
        self.assertNotIn("password", reg)                      # no cleartext key
        self.assertIn("password_hash", reg)
        self.assertNotEqual(reg["password_hash"], self.PASSWORD)  # not stored raw
        # It is a real, verifiable Django password hash.
        self.assertTrue(check_password(self.PASSWORD, reg["password_hash"]))
        self.assertNotIn(self.PASSWORD, str(reg))              # nowhere in the blob

    @patch("accounts.views.verify_email_otp", return_value=(True, "ok"))
    @patch("accounts.views.send_email_otp", return_value=(True, "ok"))
    @patch("accounts.views.verify_otp", return_value=(True, "ok"))
    @patch("accounts.views.request_otp", return_value=(True, "sent"))
    def test_created_owner_can_authenticate_with_chosen_password(
        self, m_req, m_verify, m_send_email, m_verify_email
    ):
        """End-to-end: the hashed-in-session password is applied correctly, so the
        new owner can actually log in with the password they chose (not double-hashed)."""
        self._step1()
        self._step2()
        self._step3()
        self.client.post(reverse("accounts:register_clinic_verify_phone"), {"otp": "123456"})
        self.client.post(reverse("accounts:register_clinic_verify_email"), {"otp": "123456"})

        user = CustomUser.objects.get(phone=self.OWNER_PHONE)
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertTrue(Clinic.objects.filter(main_doctor=user).exists())
