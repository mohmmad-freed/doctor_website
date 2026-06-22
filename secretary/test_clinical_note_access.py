"""
Tests for secretary access to clinical notes + the printable note view.

Rule under test: a secretary may read/print a clinical note only when BOTH
  (A) the doctor flagged it secretary-visible (is_secretary_allowed), AND
  (B) it is the latest clinical note for the patient in that clinic.
"""

from django.urls import reverse

from clinics.models import Clinic
from patients.models import ClinicPatient, ClinicalNote

from secretary.tests import SecretaryTestBase
from secretary.views import _secretary_visible_note


class SecretaryClinicalNoteAccessTests(SecretaryTestBase):

    def setUp(self):
        super().setUp()
        # Register patient_a in clinic_a so the secretary can reach them.
        self.cp = ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )

    def _note(self, *, allowed, clinic=None, doctor=None, **fields):
        return ClinicalNote.objects.create(
            patient=self.patient_a,
            clinic=clinic or self.clinic_a,
            doctor=doctor or self.doctor_a,
            is_secretary_allowed=allowed,
            **fields,
        )

    # ── model helper ────────────────────────────────────────────────────
    def test_is_latest_in_clinic(self):
        older = self._note(allowed=True, subjective="old")
        newer = self._note(allowed=False, subjective="new")
        self.assertFalse(older.is_latest_in_clinic())
        self.assertTrue(newer.is_latest_in_clinic())

    # ── access predicate ────────────────────────────────────────────────
    def test_visible_when_latest_and_allowed(self):
        note = self._note(allowed=True, assessment="dx")
        self.assertEqual(_secretary_visible_note(self.clinic_a, self.patient_a.id), note)

    def test_hidden_when_latest_not_allowed(self):
        self._note(allowed=False, assessment="dx")
        self.assertIsNone(_secretary_visible_note(self.clinic_a, self.patient_a.id))

    def test_hidden_when_allowed_note_is_superseded(self):
        # Allowed note, then a newer note (regardless of its flag) hides the older one.
        self._note(allowed=True, assessment="old dx")
        self._note(allowed=False, assessment="new dx")
        self.assertIsNone(_secretary_visible_note(self.clinic_a, self.patient_a.id))

    # ── print view ──────────────────────────────────────────────────────
    def test_print_allowed_latest_returns_200(self):
        self._note(allowed=True, assessment="Hypertension")
        self.client.force_login(self.secretary_a)
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hypertension")
        self.assertTemplateUsed(resp, "doctors/clinical_note_print.html")

    def test_print_forbidden_when_not_allowed(self):
        self._note(allowed=False, assessment="Secret")
        self.client.force_login(self.secretary_a)
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 403)

    def test_print_forbidden_when_superseded(self):
        self._note(allowed=True, assessment="Old")
        self._note(allowed=False, assessment="New")
        self.client.force_login(self.secretary_a)
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 403)

    def test_print_forbidden_for_other_clinic_secretary(self):
        # patient_a is not registered in clinic_b → secretary_b gets 404.
        self._note(allowed=True, assessment="dx")
        self.client.force_login(self.secretary_b)
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 404)

    def test_print_requires_secretary_role(self):
        self._note(allowed=True, assessment="dx")
        self.client.force_login(self.doctor_a)  # a doctor, not a secretary
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 403)

    # ── patient detail context ──────────────────────────────────────────
    def test_patient_detail_exposes_latest_note_only_when_allowed(self):
        self.client.force_login(self.secretary_a)
        url = reverse("secretary:patient_detail", args=[self.patient_a.id])

        # No note yet
        self.assertIsNone(self.client.get(url).context["latest_clinical_note"])

        # Allowed latest note → present
        note = self._note(allowed=True, assessment="dx")
        self.assertEqual(self.client.get(url).context["latest_clinical_note"], note)

        # Newer disallowed note supersedes → hidden again
        self._note(allowed=False, assessment="newer")
        self.assertIsNone(self.client.get(url).context["latest_clinical_note"])
