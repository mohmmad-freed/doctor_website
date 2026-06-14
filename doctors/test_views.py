"""
HTTP-level view tests for doctors/views.py.

Covers:
- appointments_list: access control, tenant isolation, patient filter
- appointment_detail: IDOR protection, status transition enforcement
- patients_list: access control, data isolation
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, AppointmentType
from clinics.models import Clinic, ClinicStaff
from doctors.models import DoctorVerification

User = get_user_model()


# ════════════════════════════════════════════════════════════════════
#  Shared Base
# ════════════════════════════════════════════════════════════════════

class DoctorViewTestBase(TestCase):
    """
    Two independent doctors at two different clinics.
    Used to verify cross-doctor data isolation.
    """

    def setUp(self):
        # ── Clinic / Doctor A ─────────────────────────────────────────
        self.main_doc_a = User.objects.create_user(
            phone="0591300001", password="pass1234",
            name="Main Doc A", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_a = Clinic.objects.create(
            name="Clinic A", address="St 1",
            phone="0591300010", email="a@vtest.com",
            main_doctor=self.main_doc_a, is_active=True,
        )
        self.doctor_a = User.objects.create_user(
            phone="0591300002", password="pass1234",
            name="Dr. A", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=self.doctor_a, role="DOCTOR", is_active=True,
        )
        DoctorVerification.objects.create(
            user=self.doctor_a, identity_status="IDENTITY_VERIFIED",
        )

        # ── Clinic / Doctor B (separate tenant) ───────────────────────
        self.main_doc_b = User.objects.create_user(
            phone="0591400001", password="pass1234",
            name="Main Doc B", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_b = Clinic.objects.create(
            name="Clinic B", address="St 2",
            phone="0591400010", email="b@vtest.com",
            main_doctor=self.main_doc_b, is_active=True,
        )
        self.doctor_b = User.objects.create_user(
            phone="0591400002", password="pass1234",
            name="Dr. B", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_b, user=self.doctor_b, role="DOCTOR", is_active=True,
        )

        # ── Patients ───────────────────────────────────────────────────
        self.patient_a = User.objects.create_user(
            phone="0591300003", password="pass1234",
            name="Patient A", role="PATIENT", roles=["PATIENT"],
        )
        self.patient_b = User.objects.create_user(
            phone="0591400003", password="pass1234",
            name="Patient B", role="PATIENT", roles=["PATIENT"],
        )

        # ── Appointment Types ──────────────────────────────────────────
        self.appt_type_a = AppointmentType.objects.create(
            clinic=self.clinic_a, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )
        self.appt_type_b = AppointmentType.objects.create(
            clinic=self.clinic_b, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )

        # ── Next Monday ───────────────────────────────────────────────
        today = date.today()
        days_ahead = -today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        self.next_monday = today + timedelta(days=days_ahead)

    def _make_appt(self, doctor, clinic, patient, appt_type,
                   appt_time=time(9, 0), status=Appointment.Status.CONFIRMED):
        from patients.models import ClinicPatient
        # Production booking guarantees a ClinicPatient row exists; mirror that
        # invariant so views that source from ClinicPatient see the patient.
        ClinicPatient.objects.get_or_create(patient=patient, clinic=clinic)
        return Appointment.objects.create(
            patient=patient, clinic=clinic, doctor=doctor,
            appointment_type=appt_type,
            appointment_date=self.next_monday,
            appointment_time=appt_time,
            status=status,
        )


# ════════════════════════════════════════════════════════════════════
#  7C-1 — Doctor Appointments List
# ════════════════════════════════════════════════════════════════════

class DoctorAppointmentsListTests(DoctorViewTestBase):

    def test_requires_login(self):
        resp = self.client.get(reverse("doctors:appointments"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_non_doctor_blocked(self):
        """Middleware returns 403 for patients accessing staff-only routes."""
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("doctors:appointments"))
        self.assertEqual(resp.status_code, 403)

    def test_sees_only_own_appointments(self):
        appt_a = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        appt_b = self._make_appt(self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointments"))
        self.assertEqual(resp.status_code, 200)
        ids = [a.id for a in resp.context["appointments"]]
        self.assertIn(appt_a.id, ids)
        self.assertNotIn(appt_b.id, ids)

    def test_status_filter_applied(self):
        confirmed = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a, time(9, 0),
            status=Appointment.Status.CONFIRMED,
        )
        cancelled = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a, time(10, 0),
            status=Appointment.Status.CANCELLED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointments") + "?status=CONFIRMED")
        ids = [a.id for a in resp.context["appointments"]]
        self.assertIn(confirmed.id, ids)
        self.assertNotIn(cancelled.id, ids)

    def test_patient_filter_isolates_single_patient(self):
        appt_a = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a, time(9, 0),
        )
        patient_x = User.objects.create_user(
            phone="0591300099", password="pass1234",
            name="Patient X", role="PATIENT", roles=["PATIENT"],
        )
        appt_x = self._make_appt(
            self.doctor_a, self.clinic_a, patient_x, self.appt_type_a, time(10, 0),
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:appointments") + f"?patient_id={self.patient_a.id}"
        )
        ids = [a.id for a in resp.context["appointments"]]
        self.assertIn(appt_a.id, ids)
        self.assertNotIn(appt_x.id, ids)


# ════════════════════════════════════════════════════════════════════
#  7C-2 — Doctor Appointment Detail
# ════════════════════════════════════════════════════════════════════

class DoctorAppointmentDetailTests(DoctorViewTestBase):

    def test_requires_login(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        resp = self.client.get(reverse("doctors:appointment_detail", args=[appt.id]))
        self.assertEqual(resp.status_code, 302)

    def test_idor_another_doctors_appointment_returns_404(self):
        """Doctor A must get 404 when accessing Doctor B's appointment."""
        appt_b = self._make_appt(self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_detail", args=[appt_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_valid_transition_confirmed_to_checked_in(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:appointment_detail", args=[appt.id]),
            {"status": "CHECKED_IN", "notes": ""},
        )
        self.assertRedirects(
            resp, reverse("doctors:appointment_detail", args=[appt.id]),
            fetch_redirect_response=False,
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)

    def test_invalid_transition_confirmed_to_completed_rejected(self):
        """Skipping CHECKED_IN and IN_PROGRESS is not allowed."""
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:appointment_detail", args=[appt.id]),
            {"status": "COMPLETED", "notes": ""},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    def test_tampered_status_value_rejected(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:appointment_detail", args=[appt.id]),
            {"status": "FABRICATED_STATUS", "notes": ""},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    def test_completed_appointment_has_no_transitions(self):
        appt = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a,
            status=Appointment.Status.COMPLETED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_detail", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["allowed_transitions"], [])

    def test_cancelled_appointment_has_no_transitions(self):
        appt = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a,
            status=Appointment.Status.CANCELLED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_detail", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["allowed_transitions"], [])

    def test_notes_saved_on_valid_transition(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:appointment_detail", args=[appt.id]),
            {"status": "CHECKED_IN", "notes": "Patient arrived on time."},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.notes, "Patient arrived on time.")

    def test_back_url_follows_next_param(self):
        """The back button returns to the page the doctor came from (the overview)."""
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        overview = reverse("doctors:appointment_overview", args=[appt.id])
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:appointment_detail", args=[appt.id]) + f"?next={overview}"
        )
        self.assertEqual(resp.context["back_url"], overview)
        self.assertEqual(resp.context["next_url"], overview)

    def test_back_url_defaults_to_list_without_next(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_detail", args=[appt.id]))
        self.assertEqual(resp.context["back_url"], reverse("doctors:appointments"))
        self.assertIsNone(resp.context["next_url"])

    def test_back_url_rejects_external_next(self):
        """An off-site next is ignored, falling back to the appointments list."""
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:appointment_detail", args=[appt.id]) + "?next=https://evil.example.com"
        )
        self.assertEqual(resp.context["back_url"], reverse("doctors:appointments"))
        self.assertIsNone(resp.context["next_url"])

    def test_status_post_preserves_next(self):
        """After a status change the redirect keeps `next` so back still works."""
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        overview = reverse("doctors:appointment_overview", args=[appt.id])
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:appointment_detail", args=[appt.id]),
            {"status": "CHECKED_IN", "notes": "", "next": overview},
        )
        self.assertIn("next=", resp.url)


