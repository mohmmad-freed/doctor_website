from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache
from accounts.models import City

User = get_user_model()


class PhoneChangeTest(TestCase):
    def setUp(self):
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
