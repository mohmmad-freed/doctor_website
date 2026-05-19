"""
Tests for the secretary-side compliance control, blocked-patient banner/filter/
unblock, and the new-patient request flow (unregistered booking → pending →
accept/reject).

Reuses the SecretaryTestBase fixture (clinic_a + secretary_a + doctor_a +
patient_a, plus an isolated clinic_b).
"""

from datetime import time

from django.urls import reverse

from appointments.models import Appointment
from appointments.services.booking_service import book_appointment, BookingError
from compliance.models import (
    ClinicComplianceSettings,
    PatientClinicCompliance,
    ComplianceEvent,
)
from compliance.services.compliance_service import (
    count_blocked_patients,
    process_appointment_no_show,
)
from patients.models import ClinicPatient
from patients.services import ensure_patient_profile

from secretary.tests import SecretaryTestBase


class _Base(SecretaryTestBase):
    def _block_patient(self, user, clinic, score=3):
        profile, _ = ensure_patient_profile(user)
        comp, _ = PatientClinicCompliance.objects.get_or_create(
            clinic=clinic, patient=profile
        )
        comp.bad_score = score
        comp.status = "BLOCKED"
        comp.save()
        return comp

    def _register(self, user, clinic):
        return ClinicPatient.objects.create(
            clinic=clinic, patient=user, registered_by=self.secretary_a
        )


# ════════════════════════════════════════════════════════════════════
#  count_blocked_patients + isolation
# ════════════════════════════════════════════════════════════════════

class CountBlockedPatientsTests(_Base):
    def test_count_and_clinic_isolation(self):
        self.assertEqual(count_blocked_patients(self.clinic_a), 0)
        self._block_patient(self.patient_a, self.clinic_a)
        self.assertEqual(count_blocked_patients(self.clinic_a), 1)
        # The same patient is NOT blocked in clinic_b.
        self.assertEqual(count_blocked_patients(self.clinic_b), 0)


# ════════════════════════════════════════════════════════════════════
#  Secretary compliance settings
# ════════════════════════════════════════════════════════════════════

class SecretaryComplianceSettingsTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.secretary_a)
        self.url = reverse("secretary:settings_clinic")

    def test_save_valid_compliance_settings(self):
        resp = self.client.post(self.url, {
            "form_section": "compliance",
            "max_no_show_count": "5",
            "forgiveness_enabled": "on",
            "forgiveness_days": "14",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        s = ClinicComplianceSettings.objects.get(clinic=self.clinic_a)
        self.assertEqual(s.score_threshold_block, 5)
        self.assertTrue(s.auto_forgive_enabled)
        self.assertEqual(s.auto_forgive_after_days, 14)

    def test_invalid_forgiveness_is_rejected_and_unchanged(self):
        before = ClinicComplianceSettings.objects.get(clinic=self.clinic_a)
        before_threshold = before.score_threshold_block
        resp = self.client.post(self.url, {
            "form_section": "compliance",
            "max_no_show_count": "9",
            "forgiveness_enabled": "on",
            # forgiveness_days intentionally missing → invalid combination
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        after = ClinicComplianceSettings.objects.get(clinic=self.clinic_a)
        self.assertEqual(after.score_threshold_block, before_threshold)
        self.assertFalse(after.auto_forgive_enabled)

    def test_booking_section_still_works(self):
        resp = self.client.post(self.url, {
            "form_section": "booking",
            "auto_confirm_patient_bookings": "1",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        bs = self.clinic_a.get_or_create_booking_settings()
        self.assertTrue(bs.auto_confirm_patient_bookings)


# ════════════════════════════════════════════════════════════════════
#  Blocked patient: list filter + manual unblock
# ════════════════════════════════════════════════════════════════════

class BlockedPatientListTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.secretary_a)
        self.cp = self._register(self.patient_a, self.clinic_a)
        self._block_patient(self.patient_a, self.clinic_a)

    def test_blocked_filter_shows_only_blocked(self):
        url = reverse("secretary:patient_list")
        resp = self.client.get(url, {"filter": "blocked"})
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context["clinic_patients"])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_blocked)
        self.assertEqual(resp.context["blocked_count"], 1)

    def test_htmx_endpoint_honors_blocked_filter(self):
        url = reverse("secretary:patient_list_htmx")
        resp = self.client.get(url, {"filter": "blocked"})
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context["clinic_patients"])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_blocked)

    def test_remove_block_resets_and_logs_event(self):
        url = reverse("secretary:remove_patient_block", args=[self.patient_a.id])
        resp = self.client.post(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        profile = self.patient_a.patient_profile
        comp = PatientClinicCompliance.objects.get(
            clinic=self.clinic_a, patient=profile
        )
        self.assertEqual(comp.status, "OK")
        self.assertEqual(comp.bad_score, 0)
        self.assertTrue(ComplianceEvent.objects.filter(
            clinic=self.clinic_a, patient=profile, event_type="MANUAL_WAIVER"
        ).exists())

    def test_remove_block_rejects_get(self):
        url = reverse("secretary:remove_patient_block", args=[self.patient_a.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_remove_block_cross_clinic_forbidden(self):
        self.client.force_login(self.secretary_b)
        url = reverse("secretary:remove_patient_block", args=[self.patient_a.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 404)
        # Still blocked.
        comp = PatientClinicCompliance.objects.get(
            clinic=self.clinic_a, patient=self.patient_a.patient_profile
        )
        self.assertEqual(comp.status, "BLOCKED")


# ════════════════════════════════════════════════════════════════════
#  Blocked patient cannot book (bilingual message via gettext_lazy)
# ════════════════════════════════════════════════════════════════════

class BlockedBookingTests(_Base):
    def test_blocked_patient_cannot_book(self):
        self._block_patient(self.patient_a, self.clinic_a)
        with self.assertRaises(BookingError) as ctx:
            book_appointment(
                patient=self.patient_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                appointment_date=self.next_monday,
                appointment_time=time(10, 0),
            )
        self.assertEqual(ctx.exception.code, "patient_blocked")


# ════════════════════════════════════════════════════════════════════
#  New-patient request flow
# ════════════════════════════════════════════════════════════════════

class NewPatientRequestTests(_Base):
    def test_unregistered_booking_is_pending_even_with_auto_confirm(self):
        bs = self.clinic_a.get_or_create_booking_settings()
        self.assertTrue(bs.auto_confirm_patient_bookings)  # default
        appt = book_appointment(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
        )
        self.assertEqual(appt.status, Appointment.Status.PENDING)
        self.assertFalse(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.patient_a
            ).exists()
        )

    def test_appointments_list_new_patient_filter(self):
        appt = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=self.next_monday,
            appointment_time=time(11, 0), status=Appointment.Status.PENDING,
            created_by=self.patient_a,
        )
        self.client.force_login(self.secretary_a)
        resp = self.client.get(
            reverse("secretary:appointments"), {"status": "new_patient"}
        )
        self.assertEqual(resp.status_code, 200)
        ids = [a.id for a in resp.context["appointments"]]
        self.assertIn(appt.id, ids)

    def test_accept_registers_patient_and_confirms(self):
        appt = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=self.next_monday,
            appointment_time=time(12, 0), status=Appointment.Status.PENDING,
            created_by=self.patient_a,
        )
        self.client.force_login(self.secretary_a)
        url = reverse("secretary:accept_new_patient_request", args=[appt.id])
        resp = self.client.post(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)
        cp = ClinicPatient.objects.get(
            clinic=self.clinic_a, patient=self.patient_a
        )
        self.assertTrue(cp.file_number)

        # Idempotent: a second accept does not error or duplicate the row.
        self.client.post(url, follow=True)
        self.assertEqual(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.patient_a
            ).count(),
            1,
        )

    def test_reject_cancels_without_registering(self):
        appt = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=self.next_monday,
            appointment_time=time(13, 0), status=Appointment.Status.PENDING,
            created_by=self.patient_a,
        )
        self.client.force_login(self.secretary_a)
        url = reverse("secretary:reject_new_patient_request", args=[appt.id])
        resp = self.client.post(url, {"cancellation_reason": "spam"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)
        self.assertFalse(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.patient_a
            ).exists()
        )

    def test_unaccepted_request_is_not_penalized_as_no_show(self):
        appt = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=self.next_monday,
            appointment_time=time(14, 0), status=Appointment.Status.PENDING,
            created_by=self.patient_a,
        )
        ensure_patient_profile(self.patient_a)
        process_appointment_no_show(appt)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.PENDING)
        self.assertFalse(
            ComplianceEvent.objects.filter(
                clinic=self.clinic_a, event_type="NO_SHOW"
            ).exists()
        )
        self.assertEqual(count_blocked_patients(self.clinic_a), 0)
