"""
F1 / F2 regression: access-controlled serving + validation of uploaded patient files.

Threat model: uploaded medical records and intake attachments hold PHI. They must
NOT be reachable by a public ``/media/`` URL — only through the authenticated
download views, gated (via ``clinics.access.user_can_access_clinic_file``) to the
owning patient or an active staff member of the file's clinic. Cross-clinic staff
and anonymous users are denied.

Also covers F2: the medical-record upload view creates rows with
``objects.create()`` (which bypasses model-field validators), so it must re-run
extension + magic-byte signature validation itself and reject a file whose bytes
aren't a real allowed type even when the extension lies.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from appointments.models import AppointmentAttachment
from patients.models import ClinicPatient, MedicalRecord
from secretary.tests import SecretaryTestBase

User = get_user_model()

# Minimal valid 1x1 PNG — real signature, so it passes magic-byte sniffing.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000154a24f0e0000000049454e44ae426082"
)

# Uploads land in a throwaway dir so the real media/ tree is never touched and
# Windows file-handle locks can't block cleanup of individual files.
_MEDIA = tempfile.mkdtemp(prefix="test_protected_media_")


@override_settings(MEDIA_ROOT=_MEDIA)
class ProtectedMediaAccessTests(SecretaryTestBase):
    """Only the owner or active clinic staff may download a record / attachment."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )
        appt = self._make_appointment(clinic=self.clinic_a)
        self.attachment = AppointmentAttachment.objects.create(
            appointment=appt,
            file=SimpleUploadedFile("lab.png", _PNG_BYTES, content_type="image/png"),
            original_name="lab.png", file_size=len(_PNG_BYTES), mime_type="image/png",
            uploaded_by=self.secretary_a,
        )
        self.record = MedicalRecord.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, uploaded_by=self.doctor_a,
            title="CBC",
            file=SimpleUploadedFile("cbc.png", _PNG_BYTES, content_type="image/png"),
            original_name="cbc.png", file_size=len(_PNG_BYTES),
        )
        self.att_url = reverse("appointments:download_attachment", args=[self.attachment.id])
        self.rec_url = reverse("patients:download_medical_record", args=[self.record.id])

    def _urls(self):
        return (self.att_url, self.rec_url)

    def test_owning_patient_can_download(self):
        self.client.force_login(self.patient_a)
        for url in self._urls():
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)

    def test_clinic_staff_can_download_with_nosniff(self):
        self.client.force_login(self.secretary_a)
        for url in self._urls():
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            self.assertEqual(resp["X-Content-Type-Options"], "nosniff", url)

    def test_cross_clinic_staff_denied(self):
        # secretary_b is active staff of clinic B only — must not reach clinic A files.
        self.client.force_login(self.secretary_b)
        for url in self._urls():
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_revoked_staff_denied(self):
        self.staff_a.revoked_at = self.attachment.uploaded_at
        self.staff_a.save(update_fields=["revoked_at"])
        self.client.force_login(self.secretary_a)
        for url in self._urls():
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_anonymous_redirected_to_login(self):
        for url in self._urls():
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("login", resp["Location"].lower(), url)


@override_settings(MEDIA_ROOT=_MEDIA)
class MedicalRecordUploadValidationTests(SecretaryTestBase):
    """F2: upload view must reject spoofed/disallowed content, accept real images."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )
        self.url = reverse("doctors:ws_record_upload", args=[self.patient_a.id])

    def test_html_renamed_pdf_is_rejected(self):
        self.client.force_login(self.doctor_a)
        evil = SimpleUploadedFile(
            "x.pdf", b"<html><script>alert(1)</script></html>",
            content_type="application/pdf",
        )
        resp = self.client.post(self.url, {
            "clinic_id": str(self.clinic_a.id), "title": "evil",
            "category": "GENERAL", "record_file": evil,
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered partial carrying the error
        self.assertFalse(
            MedicalRecord.objects.filter(patient=self.patient_a).exists()
        )

    def test_valid_png_is_accepted(self):
        self.client.force_login(self.doctor_a)
        good = SimpleUploadedFile("scan.png", _PNG_BYTES, content_type="image/png")
        resp = self.client.post(self.url, {
            "clinic_id": str(self.clinic_a.id), "title": "scan",
            "category": "GENERAL", "record_file": good,
        })
        self.assertEqual(resp.status_code, 200)
        rec = MedicalRecord.objects.get(patient=self.patient_a)
        self.assertEqual(rec.original_name, "scan.png")
        # F4: stored path discards the raw client name (uuid.ext only).
        self.assertNotIn("scan", rec.file.name.rsplit("/", 1)[-1])
