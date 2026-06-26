from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache
from accounts.models import City

User = get_user_model()


# Isolated locmem cache so the change-phone OTP step is deterministic and the
# OTP cooldown can't leak in from the real Redis the dev app shares. The live
# TweetsMS send is neutralised per-test in setUp (it's "configured" in this env
# but the gateway rejects the send, which otherwise makes request_otp return
# False and the request step re-render with 200 instead of redirecting).
_OTP_SEND_TEST_OVERRIDES = dict(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "phone-change-tests",
        }
    },
    ENFORCE_OTP_LIMITS=False,
)


@override_settings(**_OTP_SEND_TEST_OVERRIDES)
class PhoneChangeTest(TestCase):
    def setUp(self):
        cache.clear()  # isolate OTP cooldown from prior tests / the dev Redis
        # request_otp still stores the OTP in cache; it just doesn't hit TweetsMS.
        p = patch("accounts.otp_utils.tweetsms_send_sms", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        # Create a city (referenced in some user signals or forms?)
        self.city = City.objects.create(name="Gaza")

        # Create a user
        self.user = User.objects.create_user(
            phone="0591111111",
            password="password123",
            name="Test User",
            role="PATIENT",
            city=self.city,
        )
        self.client = Client()
        self.client.login(phone="0591111111", password="password123")

    def tearDown(self):
        cache.clear()

    def test_change_phone_request_invalid_format(self):
        response = self.client.post(
            reverse("accounts:change_phone_request"), {"phone": "123"}  # Invalid
        )
        self.assertEqual(response.status_code, 200)  # Should verify on same page
        messages = list(response.context["messages"])
        self.assertTrue(any("رقم الهاتف غير صحيح" in str(m) for m in messages))

    def test_change_phone_request_same_phone(self):
        response = self.client.post(
            reverse("accounts:change_phone_request"), {"phone": "0591111111"}
        )
        messages = list(response.context["messages"])
        self.assertTrue(any("رقم هاتفك الحالي" in str(m) for m in messages))

    def test_change_phone_request_existing_phone(self):
        # Create another user
        User.objects.create_user(phone="0592222222", password="p", name="U2")

        response = self.client.post(
            reverse("accounts:change_phone_request"), {"phone": "0592222222"}
        )
        messages = list(response.context["messages"])
        self.assertTrue(any("مسجل بالفعل" in str(m) for m in messages))

    # Force the local OTP mock path so this flow is deterministic regardless of
    # SMS_PROVIDER — passes whether the suite runs with the live provider
    # configured (friend's class setup patches the sender) or with SMS neutralised
    # (SMS_PROVIDER=""). The runner forces DEBUG=False; this re-enables the mock
    # fallback for this one flow so request_otp stores the code and returns True.
    @override_settings(DEBUG=True, SMS_PROVIDER="")
    def test_change_phone_flow_success(self):
        new_phone = "0599999999"

        # 1. Request Change
        response = self.client.post(
            reverse("accounts:change_phone_request"), {"phone": new_phone}
        )

        # Should redirect to verify
        self.assertRedirects(response, reverse("accounts:change_phone_verify"))

        # Check Session
        self.assertEqual(self.client.session["change_phone_new"], new_phone)

        # Get OTP from Cache (Mock behavior)
        otp_key = f"otp:code:{new_phone}"
        otp = cache.get(otp_key)
        self.assertIsNotNone(otp, "OTP should be stored in cache")

        # 2. Verify with Wrong OTP
        response = self.client.post(
            reverse("accounts:change_phone_verify"), {"otp": "000000"}
        )
        messages = list(response.context["messages"])
        self.assertTrue(
            any("Incorrect OTP" in str(m) or "خاطئ" in str(m) for m in messages)
        )

        # 3. Verify with Correct OTP
        response = self.client.post(
            reverse("accounts:change_phone_verify"), {"otp": otp}
        )

        # Should redirect to profile
        self.assertRedirects(response, reverse("patients:profile"))

        # Verify DB Update
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, new_phone)
        self.assertTrue(self.user.is_verified)