# ════════════════════════════════════════════════════════════════════
#  7C-2b — Doctor Appointment Overview (patient-scoped notification target)
# ════════════════════════════════════════════════════════════════════

class DoctorAppointmentOverviewTests(DoctorViewTestBase):

    def _make_appt_on(self, the_date, appt_time=time(9, 0),
                      status=Appointment.Status.CONFIRMED, patient=None):
        from patients.models import ClinicPatient
        patient = patient or self.patient_a
        ClinicPatient.objects.get_or_create(patient=patient, clinic=self.clinic_a)
        return Appointment.objects.create(
            patient=patient, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=the_date, appointment_time=appt_time, status=status,
        )

    def test_requires_login(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        resp = self.client.get(reverse("doctors:appointment_overview", args=[appt.id]))
        self.assertEqual(resp.status_code, 302)

    def test_idor_another_doctors_appointment_returns_404(self):
        appt_b = self._make_appt(self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_overview", args=[appt_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_renders_focused_appointment_and_patient(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_overview", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["appointment"].id, appt.id)
        self.assertEqual(resp.context["patient"].id, self.patient_a.id)

    def test_splits_upcoming_and_past_excluding_focused(self):
        focused = self._make_appt_on(self.next_monday, time(9, 0))
        upcoming_other = self._make_appt_on(self.next_monday + timedelta(days=7), time(9, 0))
        past_other = self._make_appt_on(
            date.today() - timedelta(days=7), time(9, 0),
            status=Appointment.Status.COMPLETED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_overview", args=[focused.id]))
        upcoming_ids = [a.id for a in resp.context["upcoming"]]
        past_ids = [a.id for a in resp.context["past"]]
        self.assertEqual(upcoming_ids, [upcoming_other.id])
        self.assertEqual(past_ids, [past_other.id])
        # Focused appointment never appears in either timeline list.
        self.assertNotIn(focused.id, upcoming_ids)
        self.assertNotIn(focused.id, past_ids)

    def test_only_this_patients_appointments_listed(self):
        focused = self._make_appt_on(self.next_monday, time(9, 0), patient=self.patient_a)
        other_patient_appt = self._make_appt_on(
            self.next_monday, time(11, 0), patient=self.patient_b
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_overview", args=[focused.id]))
        all_listed = [a.id for a in resp.context["upcoming"]] + [a.id for a in resp.context["past"]]
        self.assertNotIn(other_patient_appt.id, all_listed)

    def test_status_transition_updates_and_redirects(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:appointment_overview", args=[appt.id]),
            {"status": "CHECKED_IN", "notes": "Arrived."},
        )
        self.assertRedirects(
            resp, reverse("doctors:appointment_overview", args=[appt.id]),
            fetch_redirect_response=False,
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)
        self.assertEqual(appt.notes, "Arrived.")

    def test_tampered_status_value_rejected(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:appointment_overview", args=[appt.id]),
            {"status": "FABRICATED", "notes": ""},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    def test_check_in_stamps_arrival_time_and_queue_position(self):
        """Checking in from the overview must set checked_in_at + queue_priority so the
        patient appears with an arrival time in the secretary waiting-room queue."""
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.assertIsNone(appt.checked_in_at)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:appointment_overview", args=[appt.id]),
            {"status": "CHECKED_IN", "notes": ""},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)
        self.assertIsNotNone(appt.checked_in_at)
        self.assertIsNotNone(appt.queue_priority)

    def test_intake_partial_returns_form_for_owner(self):
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_intake_partial", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)

    def test_intake_partial_idor_404(self):
        appt_b = self._make_appt(self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_intake_partial", args=[appt_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_doctor_notification_link_redirects_to_overview(self):
        """Opening a DOCTOR notification lands on the appointment overview page."""
        from appointments.models import AppointmentNotification
        appt = self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        notif = AppointmentNotification.objects.create(
            patient=self.doctor_a,
            appointment=appt,
            context_role=AppointmentNotification.ContextRole.DOCTOR,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="حجز جديد", message="msg",
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("appointments:open_notification", args=[notif.pk]))
        self.assertRedirects(
            resp, reverse("doctors:appointment_overview", args=[appt.id]),
            fetch_redirect_response=False,
        )


# ════════════════════════════════════════════════════════════════════
#  7C-3 — Doctor Patients List
# ════════════════════════════════════════════════════════════════════

class DoctorPatientsListTests(DoctorViewTestBase):

    def test_requires_login(self):
        resp = self.client.get(reverse("doctors:patients"))
        self.assertEqual(resp.status_code, 302)

    def test_non_doctor_blocked(self):
        """Middleware returns 403 for patients accessing staff-only routes."""
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("doctors:patients"))
        self.assertEqual(resp.status_code, 403)

    def test_shows_only_own_patients(self):
        """Doctor A must not see patients only treated by Doctor B."""
        self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a)
        self._make_appt(self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:patients"))
        self.assertEqual(resp.status_code, 200)
        patient_ids = [p["patient_id"] for p in resp.context["patient_page"].object_list]
        self.assertIn(self.patient_a.id, patient_ids)
        self.assertNotIn(self.patient_b.id, patient_ids)

    def test_visit_count_aggregated_correctly(self):
        """Multiple appointments for same patient should aggregate correctly."""
        self._make_appt(self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a, time(9, 0))
        self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a, time(10, 0),
            status=Appointment.Status.COMPLETED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:patients"))
        stats = {p["patient_id"]: p for p in resp.context["patient_page"].object_list}
        self.assertEqual(stats[self.patient_a.id]["total_visits"], 2)
