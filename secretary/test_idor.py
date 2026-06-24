"""
Adversarial IDOR / cross-tenant isolation tests for the secretary portal.

Premise: clinic B's secretary (the adversary) must never reach clinic A's
objects by swapping the id in a secretary URL. Every id-bearing endpoint must
return **404** (never 200, a 302-with-effect, or 500) for a cross-clinic id.

A positive control proves the same ids ARE reachable by clinic A's own
secretary, so the 404s reflect tenant isolation rather than missing rows.

Companion class ``SecretaryPatientCardHardeningTests`` covers the registration
*lookup* surfaces (``patient_card`` and the walk-in HTMX), which fetch a global
``User`` by id: they must not surface PII for a patient the secretary can't
legitimately reach (already registered here, or matched by an exact strong id).
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from clinics.models import DoctorAvailabilityException
from patients.models import ClinicPatient, StaffNote
from secretary.models import Invoice, InvoiceItem, PurchaseRequest
from secretary.tests import SecretaryTestBase

User = get_user_model()


class SecretaryIDORTests(SecretaryTestBase):
    """Sweep every id-bearing secretary endpoint for cross-clinic access."""

    def setUp(self):
        super().setUp()
        # patient_a belongs to clinic A only.
        self.cp_a = ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )
        # Clinic-A-owned objects the adversary will try to reach by id.
        self.appt_a = self._make_appointment(clinic=self.clinic_a)
        self.invoice_a = Invoice.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            invoice_number="INV-IDOR-A-1", created_by=self.secretary_a,
        )
        self.item_a = InvoiceItem.objects.create(
            invoice=self.invoice_a, description="Consultation", unit_price=Decimal("50"),
        )
        self.pr_a = PurchaseRequest.objects.create(
            clinic=self.clinic_a, requested_by=self.secretary_a,
            request_number="PR-IDOR-A-1", title="Gloves",
        )
        self.exc_a = DoctorAvailabilityException.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            start_date=date.today(), end_date=date.today() + timedelta(days=1),
            created_by=self.secretary_a,
        )
        self.appt_note_a = StaffNote.objects.create(
            clinic=self.clinic_a, patient=self.patient_a, appointment=self.appt_a,
            audience=StaffNote.Audience.SECRETARY, body="appt note",
            author=self.secretary_a, author_name="Secretary A", author_role="SECRETARY",
        )
        self.patient_note_a = StaffNote.objects.create(
            clinic=self.clinic_a, patient=self.patient_a, appointment=None,
            audience=StaffNote.Audience.SECRETARY, body="profile note",
            author=self.secretary_a, author_name="Secretary A", author_role="SECRETARY",
        )

    def _endpoints(self):
        """Every id-bearing endpoint as (url_name, args, http_method).

        @require_POST views are exercised with POST so the decorator doesn't
        short-circuit with 405 before the clinic-scoped object lookup runs.
        """
        return [
            # ── appointment_id ──────────────────────────────────────────
            ("secretary:edit_appointment", [self.appt_a.id], "get"),
            ("secretary:cancel_appointment", [self.appt_a.id], "post"),
            ("secretary:checkin_appointment", [self.appt_a.id], "post"),
            ("secretary:update_appointment_status", [self.appt_a.id], "post"),
            ("secretary:accept_new_patient_request", [self.appt_a.id], "post"),
            ("secretary:reject_new_patient_request", [self.appt_a.id], "post"),
            ("secretary:register_new_patient_only", [self.appt_a.id], "post"),
            ("secretary:appointment_overview", [self.appt_a.id], "get"),
            ("secretary:appointment_intake_partial", [self.appt_a.id], "get"),
            ("secretary:remove_from_queue", [self.appt_a.id], "post"),
            ("secretary:start_billing", [self.appt_a.id], "post"),
            ("secretary:appointment_note_add", [self.appt_a.id], "post"),
            ("secretary:appointment_note_delete",
             [self.appt_a.id, self.appt_note_a.id], "post"),
            # ── invoice_id / item_id ────────────────────────────────────
            ("secretary:invoice_detail", [self.invoice_a.id], "get"),
            ("secretary:invoice_add_charge", [self.invoice_a.id], "post"),
            ("secretary:invoice_remove_charge",
             [self.invoice_a.id, self.item_a.id], "post"),
            ("secretary:invoice_record_payment", [self.invoice_a.id], "post"),
            ("secretary:invoice_delete", [self.invoice_a.id], "post"),
            # ── request_id ──────────────────────────────────────────────
            ("secretary:purchase_request_delete", [self.pr_a.id], "post"),
            # ── exception_id ────────────────────────────────────────────
            ("secretary:delete_doctor_block", [self.exc_a.id], "post"),
            # ── patient_id ──────────────────────────────────────────────
            ("secretary:patient_detail", [self.patient_a.id], "get"),
            ("secretary:patient_card", [self.patient_a.id], "get"),
            ("secretary:patient_note_add", [self.patient_a.id], "post"),
            ("secretary:patient_note_delete",
             [self.patient_a.id, self.patient_note_a.id], "post"),
            ("secretary:clinical_note_print", [self.patient_a.id], "get"),
            ("secretary:patient_pay_debt", [self.patient_a.id], "post"),
            ("secretary:edit_patient", [self.patient_a.id], "get"),
            ("secretary:remove_patient_block", [self.patient_a.id], "post"),
        ]

    def _call(self, url_name, args, method):
        return getattr(self.client, method)(reverse(url_name, args=args))

    def test_cross_clinic_access_returns_404(self):
        """secretary_b gets 404 on every clinic-A id endpoint (no leak, no effect)."""
        self.client.force_login(self.secretary_b)
        for url_name, args, method in self._endpoints():
            with self.subTest(endpoint=url_name):
                resp = self._call(url_name, args, method)
                self.assertEqual(
                    resp.status_code, 404,
                    f"{url_name} returned {resp.status_code} (expected 404) "
                    f"for a cross-clinic id",
                )

    def test_no_server_errors_cross_clinic(self):
        """No endpoint 500s on a cross-clinic id (guards broad-except Http404 swallows)."""
        self.client.force_login(self.secretary_b)
        for url_name, args, method in self._endpoints():
            with self.subTest(endpoint=url_name):
                resp = self._call(url_name, args, method)
                self.assertNotEqual(
                    resp.status_code, 500, f"{url_name} returned 500 cross-clinic",
                )

    def test_owning_secretary_can_reach_objects(self):
        """Positive control: clinic A's secretary reads these same ids (200),
        proving the 404s above are tenant isolation, not missing/typo'd rows."""
        self.client.force_login(self.secretary_a)
        read_endpoints = [
            ("secretary:patient_detail", [self.patient_a.id]),
            ("secretary:patient_card", [self.patient_a.id]),
            ("secretary:invoice_detail", [self.invoice_a.id]),
            ("secretary:appointment_overview", [self.appt_a.id]),
            ("secretary:edit_patient", [self.patient_a.id]),
        ]
        for url_name, args in read_endpoints:
            with self.subTest(endpoint=url_name):
                resp = self.client.get(reverse(url_name, args=args))
                self.assertEqual(
                    resp.status_code, 200, f"{url_name} not reachable by its owning secretary",
                )


