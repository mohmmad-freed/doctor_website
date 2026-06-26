"""Open-redirect regression tests for the secretary portal.

Every ``?next=`` / POST ``next`` / Referer redirect in the portal must be
validated with ``url_has_allowed_host_and_scheme`` (via ``_is_safe_next`` in
secretary/views.py) so off-site / protocol-relative targets cannot turn a
post-action redirect into an open redirect.

``register_new_patient_only`` is the representative action: it computes its
redirect target from the POSTed ``next`` and falls back to
``secretary:appointments`` when that target is not a safe same-host path.
"""

from django.urls import reverse

from appointments.models import Appointment
from secretary.tests import SecretaryTestBase


# Targets a browser would follow off-site but that the legacy startswith("/")
# check failed to reject.
HOSTILE_NEXTS = [
    "//evil.com",               # protocol-relative — starts with "/"
    "/\\evil.com",              # backslash variant browsers normalize to //
    "https://evil.com/phish",   # absolute off-site
]


class SecretaryOpenRedirectTests(SecretaryTestBase):
    def _post_register_only(self, next_value):
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self.client.force_login(self.secretary_a)
        return self.client.post(
            reverse("secretary:register_new_patient_only", args=[appt.id]),
            {"next": next_value, "cancellation_reason": "x"},
        )

    def test_hostile_next_falls_back_to_safe_in_app_target(self):
        safe = reverse("secretary:appointments")
        for hostile in HOSTILE_NEXTS:
            with self.subTest(next=hostile):
                resp = self._post_register_only(hostile)
                self.assertEqual(resp.status_code, 302)
                location = resp.headers["Location"]
                self.assertNotIn("evil.com", location)
                self.assertEqual(location, safe)

    def test_legit_local_next_is_honored(self):
        target = reverse("appointments:secretary_notifications")
        resp = self._post_register_only(target)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], target)
