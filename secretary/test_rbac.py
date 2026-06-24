"""
RBAC sweep for the secretary portal.

Premise: every secretary endpoint must be gated by the @secretary_required
decorator (authentication + an active ClinicStaff row with role="SECRETARY").
This suite enumerates EVERY route in ``secretary/urls.py`` and asserts that a
non-secretary (PATIENT / DOCTOR / MAIN_DOCTOR) is denied with 403 and an
anonymous visitor is redirected to login — so a newly added endpoint that
forgets the guard fails the build.

Five endpoints are intentionally NOT secretary-gated (public kiosk + the
invitation join-flow, each with its own compensating control); they live in
``INTENTIONAL_EXCEPTIONS`` and are asserted separately.

Membership is keyed on the ClinicStaff row, never on user.role/user.roles, so
multi-role users (DOCTOR+SECRETARY, or a PATIENT promoted to SECRETARY) are
covered explicitly.
"""

import uuid

from django.urls import reverse

from clinics.models import ClinicStaff
from django.contrib.auth import get_user_model

from secretary import urls as secretary_urls
from secretary.tests import SecretaryTestBase

User = get_user_model()


# URL names that are deliberately reachable without a secretary post.
INTENTIONAL_EXCEPTIONS = {
    "waiting_room_display",          # public TV/kiosk, gated by display_token
    "secretary_invitations_inbox",   # join flow: not-yet-secretary, scoped to own phone
    "accept_invitation",             # join flow: phone-match IDOR guard
    "reject_invitation",             # join flow: phone-match IDOR guard
    "guest_accept_invitation",       # public SMS link, gated by UUID token
}


def _dummy_kwargs(pattern):
    """Build placeholder kwargs for a route's URL captures.

    The guard runs in the decorator *before* any get_object_or_404, so bogus ids
    still yield 403 for a non-secretary (never a 404 that would mask a missing
    guard).
    """
    kwargs = {}
    for name, conv in getattr(pattern.pattern, "converters", {}).items():
        cname = type(conv).__name__
        if cname == "IntConverter":
            kwargs[name] = 999999
        elif cname == "UUIDConverter":
            kwargs[name] = uuid.UUID("00000000-0000-0000-0000-000000000000")
        else:
            kwargs[name] = "x"
    return kwargs


def _guarded_routes():
    """(name, url) for every secretary route except the intentional exceptions."""
    routes = []
    for p in secretary_urls.urlpatterns:
        name = getattr(p, "name", None)
        if not name or name in INTENTIONAL_EXCEPTIONS:
            continue
        routes.append((name, reverse(f"secretary:{name}", kwargs=_dummy_kwargs(p))))
    return routes


class SecretaryRbacSweepTests(SecretaryTestBase):
    """Every guarded secretary endpoint denies non-secretaries."""

    def setUp(self):
        super().setUp()
        self.routes = _guarded_routes()
        # Guard against a silently-broken enumeration: the portal has ~65 guarded
        # endpoints, so anything far below that means the sweep stopped finding them.
        self.assertGreaterEqual(
            len(self.routes), 60,
            "URL enumeration looks broken — too few guarded routes discovered.",
        )

    def test_authenticated_non_secretaries_get_403(self):
        """PATIENT, DOCTOR and MAIN_DOCTOR are forbidden from every guarded route."""
        actors = {
            "PATIENT": self.patient_a,
            "DOCTOR": self.doctor_a,
            "MAIN_DOCTOR": self.main_doctor_a,
        }
        for label, user in actors.items():
            self.client.force_login(user)
            for name, url in self.routes:
                with self.subTest(actor=label, route=name):
                    resp = self.client.get(url)
                    self.assertEqual(
                        resp.status_code, 403,
                        f"{label} reached secretary:{name} (got {resp.status_code}, want 403)",
                    )
            self.client.logout()

    def test_anonymous_is_redirected_to_login(self):
        """No session → login_required redirect (302), never a 200."""
        for name, url in self.routes:
            with self.subTest(route=name):
                resp = self.client.get(url)
                self.assertEqual(
                    resp.status_code, 302,
                    f"anonymous reached secretary:{name} (got {resp.status_code}, want 302)",
                )
                self.assertIn("/login", resp.url)

    def test_positive_control_secretary_reaches_dashboard(self):
        """Sanity: the active secretary is NOT denied — proves the 403s are RBAC,
        not a globally broken portal."""
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:dashboard"))
        self.assertEqual(resp.status_code, 200)


