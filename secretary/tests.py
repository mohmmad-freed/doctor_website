"""
Tests for the secretary module.

Covers:
- Secretary invitation flow (guest accept, inbox, accept, reject)
- Secretary appointment management (dashboard, list, create, edit, cancel)
- Access control & tenant isolation
- Permission enforcement
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, AppointmentType
from clinics.models import Clinic, ClinicStaff, ClinicInvitation
from doctors.models import DoctorAvailability, DoctorVerification

User = get_user_model()


# ════════════════════════════════════════════════════════════════════
#  Shared Test Base
# ════════════════════════════════════════════════════════════════════

class SecretaryTestBase(TestCase):
    """
    Minimal complete fixture for secretary view tests.

    Topology:
    - clinic_a     owned by main_doctor_a
    - secretary_a  active ClinicStaff(SECRETARY) at clinic_a
    - doctor_a     verified DOCTOR at clinic_a with Monday availability
    - patient_a    PATIENT user
    - clinic_b     separate clinic (different owner/secretary/doctor)
    """

    def setUp(self):
        # ── Clinic A ──────────────────────────────────────────────────
        self.main_doctor_a = User.objects.create_user(
            phone="0591100001", password="pass1234",
            name="Dr. Owner A", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_a = Clinic.objects.create(
            name="Clinic A", address="Street 1",
            phone="0591100010", email="clinica@test.com",
            main_doctor=self.main_doctor_a, is_active=True,
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=self.main_doctor_a, role="MAIN_DOCTOR",
        )

        # ── Secretary A ───────────────────────────────────────────────
        self.secretary_a = User.objects.create_user(
            phone="0591100002", password="pass1234",
            name="Secretary A", role="SECRETARY", roles=["SECRETARY"],
        )
        self.staff_a = ClinicStaff.objects.create(
            clinic=self.clinic_a, user=self.secretary_a,
            role="SECRETARY", is_active=True,
        )

        # ── Doctor A (verified) ────────────────────────────────────────
        self.doctor_a = User.objects.create_user(
            phone="0591100003", password="pass1234",
            name="Dr. Ahmad", role="DOCTOR", roles=["DOCTOR"],
        )
        self.doctor_staff_a = ClinicStaff.objects.create(
            clinic=self.clinic_a, user=self.doctor_a,
            role="DOCTOR", is_active=True,
        )
        DoctorVerification.objects.create(
            user=self.doctor_a, identity_status="IDENTITY_VERIFIED",
        )
        DoctorAvailability.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            day_of_week=0, start_time=time(9, 0), end_time=time(17, 0),
        )

        # ── Patient A ─────────────────────────────────────────────────
        self.patient_a = User.objects.create_user(
            phone="0591100004", password="pass1234",
            name="Patient Ali", role="PATIENT", roles=["PATIENT"],
        )

        # ── Appointment Type ──────────────────────────────────────────
        self.appt_type_a = AppointmentType.objects.create(
            clinic=self.clinic_a, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )

        # ── Clinic B (separate tenant) ────────────────────────────────
        self.main_doctor_b = User.objects.create_user(
            phone="0591200001", password="pass1234",
            name="Dr. Owner B", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic_b = Clinic.objects.create(
            name="Clinic B", address="Street 2",
            phone="0591200010", email="clinicb@test.com",
            main_doctor=self.main_doctor_b, is_active=True,
        )
        self.secretary_b = User.objects.create_user(
            phone="0591200002", password="pass1234",
            name="Secretary B", role="SECRETARY", roles=["SECRETARY"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_b, user=self.secretary_b,
            role="SECRETARY", is_active=True,
        )
        self.doctor_b = User.objects.create_user(
            phone="0591200003", password="pass1234",
            name="Dr. B", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_b, user=self.doctor_b,
            role="DOCTOR", is_active=True,
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

    def _make_appointment(self, clinic=None, status=Appointment.Status.CONFIRMED,
                          appointment_date=None, appointment_time=None):
        if clinic is None:
            clinic = self.clinic_a
        return Appointment.objects.create(
            patient=self.patient_a,
            clinic=clinic,
            doctor=self.doctor_a if clinic == self.clinic_a else self.doctor_b,
            appointment_type=self.appt_type_a if clinic == self.clinic_a else self.appt_type_b,
            appointment_date=appointment_date or self.next_monday,
            appointment_time=appointment_time or time(10, 0),
            status=status,
            created_by=self.secretary_a,
        )

    def _make_invitation(self, phone="0591100002"):
        return ClinicInvitation.objects.create(
            clinic=self.clinic_a,
            invited_by=self.main_doctor_a,
            doctor_name="Secretary A",
            doctor_phone=phone,
            doctor_email="sec@test.com",
            role="SECRETARY",
            status="PENDING",
            expires_at=timezone.now() + timedelta(days=7),
        )


# ════════════════════════════════════════════════════════════════════
#  7A — Secretary Invitation Flow
# ════════════════════════════════════════════════════════════════════

class SecretaryInvitationFlowTests(SecretaryTestBase):

    # ── Inbox ─────────────────────────────────────────────────────────

    def test_inbox_requires_login(self):
        resp = self.client.get(reverse("secretary:secretary_invitations_inbox"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_inbox_shows_only_own_invitations(self):
        inv = self._make_invitation("0591100002")
        # Invitation for a different phone — must not appear
        ClinicInvitation.objects.create(
            clinic=self.clinic_a, invited_by=self.main_doctor_a,
            doctor_name="Other", doctor_phone="0599999999",
            doctor_email="o@t.com", role="SECRETARY", status="PENDING",
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:secretary_invitations_inbox"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["invitations"].count(), 1)
        self.assertEqual(resp.context["invitations"].first().id, inv.id)

    # ── Accept ────────────────────────────────────────────────────────

    def test_accept_wrong_phone_shows_error_does_not_accept(self):
        """S-04: Secretary B cannot accept invitation addressed to Secretary A."""
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_b)
        resp = self.client.post(reverse("secretary:accept_invitation", args=[inv.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لا تملك الصلاحية")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "PENDING")

    def test_accept_correct_phone_creates_staff_record(self):
        self.staff_a.delete()  # remove existing so accept can recreate
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:accept_invitation", args=[inv.id]))
        self.assertRedirects(
            resp, reverse("secretary:secretary_invitations_inbox"),
            fetch_redirect_response=False,
        )
        inv.refresh_from_db()
        self.assertEqual(inv.status, "ACCEPTED")
        self.assertTrue(
            ClinicStaff.objects.filter(
                clinic=self.clinic_a, user=self.secretary_a, role="SECRETARY"
            ).exists()
        )

    # ── Reject ────────────────────────────────────────────────────────

    def test_reject_wrong_phone_shows_error_does_not_reject(self):
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_b)
        resp = self.client.post(reverse("secretary:reject_invitation", args=[inv.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لا تملك الصلاحية")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "PENDING")

    def test_reject_correct_phone_marks_rejected(self):
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:reject_invitation", args=[inv.id]))
        self.assertRedirects(
            resp, reverse("secretary:secretary_invitations_inbox"),
            fetch_redirect_response=False,
        )
        inv.refresh_from_db()
        self.assertEqual(inv.status, "REJECTED")

    # ── Guest accept ──────────────────────────────────────────────────

    def test_guest_accept_unauthenticated_stores_session_redirects_login(self):
        inv = self._make_invitation("0591100002")
        resp = self.client.get(
            reverse("secretary:guest_accept_invitation", args=[inv.token])
        )
        self.assertRedirects(resp, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("pending_invitation_app"), "secretary")
        self.assertEqual(
            str(self.client.session.get("pending_invitation_token")), str(inv.token)
        )

    def test_guest_accept_correct_phone_authenticated_redirects_inbox(self):
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_a)
        resp = self.client.get(
            reverse("secretary:guest_accept_invitation", args=[inv.token])
        )
        self.assertRedirects(
            resp, reverse("secretary:secretary_invitations_inbox"),
            fetch_redirect_response=False,
        )

    def test_guest_accept_wrong_phone_authenticated_shows_error(self):
        inv = self._make_invitation("0591100002")
        self.client.force_login(self.secretary_b)  # different phone
        resp = self.client.get(
            reverse("secretary:guest_accept_invitation", args=[inv.token])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لا تملك الصلاحية")

    def test_guest_accept_expired_shows_error(self):
        inv = ClinicInvitation.objects.create(
            clinic=self.clinic_a, invited_by=self.main_doctor_a,
            doctor_name="Secretary A", doctor_phone="0591100002",
            doctor_email="sec@test.com", role="SECRETARY", status="PENDING",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        resp = self.client.get(
            reverse("secretary:guest_accept_invitation", args=[inv.token])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "انتهت صلاحية")


# ════════════════════════════════════════════════════════════════════
#  7B — Secretary Appointment Management
# ════════════════════════════════════════════════════════════════════

class SecretaryAppointmentPermissionTests(SecretaryTestBase):
    """Verify access control on all appointment management endpoints."""

    def test_dashboard_non_secretary_gets_403(self):
        self.client.force_login(self.patient_a)
        self.assertEqual(self.client.get(reverse("secretary:dashboard")).status_code, 403)

    def test_list_non_secretary_gets_403(self):
        self.client.force_login(self.patient_a)
        self.assertEqual(self.client.get(reverse("secretary:appointments")).status_code, 403)

    def test_create_non_secretary_gets_403(self):
        self.client.force_login(self.patient_a)
        self.assertEqual(self.client.post(reverse("secretary:create_appointment"), {}).status_code, 403)

    def test_revoked_secretary_loses_dashboard_access(self):
        self.staff_a.is_active = False
        self.staff_a.revoked_at = timezone.now()
        self.staff_a.save()
        self.client.force_login(self.secretary_a)
        self.assertEqual(self.client.get(reverse("secretary:dashboard")).status_code, 403)

    def test_unauthenticated_gets_redirected_from_dashboard(self):
        self.assertEqual(self.client.get(reverse("secretary:dashboard")).status_code, 302)

    def test_unauthenticated_gets_redirected_from_appointments(self):
        self.assertEqual(self.client.get(reverse("secretary:appointments")).status_code, 302)


class SecretaryAppointmentTenantTests(SecretaryTestBase):
    """Verify tenant isolation: secretary A cannot touch clinic B data."""

    def test_list_only_shows_own_clinic(self):
        appt_a = self._make_appointment(self.clinic_a)
        appt_b = self._make_appointment(self.clinic_b)
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:appointments"))
        ids = [a.id for a in resp.context["appointments"]]
        self.assertIn(appt_a.id, ids)
        self.assertNotIn(appt_b.id, ids)

    def test_dashboard_only_shows_own_clinic_today(self):
        appt_a = self._make_appointment(self.clinic_a, appointment_date=date.today())
        appt_b = self._make_appointment(self.clinic_b, appointment_date=date.today())
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:dashboard"))
        ids = [a.id for a in resp.context["todays_appointments"]]
        self.assertIn(appt_a.id, ids)
        self.assertNotIn(appt_b.id, ids)

    def test_edit_other_clinic_appointment_returns_404(self):
        appt_b = self._make_appointment(self.clinic_b)
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:edit_appointment", args=[appt_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_cancel_other_clinic_appointment_does_not_cancel(self):
        appt_b = self._make_appointment(self.clinic_b)
        self.client.force_login(self.secretary_a)
        self.client.post(reverse("secretary:cancel_appointment", args=[appt_b.id]))
        appt_b.refresh_from_db()
        self.assertNotEqual(appt_b.status, Appointment.Status.CANCELLED)

    def test_create_with_doctor_from_other_clinic_rejected(self):
        """S-02: doctor_b belongs to clinic_b — secretary_a must not be able to use them."""
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:create_appointment"), {
            "patient_phone": "0591100004",
            "doctor_id": str(self.doctor_b.id),
            "appointment_type_id": str(self.appt_type_a.id),
            "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
            "appointment_time": "09:00",
        })
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())


class SecretaryAppointmentCRUDTests(SecretaryTestBase):
    """Functional correctness tests for create/edit/cancel."""

    def test_create_by_phone_books_appointment(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:create_appointment"), {
            "patient_phone": "0591100004",
            "doctor_id": str(self.doctor_a.id),
            "appointment_type_id": str(self.appt_type_a.id),
            "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
            "appointment_time": "09:00",
            "reason": "Secretary booking",
        })
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        self.assertEqual(appt.created_by, self.secretary_a)

    def test_create_unknown_phone_shows_error_no_appointment(self):
        self.client.force_login(self.secretary_a)
        self.client.post(reverse("secretary:create_appointment"), {
            "patient_phone": "0599999999",
            "doctor_id": str(self.doctor_a.id),
            "appointment_type_id": str(self.appt_type_a.id),
            "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
            "appointment_time": "09:00",
        })
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())

    def test_create_non_patient_user_shows_error(self):
        """S-01: secretary cannot book an appointment with a doctor as the patient."""
        self.client.force_login(self.secretary_a)
        self.client.post(reverse("secretary:create_appointment"), {
            "patient_phone": "0591100003",  # doctor_a
            "doctor_id": str(self.doctor_a.id),
            "appointment_type_id": str(self.appt_type_a.id),
            "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
            "appointment_time": "09:00",
        })
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())

    def test_edit_updates_appointment(self):
        appt = self._make_appointment(appointment_time=time(9, 0))
        next_week = self.next_monday + timedelta(days=7)
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:edit_appointment", args=[appt.id]),
            {
                "appointment_type_id": str(self.appt_type_a.id),
                "appointment_date": next_week.strftime("%Y-%m-%d"),
                "appointment_time": "10:00",
                "reason": "rescheduled",
            },
        )
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)
        appt.refresh_from_db()
        self.assertEqual(appt.appointment_date, next_week)
        self.assertEqual(appt.appointment_time, time(10, 0))

    def test_edit_past_date_rejected(self):
        appt = self._make_appointment()
        yesterday = date.today() - timedelta(days=1)
        self.client.force_login(self.secretary_a)
        self.client.post(
            reverse("secretary:edit_appointment", args=[appt.id]),
            {
                "appointment_type_id": str(self.appt_type_a.id),
                "appointment_date": yesterday.strftime("%Y-%m-%d"),
                "appointment_time": "09:00",
            },
        )
        appt.refresh_from_db()
        self.assertEqual(appt.appointment_date, self.next_monday)

    def test_edit_completed_appointment_redirects_with_error(self):
        appt = self._make_appointment(status=Appointment.Status.COMPLETED)
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:edit_appointment", args=[appt.id]))
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)

    def test_edit_checked_in_appointment_blocked(self):
        appt = self._make_appointment(status=Appointment.Status.CHECKED_IN)
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:edit_appointment", args=[appt.id]))
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)

    def test_edit_in_progress_appointment_blocked(self):
        appt = self._make_appointment(status=Appointment.Status.IN_PROGRESS)
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:edit_appointment", args=[appt.id]))
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)

    def test_edit_conflict_check_prevents_double_booking(self):
        """S-05: Moving an appointment to an already-occupied slot must fail."""
        appt1 = self._make_appointment(appointment_time=time(9, 0))
        appt2 = self._make_appointment(appointment_time=time(10, 0))
        self.client.force_login(self.secretary_a)
        self.client.post(
            reverse("secretary:edit_appointment", args=[appt2.id]),
            {
                "appointment_type_id": str(self.appt_type_a.id),
                "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
                "appointment_time": "09:00",  # conflicts with appt1
            },
        )
        appt2.refresh_from_db()
        self.assertEqual(appt2.appointment_time, time(10, 0))

    def test_cancel_own_clinic_appointment_succeeds(self):
        appt = self._make_appointment()
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:cancel_appointment", args=[appt.id]))
        self.assertRedirects(resp, reverse("secretary:appointments"), fetch_redirect_response=False)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)

    def test_cancel_via_get_does_not_cancel(self):
        """A GET request to cancel must not change appointment status."""
        appt = self._make_appointment()
        self.client.force_login(self.secretary_a)
        self.client.get(reverse("secretary:cancel_appointment", args=[appt.id]))
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)
