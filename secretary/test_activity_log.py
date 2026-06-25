"""
Tests for the secretary-portal audit trail (clinics.models.ActivityLog).

Verifies that each audited operation emits exactly one ActivityLog row with the
right action / target / actor / metadata, that failed operations emit nothing,
and that sensitive clinical-note reads are recorded only when a note is shown.
"""

from datetime import time
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from clinics.models import ActivityLog
from patients.models import ClinicPatient, ClinicalNote

from secretary import billing
from secretary.services import (
    secretary_book_appointment,
    transition_appointment_status,
)
from secretary.tests import SecretaryTestBase
from appointments.services.booking_service import BookingError


class ActivityLogServiceTests(SecretaryTestBase):
    """Service-layer emit points (booking, status transitions, billing)."""

    def test_book_appointment_logs_created(self):
        appt = secretary_book_appointment(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
            created_by=self.secretary_a,
            ip="203.0.113.5",
        )
        log = ActivityLog.objects.get(action=ActivityLog.Action.APPOINTMENT_CREATED)
        self.assertEqual(log.actor, self.secretary_a)
        self.assertEqual(log.clinic, self.clinic_a)
        self.assertEqual(log.target_type, "Appointment")
        self.assertEqual(log.target_id, appt.id)
        self.assertEqual(log.ip, "203.0.113.5")
        self.assertFalse(log.metadata["is_walk_in"])

    def test_transition_logs_status_changed(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        transition_appointment_status(
            appt,
            Appointment.Status.CANCELLED,
            cancellation_reason="patient called",
            actor=self.secretary_a,
            ip="198.51.100.7",
        )
        log = ActivityLog.objects.get(action=ActivityLog.Action.APPOINTMENT_STATUS_CHANGED)
        self.assertEqual(log.target_id, appt.id)
        self.assertEqual(log.actor, self.secretary_a)
        self.assertEqual(log.ip, "198.51.100.7")
        self.assertEqual(log.metadata["from"], Appointment.Status.CONFIRMED)
        self.assertEqual(log.metadata["to"], Appointment.Status.CANCELLED)
        self.assertEqual(log.metadata["cancellation_reason"], "patient called")

    def test_invalid_transition_logs_nothing(self):
        # COMPLETED is terminal — the transition raises before any save/log.
        appt = self._make_appointment(status=Appointment.Status.COMPLETED)
        with self.assertRaises(BookingError):
            transition_appointment_status(
                appt, Appointment.Status.CONFIRMED, actor=self.secretary_a
            )
        self.assertEqual(ActivityLog.objects.count(), 0)

    def _checked_in_invoice(self):
        appt = self._make_appointment(status=Appointment.Status.CHECKED_IN)
        appt.checked_in_at = timezone.now()
        appt.save(update_fields=["checked_in_at"])
        return appt, billing.open_billing_session(appt, by_user=self.secretary_a, ip="192.0.2.1")

    def test_open_billing_session_logs(self):
        _, invoice = self._checked_in_invoice()
        log = ActivityLog.objects.get(action=ActivityLog.Action.INVOICE_OPENED)
        self.assertEqual(log.target_id, invoice.id)
        self.assertEqual(log.metadata["invoice_number"], invoice.invoice_number)
        self.assertEqual(log.ip, "192.0.2.1")

    def test_record_payment_logs(self):
        _, invoice = self._checked_in_invoice()
        billing.record_payment(
            primary_invoice=invoice,
            amount=Decimal("50.00"),
            method="CASH",
            by_user=self.secretary_a,
            ip="192.0.2.9",
        )
        log = ActivityLog.objects.get(action=ActivityLog.Action.PAYMENT_RECORDED)
        self.assertEqual(log.actor, self.secretary_a)
        self.assertEqual(log.metadata["amount"], "50.00")
        self.assertEqual(log.metadata["method"], "CASH")
        self.assertEqual(log.ip, "192.0.2.9")

    def test_delete_invoice_logs_with_number_preserved(self):
        _, invoice = self._checked_in_invoice()
        number = invoice.invoice_number
        invoice_id = invoice.id
        billing.delete_invoice(invoice, actor=self.secretary_a, ip="192.0.2.2")
        log = ActivityLog.objects.get(action=ActivityLog.Action.INVOICE_DELETED)
        # target_id + invoice_number survive even though the row is gone.
        self.assertEqual(log.target_id, invoice_id)
        self.assertEqual(log.metadata["invoice_number"], number)
        self.assertFalse(
            billing.Invoice.objects.filter(id=invoice_id).exists()
        )


class ActivityLogViewTests(SecretaryTestBase):
    """View-layer emit points (direct writes + sensitive reads)."""

    def setUp(self):
        super().setUp()
        self.cp = ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a,
        )
        self.client.force_login(self.secretary_a)

    def test_checkin_view_logs_status_changed_with_ip(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        resp = self.client.post(
            reverse("secretary:checkin_appointment", args=[appt.id])
        )
        self.assertEqual(resp.status_code, 302)
        log = ActivityLog.objects.get(action=ActivityLog.Action.APPOINTMENT_STATUS_CHANGED)
        self.assertEqual(log.target_id, appt.id)
        self.assertEqual(log.metadata["to"], "CHECKED_IN")
        self.assertEqual(log.actor, self.secretary_a)
        self.assertIsNotNone(log.ip)  # test client provides REMOTE_ADDR

    def test_edit_appointment_view_logs_reschedule(self):
        appt = self._make_appointment(
            status=Appointment.Status.CONFIRMED, appointment_time=time(10, 0)
        )
        resp = self.client.post(
            reverse("secretary:edit_appointment", args=[appt.id]),
            {
                "doctor_id": self.doctor_a.id,
                "appointment_type_id": self.appt_type_a.id,
                "appointment_date": self.next_monday.isoformat(),
                "appointment_time": "11:00",
                "reason": "moved",
            },
        )
        self.assertEqual(resp.status_code, 302)
        log = ActivityLog.objects.get(action=ActivityLog.Action.APPOINTMENT_RESCHEDULED)
        self.assertEqual(log.target_id, appt.id)
        self.assertEqual(log.metadata["new_time"], "11:00")
        self.assertEqual(log.metadata["old_time"], "10:00")

    def test_clinical_note_print_logs_view(self):
        ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            is_secretary_allowed=True, assessment="Hypertension",
        )
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 200)
        log = ActivityLog.objects.get(action=ActivityLog.Action.CLINICAL_NOTE_VIEWED)
        self.assertEqual(log.target_type, "ClinicalNote")
        self.assertEqual(log.metadata["via"], "print")
        self.assertEqual(log.actor, self.secretary_a)

    def test_clinical_note_print_forbidden_logs_nothing(self):
        ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            is_secretary_allowed=False, assessment="Secret",
        )
        resp = self.client.get(
            reverse("secretary:clinical_note_print", args=[self.patient_a.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            ActivityLog.objects.filter(
                action=ActivityLog.Action.CLINICAL_NOTE_VIEWED
            ).count(),
            0,
        )
