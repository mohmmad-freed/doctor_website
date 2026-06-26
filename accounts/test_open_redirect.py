"""Open-redirect regression tests for the accounts app.

Every redirect that honours a user-supplied target (`?next=`, POST `next`,
Referer) must be validated with ``url_has_allowed_host_and_scheme`` against the
request host, so an attacker cannot turn a genuine login / language switch into a
bounce to an off-site phishing page.

Covers:
- the post-login ``?next=`` redirect (``login_view`` → ``_safe_next`` →
  ``handle_pending_invitation_redirect``)
- the language endpoint (``set_language_preference``)

The hostile targets below all defeated the previous string checks:
``startswith("/")`` passed ``//evil.com`` / ``/\\evil.com`` (protocol-relative),
and ``startswith("http")`` passed ``//evil.com`` and cased ``HTTPS://``.
"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from patients.models import PatientProfile


# Targets a browser would follow off-site.
HOSTILE_NEXTS = [
    "//evil.com",               # protocol-relative — starts with "/"
    "/\\evil.com",              # backslash variant browsers normalize to //
    "https://evil.com/phish",   # absolute off-site
    "HTTPS://evil.com",         # cased scheme (defeated startswith("http"))
]


@override_settings(ENFORCE_PHONE_VERIFICATION=False)
class LoginOpenRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = "TestPass123!@#"
        self.user = CustomUser.objects.create_user(
            phone="0594073157", name="Test User",
            national_id="123456789", password=self.password,
        )
        PatientProfile.objects.create(user=self.user)

    def _login_with_next(self, next_value):
        url = reverse("accounts:login") + "?next=" + next_value
        return self.client.post(
            url, {"phone": "0594073157", "password": self.password}
        )

    def test_hostile_next_is_rejected_and_falls_back_home(self):
        home = reverse("accounts:home")
        for hostile in HOSTILE_NEXTS:
            with self.subTest(next=hostile):
                self.client.logout()
                resp = self._login_with_next(hostile)
                self.assertEqual(resp.status_code, 302)
                location = resp.headers["Location"]
                self.assertNotIn("evil.com", location)
                self.assertEqual(location, home)

    def test_valid_local_next_is_honored(self):
        target = "/accounts/dashboard/"
        resp = self._login_with_next(target)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], target)


class SetLanguageOpenRedirectTests(TestCase):
    def test_hostile_next_falls_back_to_root(self):
        for hostile in HOSTILE_NEXTS:
            with self.subTest(next=hostile):
                resp = self.client.post(
                    reverse("accounts:set_language"),
                    {"language": "en", "next": hostile},
                )
                self.assertEqual(resp.status_code, 302)
                location = resp.headers["Location"]
                self.assertNotIn("evil.com", location)
                self.assertEqual(location, "/")

    def test_valid_local_next_is_honored(self):
        target = "/accounts/dashboard/"
        resp = self.client.post(
            reverse("accounts:set_language"),
            {"language": "en", "next": target},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], target)
