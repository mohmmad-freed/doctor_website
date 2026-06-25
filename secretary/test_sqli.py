"""
SQL-injection / dynamic-field regression guard for the secretary portal.

The portal is ORM-only (no .raw()/.extra()/cursor.execute), so the only column
an attacker could influence is the **ordering field**, via the user-controlled
``?sort=`` parameter. Two code paths read it:

- the patient roster — ``_patient_list_queryset`` clamps ``sort`` through the
  static ``_PATIENT_LIST_ALLOWED_SORTS`` whitelist (``secretary/views.py``);
- the purchase-request list — ``purchase_requests`` clamps ``sort`` through an
  inline ``sort_map`` whitelist before ``order_by`` (``secretary/views.py``).

Whitelist-then-index is the correct defense: an unrecognized ``sort`` must fall
back to the default ordering, never reach ``order_by()`` as a raw field name
(which would either leak/traverse arbitrary columns or raise ``FieldError`` →
500). These tests lock that behavior in so a future edit that drops the clamp
fails loudly rather than silently reopening the dynamic-field surface.
"""

from decimal import Decimal

from django.urls import reverse

from patients.models import ClinicPatient
from secretary.models import PurchaseRequest
from secretary.views import _PATIENT_LIST_ALLOWED_SORTS, _patient_list_queryset
from secretary.tests import SecretaryTestBase


# Values an attacker would try in ?sort= to escape the whitelist: a sensitive
# column, a relation traversal toward PII, and raw injection-flavored junk.
HOSTILE_SORTS = [
    "password",
    "patient__patient_profile__national_id",
    "patient__password",
    "id); DROP TABLE patients_clinicpatient;--",
    "-id' OR '1'='1",
    "",
]


class PatientListSortWhitelistTests(SecretaryTestBase):
    """The roster queryset must honor only whitelisted sorts; everything else
    falls back to the default ordering instead of reaching the ORM verbatim."""

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )

    def test_hostile_sort_falls_back_to_default_ordering(self):
        """No attacker-supplied field reaches order_by(); query orders by the
        default (-registered_at), proving the whitelist clamp held."""
        for sort in HOSTILE_SORTS:
            with self.subTest(sort=sort):
                qs = _patient_list_queryset(self.clinic_a, sort=sort)
                self.assertEqual(
                    tuple(qs.query.order_by), ("-registered_at",),
                    f"hostile sort {sort!r} leaked into order_by()",
                )

    def test_whitelisted_sorts_are_honored(self):
        """Positive control: every whitelisted key maps to its mapped column,
        proving the clamp isn't a no-op that ignores sort entirely."""
        for key, expected in _PATIENT_LIST_ALLOWED_SORTS.items():
            with self.subTest(sort=key):
                qs = _patient_list_queryset(self.clinic_a, sort=key)
                self.assertEqual(tuple(qs.query.order_by), (expected,))


class PatientListSortHttpTests(SecretaryTestBase):
    """End-to-end: hostile ?sort= must not 500 (FieldError) on either roster
    entry point — the full-page list or the HTMX live-search partial."""

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )
        self.client.force_login(self.secretary_a)

    def test_patient_list_survives_hostile_sort(self):
        for sort in HOSTILE_SORTS:
            with self.subTest(sort=sort):
                resp = self.client.get(
                    reverse("secretary:patient_list"), {"sort": sort},
                )
                self.assertEqual(resp.status_code, 200)

    def test_patient_list_htmx_survives_hostile_sort(self):
        for sort in HOSTILE_SORTS:
            with self.subTest(sort=sort):
                resp = self.client.get(
                    reverse("secretary:patient_list_htmx"),
                    {"q": "al", "sort": sort},
                )
                self.assertEqual(resp.status_code, 200)


class PurchaseRequestSortHttpTests(SecretaryTestBase):
    """The purchase-request list clamps ?sort= via an inline sort_map whitelist
    that can't be unit-introspected, so guard it over HTTP: hostile sorts must
    fall back to default ordering (200), whitelisted ones must work (200)."""

    def setUp(self):
        super().setUp()
        PurchaseRequest.objects.create(
            clinic=self.clinic_a, requested_by=self.secretary_a,
            request_number="PR-SQLI-1", title="Gloves", total=Decimal("10"),
        )
        self.client.force_login(self.secretary_a)

    def test_survives_hostile_sort(self):
        for sort in HOSTILE_SORTS:
            with self.subTest(sort=sort):
                resp = self.client.get(
                    reverse("secretary:purchase_requests"), {"sort": sort},
                )
                self.assertEqual(resp.status_code, 200)

    def test_whitelisted_sorts_work(self):
        for sort in ("newest", "cost_high", "cost_low"):
            with self.subTest(sort=sort):
                resp = self.client.get(
                    reverse("secretary:purchase_requests"), {"sort": sort},
                )
                self.assertEqual(resp.status_code, 200)