class SecretaryRbacMultiRoleTests(SecretaryTestBase):
    """Access is decided by the SECRETARY ClinicStaff row, not the primary role."""

    def _dashboard_status(self, user):
        self.client.force_login(user)
        try:
            return self.client.get(reverse("secretary:dashboard")).status_code
        finally:
            self.client.logout()

    def test_patient_promoted_to_secretary_has_access(self):
        """roles=[PATIENT, SECRETARY], primary role SECRETARY + active post → allowed."""
        user = User.objects.create_user(
            phone="0591100020", password="pass1234",
            name="Dual Patient-Secretary",
            role="SECRETARY", roles=["PATIENT", "SECRETARY"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=user, role="SECRETARY", is_active=True,
        )
        self.assertEqual(self._dashboard_status(user), 200)

    def test_doctor_who_is_also_secretary_has_access(self):
        """Primary role DOCTOR but holds an active SECRETARY post → allowed.

        Proves membership is ClinicStaff-driven: the guard must not reject based on
        the primary role string."""
        user = User.objects.create_user(
            phone="0591100021", password="pass1234",
            name="Dual Doctor-Secretary",
            role="DOCTOR", roles=["DOCTOR", "SECRETARY"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=user, role="DOCTOR", is_active=True,
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=user, role="SECRETARY", is_active=True,
        )
        self.assertEqual(self._dashboard_status(user), 200)

    def test_secretary_without_secretary_post_is_denied(self):
        """role=SECRETARY string but NO active SECRETARY ClinicStaff → 403.

        (A bare role label must never grant access on its own.)"""
        user = User.objects.create_user(
            phone="0591100022", password="pass1234",
            name="Label-only Secretary",
            role="SECRETARY", roles=["SECRETARY"],
        )
        # Only a DOCTOR post — so _require_secretary finds nothing.
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=user, role="DOCTOR", is_active=True,
        )
        self.assertEqual(self._dashboard_status(user), 403)


class SecretaryRbacDeactivationTests(SecretaryTestBase):
    """A deactivated secretary loses access on every guarded route."""

    def test_deactivated_secretary_is_denied_everywhere(self):
        self.staff_a.is_active = False
        self.staff_a.save(update_fields=["is_active"])

        self.client.force_login(self.secretary_a)
        for name, url in _guarded_routes():
            with self.subTest(route=name):
                resp = self.client.get(url)
                self.assertEqual(
                    resp.status_code, 403,
                    f"deactivated secretary reached secretary:{name} "
                    f"(got {resp.status_code}, want 403)",
                )

    def test_reactivating_restores_access(self):
        """Control: flipping is_active back on restores access (the 403 was the flag,
        not an unrelated breakage)."""
        self.staff_a.is_active = False
        self.staff_a.save(update_fields=["is_active"])
        self.client.force_login(self.secretary_a)
        self.assertEqual(
            self.client.get(reverse("secretary:dashboard")).status_code, 403,
        )

        self.staff_a.is_active = True
        self.staff_a.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get(reverse("secretary:dashboard")).status_code, 200,
        )


class SecretaryIntentionalExceptionTests(SecretaryTestBase):
    """The 5 endpoints that are deliberately NOT secretary-gated stay reachable
    by design (documented, with their own compensating controls)."""

    def test_waiting_room_display_is_public_not_login_gated(self):
        """Anonymous + no token → 400 (bad request), NOT a 302 login redirect:
        confirms the kiosk is a public, token-addressed endpoint."""
        resp = self.client.get(reverse("secretary:waiting_room_display"))
        self.assertEqual(resp.status_code, 400)

    def test_invite_inbox_reachable_by_not_yet_active_secretary(self):
        """A user with role=SECRETARY but no active ClinicStaff (mid-join) can still
        open the invitation inbox — it is intentionally login-only, not gated by
        _require_secretary."""
        joiner = User.objects.create_user(
            phone="0591100030", password="pass1234",
            name="Pending Joiner", role="SECRETARY", roles=["SECRETARY"],
        )
        self.client.force_login(joiner)
        resp = self.client.get(reverse("secretary:secretary_invitations_inbox"))
        self.assertEqual(resp.status_code, 200)
