"""
Doctor-side tests for the clinical-note secretary toggle + printable note view.
"""

from django.urls import reverse

from patients.models import ClinicPatient, ClinicalNote

from doctors.test_views import DoctorViewTestBase


class DoctorClinicalNotePrintTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        # Doctor A treats Patient A in Clinic A.
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)
        self.client.force_login(self.doctor_a)

    def test_note_add_persists_secretary_toggle_on(self):
        self.client.post(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]),
            {"clinic_id": self.clinic_a.id, "subjective": "complaint",
             "is_secretary_allowed": "on"},
        )
        note = ClinicalNote.objects.get(patient=self.patient_a)
        self.assertTrue(note.is_secretary_allowed)

    def test_note_add_unchecked_yields_false(self):
        # When the doctor unchecks the toggle, the checkbox is absent from POST.
        self.client.post(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]),
            {"clinic_id": self.clinic_a.id, "subjective": "complaint"},
        )
        note = ClinicalNote.objects.get(patient=self.patient_a)
        self.assertFalse(note.is_secretary_allowed)

    def test_new_note_forms_default_toggle_checked(self):
        import re
        pattern = re.compile(r'name="is_secretary_allowed"\s+checked')
        for tab in ("overview", "notes"):
            resp = self.client.get(
                reverse("doctors:patient_workspace", args=[self.patient_a.id]),
                {"tab": tab},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertRegex(resp.content.decode(), pattern,
                             msg=f"toggle not default-checked on '{tab}' tab")

    def test_edit_form_reflects_unchecked_state(self):
        # A note the doctor opted out of must render its toggle UNchecked on edit.
        import re
        note = ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            assessment="dx", is_secretary_allowed=False,
        )
        resp = self.client.get(
            reverse("doctors:ws_note_edit", args=[self.patient_a.id, note.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotRegex(resp.content.decode(),
                            re.compile(r'name="is_secretary_allowed"\s+checked'))

    def test_note_print_returns_200(self):
        note = ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            assessment="Diagnosis here",
        )
        resp = self.client.get(
            reverse("doctors:ws_note_print", args=[self.patient_a.id, note.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "doctors/clinical_note_print.html")
        self.assertContains(resp, "Diagnosis here")

    def test_overview_tab_shows_toggle_and_print(self):
        note = ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            assessment="dx", is_secretary_allowed=True,
        )
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]),
            {"tab": "overview"},
        )
        self.assertEqual(resp.status_code, 200)
        # New-note form carries the secretary toggle …
        self.assertContains(resp, 'name="is_secretary_allowed"')
        # … and each note exposes its print link.
        self.assertContains(
            resp, reverse("doctors:ws_note_print", args=[self.patient_a.id, note.id])
        )

    def test_note_print_blocks_other_clinic_doctor(self):
        note = ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            assessment="Private",
        )
        self.client.force_login(self.doctor_b)
        resp = self.client.get(
            reverse("doctors:ws_note_print", args=[self.patient_a.id, note.id])
        )
        # doctor_b shares no clinic with patient_a → forbidden by _ws_access.
        self.assertIn(resp.status_code, (403, 404))