class SecretaryPatientCardHardeningTests(SecretaryTestBase):
    """The registration lookup surfaces fetch a *global* User by id; they must
    only surface PII for a patient the secretary can legitimately reach."""

    def setUp(self):
        super().setUp()
        # patient_a is registered ONLY in clinic A.
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a, registered_by=self.secretary_a,
        )
        # A patient registered in NEITHER clinic — the global-lookup case.
        self.outsider = User.objects.create_user(
            phone="0599999999", password="pass1234", national_id="987654321",
            name="Outsider Omar", role="PATIENT", roles=["PATIENT"],
        )

    def _card(self, patient_id, q=None):
        url = reverse("secretary:patient_card", args=[patient_id])
        if q is not None:
            url += f"?q={q}"
        return self.client.get(url)

    def test_card_404_for_foreign_patient_without_query(self):
        """secretary_b can't pull patient_a's card by bare id (not their patient)."""
        self.client.force_login(self.secretary_b)
        self.assertEqual(self._card(self.patient_a.id).status_code, 404)

    def test_card_404_for_outsider_with_wrong_query(self):
        """A non-matching query can't unlock an arbitrary global user's card."""
        self.client.force_login(self.secretary_b)
        self.assertEqual(self._card(self.outsider.id, q="nope").status_code, 404)

    def test_card_ok_with_exact_phone(self):
        """Registration flow preserved: an exact phone match surfaces the card."""
        self.client.force_login(self.secretary_b)
        self.assertEqual(self._card(self.outsider.id, q=self.outsider.phone).status_code, 200)

    def test_card_ok_with_exact_national_id(self):
        self.client.force_login(self.secretary_b)
        self.assertEqual(
            self._card(self.outsider.id, q=self.outsider.national_id).status_code, 200,
        )

    def test_card_ok_for_own_clinic_patient(self):
        """secretary_a reaches their own registered patient with no query."""
        self.client.force_login(self.secretary_a)
        self.assertEqual(self._card(self.patient_a.id).status_code, 200)

    def test_walkin_partial_hides_foreign_patient(self):
        """Walk-in future-appointments partial must not echo a non-clinic patient's name."""
        self.client.force_login(self.secretary_b)
        resp = self.client.get(
            reverse("secretary:walkin_patient_appointments_htmx"),
            {"patient_id": self.patient_a.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.strip(), b"")
        self.assertNotContains(resp, self.patient_a.name)
