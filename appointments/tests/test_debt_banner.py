"""
Tests for the outstanding-debt reminder banner on the patient booking page.

The banner shows on appointments:book_appointment when the logged-in patient
owes the clinic finalized debt (any doctor), and stays hidden otherwise —
including when the only balance is an open (mid-visit) billing session or the
debt belongs to a different clinic. Rendered bilingually via {% blocktrans %}.
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from appointments.models import Appointment, AppointmentType
from clinics.models import Clinic, ClinicStaff

User = get_user_model()

BANNER_AR = "تذكير: يوجد عليك مبلغ مستحق"
BANNER_EN = "Reminder: you have an outstanding balance"


class DebtBannerTest(TestCase):
    """Two clinics so cross-clinic debt scoping can be asserted."""

    def setUp(self):
        # ── Clinic A ──────────────────────────────────────────────────
        self.owner_a = User.objects.create_user(
            phone="0599800001", password="pass1234",
            name="Dr. Owner A", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_a = Clinic.objects.create(
            name="Clinic A", address="Street 1",
            phone="0599800010", email="a@debttest.com",
            main_doctor=self.owner_a, is_active=True,
        )
        self.doctor_a = User.objects.create_user(
            phone="0599800002", password="pass1234",
            name="Dr. A", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=self.doctor_a, role="DOCTOR", is_active=True,
        )
        self.appt_type_a = AppointmentType.objects.create(
            clinic=self.clinic_a, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )

        # ── Clinic B (separate tenant) ────────────────────────────────
        self.owner_b = User.objects.create_user(
            phone="0599900001", password="pass1234",
            name="Dr. Owner B", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_b = Clinic.objects.create(
            name="Clinic B", address="Street 2",
            phone="0599900010", email="b@debttest.com",
            main_doctor=self.owner_b, is_active=True,
        )
        self.doctor_b = User.objects.create_user(
            phone="0599900002", password="pass1234",
            name="Dr. B", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_b, user=self.doctor_b, role="DOCTOR", is_active=True,
        )
        self.appt_type_b = AppointmentType.objects.create(
            clinic=self.clinic_b, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )

        # ── Patient ───────────────────────────────────────────────────
        self.patient = User.objects.create_user(
            phone="0599800003", password="pass1234",
            name="Patient Ali", role="PATIENT", roles=["PATIENT"],
        )

        today = date.today()
        self.next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    # ── Helpers ───────────────────────────────────────────────────────

    def _make_appointment(self, clinic, doctor, appt_type,
                          status=Appointment.Status.CHECKED_IN,
                          appointment_time=time(10, 0)):
        return Appointment.objects.create(
            patient=self.patient, clinic=clinic, doctor=doctor,
            appointment_type=appt_type,
            appointment_date=self.next_monday,
            appointment_time=appointment_time,
            status=status,
        )

    def _create_debt(self, clinic, doctor, appt_type):
        """Completed visit with an unpaid ₪50 session → finalized debt of 50."""
        from secretary import billing
        appt = self._make_appointment(clinic, doctor, appt_type)
        billing.open_billing_session(appt, by_user=doctor)
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(appt, Appointment.Status.COMPLETED)
        return appt

    def _get_booking_page(self, clinic):
        self.client.force_login(self.patient)
        return self.client.get(
            reverse("appointments:book_appointment", kwargs={"clinic_id": clinic.id})
        )

    # ── Tests ─────────────────────────────────────────────────────────

    def test_banner_shown_when_patient_owes_clinic(self):
        self._create_debt(self.clinic_a, self.doctor_a, self.appt_type_a)
        resp = self._get_booking_page(self.clinic_a)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, BANNER_AR)
        self.assertContains(resp, "50.00")

    def test_banner_hidden_when_no_debt(self):
        resp = self._get_booking_page(self.clinic_a)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, BANNER_AR)

    def test_banner_hidden_for_debt_at_other_clinic(self):
        self._create_debt(self.clinic_b, self.doctor_b, self.appt_type_b)
        resp = self._get_booking_page(self.clinic_a)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, BANNER_AR)

    def test_open_session_is_not_shown_as_debt(self):
        from secretary import billing
        appt = self._make_appointment(self.clinic_a, self.doctor_a, self.appt_type_a)
        billing.open_billing_session(appt, by_user=self.doctor_a)  # mid-visit bill
        resp = self._get_booking_page(self.clinic_a)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, BANNER_AR)

    def test_banner_english_rendering(self):
        self._create_debt(self.clinic_a, self.doctor_a, self.appt_type_a)
        self.patient.preferred_language = "en"
        self.patient.save(update_fields=["preferred_language"])
        resp = self._get_booking_page(self.clinic_a)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, BANNER_EN)
        self.assertContains(resp, "50.00")
