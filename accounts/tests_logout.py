from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

User = get_user_model()


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+1234567890",
            name="Test User",
            password="password123",
            role="PATIENT",
        )
        self.client = Client()
        self.api_client = APIClient()

    def test_web_logout(self):
        """Verify Web logout invalidates session and redirects"""
        # 1. Login
        login_success = self.client.login(
            username="+1234567890", password="password123"
        )
        self.assertTrue(login_success, "Client login failed")

        # Verify session is active
        session = self.client.session
        self.assertTrue(session.keys(), "Session should not be empty after login")

        # 2. Logout
        response = self.client.get(reverse("accounts:logout"))

        # Verify redirect
        self.assertRedirects(response, reverse("accounts:login"))

        # Verify session is cleared (or strictly, user is no longer in session)
        # Note: session dictionary might not be perfectly empty depending on django version/middleware,
        # but _auth_user_id should be gone.
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_api_logout(self):
        """Verify API logout returns success and handles tokens"""
        # 1. Login to get tokens
        login_url = reverse("accounts:api_login")
        response = self.api_client.post(
            login_url, {"phone": "+1234567890", "password": "password123"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        refresh_token = response.data["refresh"]
        access_token = response.data["access"]

        # 2. Logout with refresh token
        logout_url = reverse("accounts:api_logout")

        # Authenticate first (optional based on permissions, but good practice if required)
        self.api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        logout_response = self.api_client.post(
            logout_url, {"refresh_token": refresh_token}
        )

        # 3. Verify Response
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(logout_response.data["detail"], "Successfully logged out.")

    def test_api_logout_idempotency_no_token(self):
        """Verify API logout works even without a token provided"""
        logout_url = reverse("accounts:api_logout")
        logout_response = self.api_client.post(logout_url, {})  # Empty body

        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(logout_response.data["detail"], "Successfully logged out.")

    def test_api_logout_idempotency_invalid_token(self):
        """Verify API logout works with invalid token"""
        logout_url = reverse("accounts:api_logout")
        logout_response = self.api_client.post(
            logout_url, {"refresh_token": "invalid_token_string"}
        )

        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(logout_response.data["detail"], "Successfully logged out.")
