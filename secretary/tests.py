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

from appointments.models import Appointment, AppointmentType, AppointmentAnswer
from clinics.models import Clinic, ClinicStaff, ClinicInvitation
from doctors.models import (
    DoctorAvailability,
    DoctorVerification,
    DoctorIntakeFormTemplate,
    DoctorIntakeQuestion,
)

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
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        self.assertEqual(appt.created_by, self.secretary_a)
        # After booking, the view redirects to the new appointment's overview page
        # so the secretary gets immediate confirmation of what they just booked.
        self.assertRedirects(
            resp,
            reverse("secretary:appointment_overview", kwargs={"appointment_id": appt.id}),
            fetch_redirect_response=False,
        )

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
                "doctor_id": str(self.doctor_a.id),
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
                "doctor_id": str(self.doctor_a.id),
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
                "doctor_id": str(self.doctor_a.id),
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


# ════════════════════════════════════════════════════════════════════
#  Walk-In Registration
# ════════════════════════════════════════════════════════════════════

class WalkInRegistrationTests(SecretaryTestBase):
    """
    Walk-in flow: today/now, status CHECKED_IN, is_walk_in=True, immediately
    in the waiting-room queue. Walk-ins must not block (or be blocked by)
    booked appointments on the same slot.
    """

    def setUp(self):
        super().setUp()
        # Ensure doctor_a has availability for *today*'s weekday so
        # any same-day booking we test is plausible (not strictly required
        # by secretary_book_appointment, but mirrors realistic usage).
        DoctorAvailability.objects.get_or_create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            day_of_week=date.today().weekday(),
            defaults={"start_time": time(0, 0), "end_time": time(23, 59)},
        )

    # ── Service: register_walk_in ─────────────────────────────────────

    def test_register_walk_in_creates_checked_in_appointment_for_today(self):
        from secretary.services import register_walk_in
        appt = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.assertTrue(appt.is_walk_in)
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)
        self.assertEqual(appt.appointment_date, date.today())
        self.assertIsNotNone(appt.checked_in_at)
        self.assertEqual(appt.created_by, self.secretary_a)

    # ── Slot-conflict isolation ───────────────────────────────────────

    def test_walk_in_does_not_block_booked_appointment_at_same_slot(self):
        """A walk-in already in the queue must not prevent booking the same minute."""
        from secretary.services import register_walk_in, secretary_book_appointment
        register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        now_t = timezone.localtime().time().replace(second=0, microsecond=0)
        # Booking a regular appointment at the exact same slot should succeed.
        booked = secretary_book_appointment(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=date.today(),
            appointment_time=now_t,
            created_by=self.secretary_a,
        )
        self.assertEqual(booked.status, Appointment.Status.CONFIRMED)
        self.assertFalse(booked.is_walk_in)

    def test_two_walk_ins_at_same_minute_both_succeed(self):
        """Walk-ins don't reserve a slot, so two walk-ins for the same doctor
        at the same minute must both be created."""
        from secretary.services import register_walk_in
        a = register_walk_in(
            patient=self.patient_a, doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id, appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        # second patient
        patient_2 = User.objects.create_user(
            phone="0591100099", password="pass1234",
            name="Patient Two", role="PATIENT", roles=["PATIENT"],
        )
        b = register_walk_in(
            patient=patient_2, doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id, appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.assertNotEqual(a.id, b.id)
        self.assertTrue(a.is_walk_in and b.is_walk_in)

    def test_booked_appointment_blocks_other_booked_appointment(self):
        """Sanity: the conflict check still works for non-walk-in bookings."""
        from secretary.services import secretary_book_appointment
        from appointments.services.booking_service import SlotUnavailableError
        secretary_book_appointment(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
            created_by=self.secretary_a,
        )
        with self.assertRaises(SlotUnavailableError):
            secretary_book_appointment(
                patient=self.patient_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                appointment_date=self.next_monday,
                appointment_time=time(10, 0),
                created_by=self.secretary_a,
            )

    def test_booking_persists_split_notes(self):
        """Secretary booking stores a secretary-only note and a doctor-facing
        note in separate fields, leaving the doctor's own `notes` empty."""
        from secretary.services import secretary_book_appointment
        appt = secretary_book_appointment(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=self.next_monday,
            appointment_time=time(11, 0),
            secretary_note="for secretaries only",
            doctor_note="for the doctor",
            created_by=self.secretary_a,
        )
        appt.refresh_from_db()
        self.assertEqual(appt.secretary_note, "for secretaries only")
        self.assertEqual(appt.doctor_note, "for the doctor")
        self.assertEqual(appt.notes, "")

    def test_walk_in_persists_split_notes(self):
        from secretary.services import register_walk_in
        appt = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
            secretary_note="sec only",
            doctor_note="doc note",
        )
        appt.refresh_from_db()
        self.assertEqual(appt.secretary_note, "sec only")
        self.assertEqual(appt.doctor_note, "doc note")
        self.assertEqual(appt.notes, "")

    # ── View: register_walk_in ────────────────────────────────────────

    def test_walk_in_view_get_renders_template(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:register_walk_in"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "secretary/appointments/walk_in.html")

    def test_walk_in_view_post_creates_walk_in_and_redirects_to_waiting_room(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:register_walk_in"),
            {
                "patient_id": str(self.patient_a.id),
                "doctor_id": str(self.doctor_a.id),
                "appointment_type_id": str(self.appt_type_a.id),
                "reason": "Cough",
                "notes": "",
            },
        )
        self.assertRedirects(
            resp, reverse("secretary:waiting_room"),
            fetch_redirect_response=False,
        )
        appt = Appointment.objects.filter(
            clinic=self.clinic_a, patient=self.patient_a, is_walk_in=True
        ).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)
        self.assertEqual(appt.appointment_date, date.today())

    def test_walk_in_view_rejects_doctor_from_different_clinic(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:register_walk_in"),
            {
                "patient_id": str(self.patient_a.id),
                "doctor_id": str(self.doctor_b.id),  # foreign clinic
                "appointment_type_id": str(self.appt_type_a.id),
            },
        )
        self.assertRedirects(
            resp, reverse("secretary:register_walk_in"),
            fetch_redirect_response=False,
        )
        self.assertFalse(
            Appointment.objects.filter(patient=self.patient_a, is_walk_in=True).exists()
        )

    def test_walk_in_view_blocks_non_secretary(self):
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("secretary:register_walk_in"))
        self.assertEqual(resp.status_code, 403)

    def test_walk_in_appointment_appears_in_waiting_room_context(self):
        from secretary.services import register_walk_in
        register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:waiting_room"))
        self.assertEqual(resp.status_code, 200)
        checkedin = resp.context["checkedin_list"]
        self.assertEqual(len(checkedin), 1)
        self.assertTrue(checkedin[0]["appt"].is_walk_in)


# ════════════════════════════════════════════════════════════════════
#  Walk-in conflicts & self-booking guard
# ════════════════════════════════════════════════════════════════════

class WalkInQueueDuplicateTests(SecretaryTestBase):
    """Hard block: cannot register a walk-in for a patient already in queue."""

    def test_second_walk_in_for_same_patient_is_blocked(self):
        from secretary.services import register_walk_in
        from appointments.services.booking_service import BookingError

        register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        with self.assertRaises(BookingError):
            register_walk_in(
                patient=self.patient_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                created_by=self.secretary_a,
            )

    def test_walk_in_after_completed_walk_in_is_allowed(self):
        from secretary.services import register_walk_in
        first = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        first.status = Appointment.Status.COMPLETED
        first.save(update_fields=["status"])

        # Second one should now succeed (no active queue entry)
        second = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.assertEqual(second.status, Appointment.Status.CHECKED_IN)


class WalkInSameDayBookingTests(SecretaryTestBase):
    """Same-day conflict: warn unless override flag passed."""

    def test_walk_in_blocked_when_today_booking_exists(self):
        from secretary.services import register_walk_in
        from appointments.services.booking_service import BookingError

        Appointment.objects.create(
            patient=self.patient_a,
            clinic=self.clinic_a,
            doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=date.today(),
            appointment_time=time(14, 0),
            status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_a,
        )
        with self.assertRaises(BookingError):
            register_walk_in(
                patient=self.patient_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                created_by=self.secretary_a,
            )

    def test_walk_in_allowed_with_override(self):
        from secretary.services import register_walk_in

        Appointment.objects.create(
            patient=self.patient_a,
            clinic=self.clinic_a,
            doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=date.today(),
            appointment_time=time(14, 0),
            status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_a,
        )
        appt = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
            override_same_day_conflict=True,
        )
        self.assertTrue(appt.is_walk_in)
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)

    def test_walk_in_allowed_when_only_future_day_booking_exists(self):
        from secretary.services import register_walk_in

        Appointment.objects.create(
            patient=self.patient_a,
            clinic=self.clinic_a,
            doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=date.today() + timedelta(days=3),
            appointment_time=time(14, 0),
            status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_a,
        )
        appt = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.assertTrue(appt.is_walk_in)

    def test_cancelled_today_booking_does_not_block(self):
        from secretary.services import register_walk_in

        Appointment.objects.create(
            patient=self.patient_a,
            clinic=self.clinic_a,
            doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=date.today(),
            appointment_time=time(14, 0),
            status=Appointment.Status.CANCELLED,
            cancellation_reason="test",
            created_by=self.secretary_a,
        )
        appt = register_walk_in(
            patient=self.patient_a,
            doctor_id=self.doctor_a.id,
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            created_by=self.secretary_a,
        )
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)


class SelfBookingGuardTests(SecretaryTestBase):
    """A doctor cannot be booked as their own patient (in any flow)."""

    def setUp(self):
        super().setUp()
        # Give doctor_a the PATIENT role too — multi-role doctor/patient user
        self.doctor_a.roles = ["DOCTOR", "PATIENT"]
        self.doctor_a.save(update_fields=["roles"])

    def test_regular_booking_blocks_self(self):
        from secretary.services import secretary_book_appointment
        from appointments.services.booking_service import BookingError

        with self.assertRaises(BookingError):
            secretary_book_appointment(
                patient=self.doctor_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                appointment_date=self.next_monday,
                appointment_time=time(10, 0),
                status=Appointment.Status.CONFIRMED,
                created_by=self.secretary_a,
            )

    def test_walk_in_blocks_self(self):
        from secretary.services import register_walk_in
        from appointments.services.booking_service import BookingError

        with self.assertRaises(BookingError):
            register_walk_in(
                patient=self.doctor_a,
                doctor_id=self.doctor_a.id,
                clinic_id=self.clinic_a.id,
                appointment_type_id=self.appt_type_a.id,
                created_by=self.secretary_a,
            )

    def test_doctor_on_doctor_booking_succeeds(self):
        """doctor_a (as patient) booked with a different doctor in same clinic."""
        from secretary.services import secretary_book_appointment

        # Add a second doctor to clinic A
        other_doctor = User.objects.create_user(
            phone="0591100099", password="pass1234",
            name="Dr. Other", role="DOCTOR", roles=["DOCTOR", "PATIENT"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=other_doctor,
            role="DOCTOR", is_active=True,
        )

        appt = secretary_book_appointment(
            patient=self.doctor_a,        # patient = doctor_a
            doctor_id=other_doctor.id,    # doctor = different user
            clinic_id=self.clinic_a.id,
            appointment_type_id=self.appt_type_a.id,
            appointment_date=self.next_monday,
            appointment_time=time(11, 0),
            status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_a,
        )
        self.assertEqual(appt.patient_id, self.doctor_a.id)
        self.assertEqual(appt.doctor_id, other_doctor.id)


class MultiRolePatientRegistrationTests(SecretaryTestBase):
    """Registering an existing doctor as a patient must preserve all roles."""

    def test_registering_doctor_from_other_clinic_as_patient_preserves_roles(self):
        from patients.models import ClinicPatient, PatientProfile

        # doctor_b is from clinic_b, has roles ["DOCTOR"], not yet a patient
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:create_new_patient"), {
            "name": self.doctor_b.name,
            "phone": self.doctor_b.phone,
            "notes": "",
        })
        self.assertEqual(resp.status_code, 302)

        self.doctor_b.refresh_from_db()
        self.assertIn("DOCTOR", self.doctor_b.roles)
        self.assertIn("PATIENT", self.doctor_b.roles)
        self.assertTrue(
            PatientProfile.objects.filter(user=self.doctor_b).exists(),
            "PatientProfile must be ensured",
        )
        self.assertTrue(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.doctor_b
            ).exists(),
            "ClinicPatient row must be created at this clinic",
        )

    def test_registering_doctor_from_same_clinic_as_patient_preserves_roles(self):
        from patients.models import ClinicPatient, PatientProfile

        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:create_new_patient"), {
            "name": self.doctor_a.name,
            "phone": self.doctor_a.phone,
            "notes": "",
        })
        self.assertEqual(resp.status_code, 302)

        self.doctor_a.refresh_from_db()
        self.assertIn("DOCTOR", self.doctor_a.roles)
        self.assertIn("PATIENT", self.doctor_a.roles)
        self.assertTrue(PatientProfile.objects.filter(user=self.doctor_a).exists())
        self.assertTrue(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.doctor_a
            ).exists()
        )
        # ClinicStaff DOCTOR membership untouched
        self.assertTrue(
            ClinicStaff.objects.filter(
                clinic=self.clinic_a, user=self.doctor_a, role="DOCTOR", is_active=True
            ).exists()
        )


# ════════════════════════════════════════════════════════════════════
#  7E — Today's Appointments Filter (dashboard pills)
# ════════════════════════════════════════════════════════════════════

class TodaysAppointmentsFilterTests(SecretaryTestBase):
    """Filter pills on the secretary dashboard: All / Confirmed / Available Slots."""

    def setUp(self):
        super().setUp()
        # Add availability for today's weekday so generate_slots_for_date returns slots.
        # Set the window to start ~1 hour from now and end well after, so the slots
        # we generate aren't all "is_past" regardless of the test wall clock.
        now = timezone.localtime()
        start_h = min(max(now.hour + 1, 8), 21)  # clamp into a sane range
        DoctorAvailability.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            day_of_week=date.today().weekday(),
            start_time=time(start_h, 0),
            end_time=time(min(start_h + 4, 23), 0),
            is_active=True,
        )
        # Today appointments — one of each relevant status
        self.appt_confirmed = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=date.today(),
            appointment_time=time(start_h, 0), status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_a,
        )
        self.appt_cancelled = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=date.today(),
            appointment_time=time(start_h, 30), status=Appointment.Status.CANCELLED,
            created_by=self.secretary_a,
        )
        self.appt_completed = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a, appointment_date=date.today(),
            appointment_time=time(start_h + 1, 0), status=Appointment.Status.COMPLETED,
            created_by=self.secretary_a,
        )

    def _login_secretary(self):
        self.client.force_login(self.secretary_a)

    def test_dashboard_default_filter_is_all(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["current_filter"], "all")

    def test_filter_all_includes_appointments_of_every_status_and_slots(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=all")
        rows = resp.context["rows"]
        appt_ids = {r["appointment"].id for r in rows if r["kind"] == "appointment"}
        self.assertIn(self.appt_confirmed.id, appt_ids)
        self.assertIn(self.appt_cancelled.id, appt_ids)
        self.assertIn(self.appt_completed.id, appt_ids)
        # Slots should also be present (CANCELLED status doesn't reserve, so slot rows exist).
        self.assertTrue(any(r["kind"] == "slot" for r in rows))

    def test_filter_confirmed_excludes_other_statuses(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=confirmed")
        rows = resp.context["rows"]
        statuses = {r["appointment"].status for r in rows if r["kind"] == "appointment"}
        self.assertEqual(statuses, {Appointment.Status.CONFIRMED})
        # No slot rows when filtering to confirmed only.
        self.assertFalse(any(r["kind"] == "slot" for r in rows))

    def test_filter_available_excludes_appointments(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=available")
        rows = resp.context["rows"]
        # No appointment rows should be present.
        self.assertFalse(any(r["kind"] == "appointment" for r in rows))
        # All rows must be available slots.
        self.assertTrue(all(r["kind"] == "slot" for r in rows))

    def test_filter_available_excludes_booked_times(self):
        """A CONFIRMED appointment's time must not appear as an available slot for the same doctor."""
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=available")
        rows = resp.context["rows"]
        booked_key = (self.doctor_a.id, self.appt_confirmed.appointment_time)
        slot_keys = {(r["doctor"].id, r["time"]) for r in rows}
        self.assertNotIn(booked_key, slot_keys)

    def test_invalid_filter_falls_back_to_all(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=garbage")
        self.assertEqual(resp.context["current_filter"], "all")

    def test_htmx_endpoint_returns_partial_for_secretary(self):
        self._login_secretary()
        resp = self.client.get(
            reverse("secretary:todays_appointments_htmx") + "?filter=available",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        # Partial template doesn't extend base — must not include the sidebar markup.
        self.assertNotContains(resp, "secSidebarUserMenu")
        # OOB count spans should appear on HTMX requests so pill counts stay in sync.
        self.assertContains(resp, "filter-count-all")

    def test_htmx_endpoint_forbidden_for_non_secretary(self):
        self.client.force_login(self.patient_a)
        resp = self.client.get(
            reverse("secretary:todays_appointments_htmx"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 403)

    def test_tenant_isolation_other_clinic_appointments_not_in_rows(self):
        """An appointment in clinic_b must not leak into secretary_a's filtered rows."""
        other = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_b, doctor=self.doctor_b,
            appointment_type=self.appt_type_b, appointment_date=date.today(),
            appointment_time=time(11, 0), status=Appointment.Status.CONFIRMED,
            created_by=self.secretary_b,
        )
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard") + "?filter=all")
        rows = resp.context["rows"]
        appt_ids = {r["appointment"].id for r in rows if r["kind"] == "appointment"}
        self.assertNotIn(other.id, appt_ids)

    def test_pill_counts_match_rendered_rows(self):
        self._login_secretary()
        resp = self.client.get(reverse("secretary:dashboard"))
        # 3 appointments today + N slots; count_all == appointment count + count_available
        self.assertEqual(
            resp.context["count_all"],
            3 + resp.context["count_available"],
        )
        self.assertEqual(resp.context["count_confirmed"], 1)


class EditAppointmentPreselectTests(SecretaryTestBase):
    """The edit/reschedule page must load with the appointment's original
    date and time pre-selected (calendar + slot grid)."""

    def _slot_button_html(self, html, data_time):
        """Return the <button> fragment for a given data-time, or '' if absent."""
        marker = 'data-time="%s"' % data_time
        idx = html.find(marker)
        if idx == -1:
            return ""
        start = html.rfind("<button", 0, idx)
        end = html.find("</button>", idx)
        return html[start:end] if start != -1 and end != -1 else ""

    def test_edit_page_prefills_original_date_and_time(self):
        appt = self._make_appointment(appointment_time=time(10, 0))
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:edit_appointment", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # Hidden inputs carry the appointment's current date & time.
        self.assertIn(
            'id="appt-date-input"', html
        )
        self.assertIn(appt.appointment_date.strftime("%Y-%m-%d"), html)
        self.assertIn('id="hidden-time"', html)
        self.assertIn('value="10:00"', html)
        # The calendar keeps the original date selected, and the slot grid is
        # loaded on first paint so the original time can be pre-selected.
        self.assertIn("preserveInitialSelection: true", html)
        self.assertIn("whenHtmxReady", html)
        # Dead context var must be gone.
        self.assertNotIn("selected_time", html)

    def test_original_slot_is_selectable_when_self_excluded(self):
        """time_slots_htmx must render the appointment's own time as an
        available (enabled) button when exclude_appointment_id is sent —
        otherwise the original time could never be pre-selected."""
        appt = self._make_appointment(appointment_time=time(10, 0))
        self.client.force_login(self.secretary_a)
        params = {
            "doctor_id": self.doctor_a.id,
            "appointment_date": appt.appointment_date.strftime("%Y-%m-%d"),
            "appointment_type_id": self.appt_type_a.id,
        }

        # Without excluding self: the 10:00 slot is taken by this appointment.
        resp_no_excl = self.client.get(
            reverse("secretary:time_slots_htmx"), params, HTTP_HX_REQUEST="true"
        )
        btn_no_excl = self._slot_button_html(resp_no_excl.content.decode(), "10:00")
        self.assertTrue(btn_no_excl)
        self.assertIn("disabled", btn_no_excl)

        # Excluding self: the 10:00 slot is free and selectable.
        resp = self.client.get(
            reverse("secretary:time_slots_htmx"),
            dict(params, exclude_appointment_id=appt.id),
            HTTP_HX_REQUEST="true",
        )
        btn = self._slot_button_html(resp.content.decode(), "10:00")
        self.assertTrue(btn, "expected a 10:00 slot button")
        self.assertNotIn("disabled", btn)
        self.assertNotIn("is-booked", btn)

    def test_edit_then_save_unchanged_keeps_original_date_time(self):
        """Submitting the edit form unchanged (original date/time, populated by
        the pre-selection) must save successfully."""
        appt = self._make_appointment(appointment_time=time(10, 0))
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:edit_appointment", args=[appt.id]),
            {
                "doctor_id": str(self.doctor_a.id),
                "appointment_type_id": str(self.appt_type_a.id),
                "appointment_date": appt.appointment_date.strftime("%Y-%m-%d"),
                "appointment_time": "10:00",
                "exclude_appointment_id": str(appt.id),
            },
        )
        self.assertRedirects(
            resp, reverse("secretary:appointments"), fetch_redirect_response=False
        )
        appt.refresh_from_db()
        self.assertEqual(appt.appointment_time, time(10, 0))


# ════════════════════════════════════════════════════════════════════
#  Appointment overview page: view + actions (PENDING / CONFIRMED / CANCELLED)
# ════════════════════════════════════════════════════════════════════

class AppointmentOverviewTests(SecretaryTestBase):
    """The secretary notifications page's 'view appointment' link navigates to a
    patient-scoped overview page whose status-aware action controls depend on the
    appointment's status and on whether the patient is already a ClinicPatient."""

    def _login_secretary(self):
        self.client.force_login(self.secretary_a)

    def _overview_url(self, appt):
        return reverse("secretary:appointment_overview", args=[appt.id])

    # ── Page rendering ────────────────────────────────────────────────

    def test_overview_renders_pending_new_patient_with_three_actions(self):
        """PENDING + no ClinicPatient → 3 buttons (accept&register, register-only, reject)."""
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_new_patient_request"])
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/accept-new-patient/")
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/register-new-patient-only/")
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/reject-new-patient/")

    def test_overview_renders_pending_existing_patient_with_two_actions(self):
        """PENDING + existing ClinicPatient → confirm + reject only."""
        from patients.models import ClinicPatient
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a, file_number="P0001",
        )
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_new_patient_request"])
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/status/")   # confirm
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/cancel/")   # reject
        self.assertNotContains(resp, "/register-new-patient-only/")
        self.assertNotContains(resp, "/accept-new-patient/")

    def test_overview_renders_confirmed_with_reschedule_and_cancel(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/edit/")    # reschedule link
        self.assertContains(resp, f"/secretary/appointments/{appt.id}/cancel/")  # cancel form

    def test_overview_renders_cancelled_readonly_with_reason(self):
        appt = self._make_appointment(status=Appointment.Status.CANCELLED)
        appt.cancellation_reason = "ظهور حالة طارئة"
        appt.save(update_fields=["cancellation_reason"])
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt))
        self.assertEqual(resp.status_code, 200)
        # No action endpoints on the page for CANCELLED.
        self.assertNotContains(resp, f"/secretary/appointments/{appt.id}/edit/")
        self.assertNotContains(resp, f"/secretary/appointments/{appt.id}/status/")
        self.assertNotContains(resp, f"/secretary/appointments/{appt.id}/cancel/")
        self.assertContains(resp, "ظهور حالة طارئة")

    def test_overview_shows_booking_doctor_name(self):
        """The focused appointment surfaces which doctor the patient booked with."""
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt))
        self.assertContains(resp, self.doctor_a.name)

    def test_overview_timeline_spans_all_doctors_in_clinic(self):
        """The patient's other appointments in this clinic appear regardless of
        which doctor they were booked with."""
        focal = self._make_appointment(status=Appointment.Status.CONFIRMED)
        # A second clinic-A appointment with a *different* doctor.
        other_doctor = User.objects.create_user(
            phone="0591100099", password="pass1234",
            name="Dr. Other", role="DOCTOR", roles=["DOCTOR"],
        )
        ClinicStaff.objects.create(
            clinic=self.clinic_a, user=other_doctor, role="DOCTOR", is_active=True,
        )
        other = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=other_doctor,
            appointment_type=self.appt_type_a,
            appointment_date=self.next_monday + timedelta(days=7),
            appointment_time=time(12, 0),
            status=Appointment.Status.CONFIRMED, created_by=self.secretary_a,
        )
        self._login_secretary()
        resp = self.client.get(self._overview_url(focal))
        self.assertIn(other, resp.context["upcoming"])
        self.assertContains(resp, "Dr. Other")

    # ── Mark-as-read flow (via open_notification) ─────────────────────

    def test_open_notification_marks_read_and_redirects_to_overview(self):
        """The 'view appointment' link routes through open_notification, which
        marks the notification read and redirects to the overview page."""
        from appointments.models import AppointmentNotification
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        notif = AppointmentNotification.objects.create(
            patient=self.secretary_a,
            appointment=appt,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            title="حجز جديد", message="حجز جديد للمريض",
        )
        self.assertFalse(notif.is_read)
        self._login_secretary()
        resp = self.client.get(reverse("appointments:open_notification", args=[notif.pk]))
        self.assertRedirects(resp, self._overview_url(appt), fetch_redirect_response=False)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_visiting_overview_does_not_mark_notifications_read(self):
        """The overview page itself does not mark notifications read (that is
        open_notification's job)."""
        from appointments.models import AppointmentNotification
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        notif = AppointmentNotification.objects.create(
            patient=self.secretary_a,
            appointment=appt,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            title="x", message="y",
        )
        self._login_secretary()
        self.client.get(self._overview_url(appt))
        notif.refresh_from_db()
        self.assertFalse(notif.is_read)

    def test_open_notification_cannot_mark_another_users_notification(self):
        """Ownership-scoped: a notif pk owned by another secretary is a 404 and
        stays unread."""
        from appointments.models import AppointmentNotification
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        other = AppointmentNotification.objects.create(
            patient=self.secretary_b,
            appointment=appt,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            title="x", message="y",
        )
        self._login_secretary()  # secretary_a
        resp = self.client.get(reverse("appointments:open_notification", args=[other.pk]))
        self.assertEqual(resp.status_code, 404)
        other.refresh_from_db()
        self.assertFalse(other.is_read)

    # ── Permissions / isolation ───────────────────────────────────────

    def test_overview_forbidden_for_non_secretary(self):
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self.client.force_login(self.patient_a)
        resp = self.client.get(self._overview_url(appt))
        self.assertEqual(resp.status_code, 403)

    def test_overview_cross_clinic_404(self):
        """Secretary A cannot open the overview for an appointment in clinic B."""
        appt_b = Appointment.objects.create(
            patient=self.patient_a, clinic=self.clinic_b, doctor=self.doctor_b,
            appointment_type=self.appt_type_b,
            appointment_date=self.next_monday, appointment_time=time(11, 0),
            status=Appointment.Status.PENDING, created_by=self.secretary_b,
        )
        self._login_secretary()
        resp = self.client.get(self._overview_url(appt_b))
        self.assertEqual(resp.status_code, 404)

    # ── register_new_patient_only action ──────────────────────────────

    def test_register_only_creates_clinic_patient_and_cancels_appointment(self):
        from patients.models import ClinicPatient
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        notifications_url = reverse("appointments:secretary_notifications")
        self._login_secretary()
        resp = self.client.post(
            reverse("secretary:register_new_patient_only", args=[appt.id]),
            {"next": notifications_url, "cancellation_reason": "test reason"},
        )
        self.assertRedirects(resp, notifications_url, fetch_redirect_response=False)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)
        self.assertEqual(appt.cancellation_reason, "test reason")
        self.assertTrue(
            ClinicPatient.objects.filter(
                clinic=self.clinic_a, patient=self.patient_a
            ).exists()
        )

    def test_register_only_blocked_if_patient_already_registered(self):
        from patients.models import ClinicPatient
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a, file_number="P0001",
        )
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self._login_secretary()
        resp = self.client.post(
            reverse("secretary:register_new_patient_only", args=[appt.id]),
            {"next": reverse("appointments:secretary_notifications")},
        )
        # View redirects with an error message; appointment stays PENDING.
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.PENDING)

    def test_register_only_blocked_if_appointment_not_pending(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        self.client.post(
            reverse("secretary:register_new_patient_only", args=[appt.id]),
            {"next": reverse("appointments:secretary_notifications")},
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    def test_register_only_forbidden_for_non_secretary(self):
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self.client.force_login(self.patient_a)
        resp = self.client.post(
            reverse("secretary:register_new_patient_only", args=[appt.id]),
            {"next": "/"},
        )
        self.assertEqual(resp.status_code, 403)


class NotificationUrlRoutingTests(SecretaryTestBase):
    """The notifications page must expose a navigating target_url (routed through
    open_notification) for secretary notifications, and no modal_url."""

    def test_secretary_notification_has_target_url_not_modal_url(self):
        from appointments.models import AppointmentNotification
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        AppointmentNotification.objects.create(
            patient=self.secretary_a,
            appointment=appt,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            title="حجز جديد",
            message="حجز جديد للمريض",
        )
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertEqual(resp.status_code, 200)
        notifications = resp.context["notifications"]
        self.assertEqual(len(notifications), 1)
        n = notifications[0]
        self.assertIsNone(n.modal_url)
        self.assertEqual(
            n.target_url,
            reverse("appointments:open_notification", args=[n.pk]),
        )


class BookedNotificationVisualByStatusTests(SecretaryTestBase):
    """A single APPOINTMENT_BOOKED notification's card visual is derived from
    its appointment's *current* status, so after the secretary confirms a
    pending booking the card auto-updates without rewriting the notification."""

    def _login_secretary(self):
        self.client.force_login(self.secretary_a)

    def _make_booked_notif(self, appt, title="NOTIF_TITLE_X", message="NOTIF_MSG_X"):
        # Plain ASCII title/message so they don't collide with badge labels in assertions.
        from appointments.models import AppointmentNotification
        return AppointmentNotification.objects.create(
            patient=self.secretary_a,
            appointment=appt,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.SECRETARY,
            title=title,
            message=message,
        )

    def test_pending_booking_renders_amber_pending_review_badge(self):
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        self._make_booked_notif(appt)
        self._login_secretary()
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertContains(resp, "fa-regular fa-clock")
        self.assertContains(resp, "text-amber-700")
        self.assertContains(resp, "قيد المراجعة")

    def test_confirmed_booking_renders_emerald_booked_badge(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._make_booked_notif(appt)
        self._login_secretary()
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertContains(resp, "fa-regular fa-calendar-check")
        self.assertContains(resp, "text-emerald-700")
        self.assertContains(resp, "محجوز")
        self.assertNotContains(resp, "قيد المراجعة")
        self.assertNotContains(resp, "text-amber-700")

    def test_rejected_booking_renders_red_rejected_badge(self):
        appt = self._make_appointment(status=Appointment.Status.CANCELLED)
        self._make_booked_notif(appt)
        self._login_secretary()
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertContains(resp, "fa-regular fa-calendar-xmark")
        self.assertContains(resp, "text-red-700")
        self.assertContains(resp, "تم الرفض")
        self.assertNotContains(resp, "قيد المراجعة")
        self.assertNotContains(resp, "text-amber-700")
        self.assertNotContains(resp, "text-emerald-700")

    def test_card_visual_updates_after_secretary_confirms(self):
        """End-to-end: pending booking → amber card → secretary confirms → card flips to emerald."""
        from patients.models import ClinicPatient
        ClinicPatient.objects.create(
            clinic=self.clinic_a, patient=self.patient_a,
            registered_by=self.secretary_a, file_number="P0001",
        )
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        FROZEN_TITLE = "ORIGINAL_NOTIF_TITLE_PINNED"
        self._make_booked_notif(appt, title=FROZEN_TITLE)
        self._login_secretary()
        resp_before = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertContains(resp_before, "text-amber-700")
        self.assertContains(resp_before, "قيد المراجعة")
        self.assertContains(resp_before, FROZEN_TITLE)

        # Secretary confirms via the modal's confirm form.
        self.client.post(
            reverse("secretary:update_appointment_status", args=[appt.id]),
            {"status": "CONFIRMED",
             "next": reverse("appointments:secretary_notifications")},
        )

        resp_after = self.client.get(reverse("appointments:secretary_notifications"))
        # Original title text is preserved; only the badge visual updates.
        self.assertContains(resp_after, FROZEN_TITLE)            # title is frozen
        self.assertNotContains(resp_after, "text-amber-700")     # no more pending visual
        self.assertNotContains(resp_after, "قيد المراجعة")        # no more pending badge text
        self.assertContains(resp_after, "text-emerald-700")
        self.assertContains(resp_after, "محجوز")


class AppointmentOverviewBackNavigationTests(SecretaryTestBase):
    """The overview page's back button is context-aware via ?return_to=, and
    defaults to the notification center when no param is given."""

    def _login_secretary(self):
        self.client.force_login(self.secretary_a)

    def test_back_url_returns_to_notifications(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        resp = self.client.get(
            reverse("secretary:appointment_overview", args=[appt.id])
            + "?return_to=notifications"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context["back_url"],
            reverse("appointments:secretary_notifications"),
        )

    def test_back_url_defaults_to_notifications_when_no_param(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        resp = self.client.get(reverse("secretary:appointment_overview", args=[appt.id]))
        self.assertEqual(
            resp.context["back_url"],
            reverse("appointments:secretary_notifications"),
        )

    def test_back_url_returns_to_appointments_list(self):
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        self._login_secretary()
        resp = self.client.get(
            reverse("secretary:appointment_overview", args=[appt.id])
            + "?return_to=appointments"
        )
        self.assertEqual(
            resp.context["back_url"],
            reverse("secretary:appointments"),
        )

    def test_overview_timeline_links_to_overview_with_return_to_notifications(self):
        """Each timeline card on the overview page links to the appointment
        overview with the return_to param so its back button returns to notifications."""
        focal = self._make_appointment(status=Appointment.Status.CONFIRMED)
        other = self._make_appointment(
            status=Appointment.Status.CONFIRMED,
            appointment_date=self.next_monday + timedelta(days=7),
        )
        self._login_secretary()
        resp = self.client.get(
            reverse("secretary:appointment_overview", args=[focal.id])
        )
        self.assertContains(
            resp,
            reverse("secretary:appointment_overview", args=[other.id]) + "?return_to=notifications",
        )


class FormatClockHelperTests(TestCase):
    """Unit tests for the shared 24h/12h time formatter."""

    def test_24h_format(self):
        from secretary.timefmt import format_clock
        self.assertEqual(format_clock(time(14, 30), False), "14:30")
        self.assertEqual(format_clock(time(9, 5), False), "09:05")

    def test_12h_arabic_markers(self):
        from secretary.timefmt import format_clock
        self.assertEqual(format_clock(time(14, 30), True, "ar"), "2:30 م")
        self.assertEqual(format_clock(time(9, 5), True, "ar"), "9:05 ص")

    def test_12h_english_markers(self):
        from secretary.timefmt import format_clock
        self.assertEqual(format_clock(time(14, 30), True, "en"), "2:30 PM")
        self.assertEqual(format_clock(time(9, 5), True, "en"), "9:05 AM")

    def test_midnight_and_noon_edges(self):
        from secretary.timefmt import format_clock
        self.assertEqual(format_clock(time(0, 0), True, "en"), "12:00 AM")
        self.assertEqual(format_clock(time(12, 0), True, "en"), "12:00 PM")
        self.assertEqual(format_clock(time(0, 0), True, "ar"), "12:00 ص")

    def test_none_returns_empty_string(self):
        from secretary.timefmt import format_clock
        self.assertEqual(format_clock(None, True), "")

    def test_aware_datetime_is_localized(self):
        """Aware datetimes must format the same as Django's tz-aware localtime."""
        import datetime as _dt
        from secretary.timefmt import format_clock
        aware = _dt.datetime(2026, 1, 15, 23, 30, tzinfo=_dt.timezone.utc)
        expected = timezone.localtime(aware).strftime("%H:%M")
        self.assertEqual(format_clock(aware, False), expected)


class TimeFormatPreferenceTests(SecretaryTestBase):
    """The secretary profile page persists the 24h/12h display preference."""

    def test_default_is_24h(self):
        self.assertEqual(self.secretary_a.time_format, "24")

    def test_profile_page_shows_time_format_setting(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.get(reverse("secretary:settings_profile"))
        self.assertContains(resp, 'name="time_format"')

    def test_save_12h_preference(self):
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:settings_profile"),
            {"action": "preferences", "time_format": "12"},
        )
        self.assertRedirects(resp, reverse("secretary:settings_profile"))
        self.secretary_a.refresh_from_db()
        self.assertEqual(self.secretary_a.time_format, "12")

    def test_invalid_value_is_ignored(self):
        self.client.force_login(self.secretary_a)
        self.client.post(
            reverse("secretary:settings_profile"),
            {"action": "preferences", "time_format": "garbage"},
        )
        self.secretary_a.refresh_from_db()
        self.assertEqual(self.secretary_a.time_format, "24")

    def test_clock_filter_available_as_builtin(self):
        """The clock filter renders without an explicit {% load %}."""
        from django.template import Template, Context
        out = Template("{{ t|clock:True }}").render(Context({"t": time(14, 30)}))
        self.assertIn("2:30", out)

    def test_clock_text_reformats_embedded_time_for_12h(self):
        """A baked-in 24h time inside a notification message is rewritten to 12h,
        while the date (which has no colon) is left untouched."""
        from django.template import Template, Context
        msg = "A new appointment was booked on 2026-05-23 at 17:45."
        out = Template("{{ m|clock_text:True }}").render(Context({"m": msg}))
        self.assertIn("5:45", out)
        self.assertNotIn("17:45", out)
        self.assertIn("2026-05-23", out)

    def test_clock_text_is_noop_for_24h(self):
        from django.template import Template, Context
        msg = "Booked at 17:45."
        out = Template("{{ m|clock_text:False }}").render(Context({"m": msg}))
        self.assertIn("17:45", out)


# ════════════════════════════════════════════════════════════════════
#  Secretary optional intake form during booking
# ════════════════════════════════════════════════════════════════════

class SecretaryIntakeBookingTest(SecretaryTestBase):
    """
    The secretary can OPTIONALLY fill the doctor's intake form when booking on a
    patient's behalf (opt-in toggle, all fields optional). Reuses the patient-side
    intake machinery via `collect_and_validate_intake(enforce_required=False)` and
    `save_intake_answers`.
    """

    def setUp(self):
        super().setUp()
        from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: F401
        self.SimpleUploadedFile = SimpleUploadedFile

        # Generic template for doctor_a (applies to all appointment types).
        self.template = DoctorIntakeFormTemplate.objects.create(
            doctor=self.doctor_a,
            appointment_type=None,
            title="Intake",
            title_ar="نموذج الإدخال",
            show_reason_field=False,
        )
        self.q_text = DoctorIntakeQuestion.objects.create(
            template=self.template,
            question_text="Allergies?",
            question_text_ar="هل لديك حساسية؟",
            field_type=DoctorIntakeQuestion.FieldType.TEXT,
            is_required=True,  # required for patients, but optional for the secretary
            order=1,
        )
        self.q_file = DoctorIntakeQuestion.objects.create(
            template=self.template,
            question_text="Lab report",
            question_text_ar="تقرير المختبر",
            field_type=DoctorIntakeQuestion.FieldType.FILE,
            is_required=False,
            order=2,
            allowed_extensions=["pdf"],
            max_file_size_mb=5,
        )
        # No allowed_extensions → the global image+PDF baseline applies.
        self.q_file_open = DoctorIntakeQuestion.objects.create(
            template=self.template,
            question_text="Birth certificate",
            question_text_ar="شهادة الميلاد",
            field_type=DoctorIntakeQuestion.FieldType.FILE,
            is_required=False,
            order=3,
        )

    def _post_data(self, **extra):
        data = {
            "patient_id": str(self.patient_a.id),
            "doctor_id": str(self.doctor_a.id),
            "appointment_type_id": str(self.appt_type_a.id),
            "appointment_date": self.next_monday.strftime("%Y-%m-%d"),
            "appointment_time": "09:00",
            "reason": "visit",
        }
        data.update(extra)
        return data

    def test_fill_intake_saves_answers(self):
        self.client.force_login(self.secretary_a)
        self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(fill_intake="1", **{f"intake_{self.q_text.id}": "Penicillin"}),
        )
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        answers = AppointmentAnswer.objects.filter(appointment=appt)
        self.assertEqual(answers.count(), 1)
        self.assertEqual(answers.first().question_id, self.q_text.id)
        self.assertEqual(answers.first().answer_text, "Penicillin")

    def test_no_toggle_skips_intake(self):
        """Without the toggle, intake fields are ignored even if present in POST."""
        self.client.force_login(self.secretary_a)
        self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(**{f"intake_{self.q_text.id}": "Penicillin"}),
        )
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        self.assertEqual(AppointmentAnswer.objects.filter(appointment=appt).count(), 0)

    def test_required_field_not_enforced(self):
        """Toggle on but the required question left blank → booking still succeeds."""
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(fill_intake="1"),  # q_text intentionally omitted
        )
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        self.assertRedirects(
            resp,
            reverse("secretary:appointment_overview", kwargs={"appointment_id": appt.id}),
            fetch_redirect_response=False,
        )
        self.assertEqual(AppointmentAnswer.objects.filter(appointment=appt).count(), 0)

    def test_bad_file_type_blocks_booking(self):
        """A file outside the question's allowed_extensions blocks booking."""
        self.client.force_login(self.secretary_a)
        bad = self.SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        resp = self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(fill_intake="1", **{f"intake_{self.q_file.id}": bad}),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())

    def test_exe_blocked_by_baseline_when_no_extensions_set(self):
        """A question with no allowed_extensions still rejects .exe via the baseline."""
        self.client.force_login(self.secretary_a)
        exe = self.SimpleUploadedFile(
            "installer.exe", b"MZ\x90\x00\x03\x00\x00\x00", content_type="application/octet-stream"
        )
        resp = self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(fill_intake="1", **{f"intake_{self.q_file_open.id}": exe}),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())

    def test_spoofed_extension_blocked_by_signature(self):
        """An executable renamed to .pdf passes the extension check but the content
        signature check rejects it."""
        self.client.force_login(self.secretary_a)
        fake = self.SimpleUploadedFile(
            "evil.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("secretary:create_appointment"),
            self._post_data(fill_intake="1", **{f"intake_{self.q_file_open.id}": fake}),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Appointment.objects.filter(clinic=self.clinic_a).exists())


# ════════════════════════════════════════════════════════════════════
#  Billing / Accounting
# ════════════════════════════════════════════════════════════════════

class BillingTest(SecretaryTestBase):
    """Billing session lifecycle, charges, payments (overpayment + FIFO), debts."""

    def _checked_in(self, appointment_time=time(10, 0)):
        return self._make_appointment(
            status=Appointment.Status.CHECKED_IN, appointment_time=appointment_time,
        )

    def _complete_visit(self, appt):
        """Finish a visit so the open session locks (DRAFT → ISSUED) and any
        remaining balance becomes real debt."""
        from secretary import billing
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(appt, Appointment.Status.COMPLETED)

    # ── Session lifecycle ────────────────────────────────────────────

    def test_open_session_requires_checked_in(self):
        from secretary import billing
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        with self.assertRaises(billing.BillingError):
            billing.open_billing_session(appt, by_user=self.secretary_a)

    def test_open_session_seeds_consultation_fee(self):
        from secretary import billing
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.assertEqual(inv.items.count(), 1)
        self.assertEqual(inv.total, Decimal("50.00"))
        self.assertEqual(inv.balance_due, Decimal("50.00"))
        self.assertEqual(inv.appointment_id, appt.id)

    def test_open_session_is_idempotent(self):
        from secretary import billing
        appt = self._checked_in()
        inv1 = billing.open_billing_session(appt, by_user=self.secretary_a)
        inv2 = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.assertEqual(inv1.id, inv2.id)

    # ── Charges ──────────────────────────────────────────────────────

    def test_add_and_remove_charge_recomputes_total(self):
        from secretary import billing
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        billing.add_charge(inv, description="حقنة", quantity=2, unit_price=Decimal("25.00"))
        inv.refresh_from_db()
        self.assertEqual(inv.total, Decimal("100.00"))  # 50 + 2*25
        # Remove the consultation seed → only the injection remains.
        consult = inv.items.filter(description="General").first()
        billing.remove_charge(consult)
        inv.refresh_from_db()
        self.assertEqual(inv.total, Decimal("50.00"))

    def test_charges_lock_after_completed(self):
        from secretary import billing
        from secretary.models import Invoice
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        # Complete the appointment → invoice locks (DRAFT → ISSUED).
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(appt, Appointment.Status.COMPLETED)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertFalse(billing.is_editable(inv))
        with self.assertRaises(billing.BillingError):
            billing.add_charge(inv, description="late", quantity=1, unit_price=Decimal("10.00"))

    def test_auto_void_on_cancel(self):
        from secretary import billing
        from secretary.models import Invoice
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        appt.status = Appointment.Status.CANCELLED
        appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(appt, Appointment.Status.CANCELLED)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.CANCELLED)

    # ── Payments ─────────────────────────────────────────────────────

    def test_partial_then_full_payment(self):
        from secretary import billing
        from secretary.models import Invoice
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        billing.record_payment(primary_invoice=inv, amount=Decimal("30.00"),
                               method="CASH", breakdown="دفعة أولى", by_user=self.secretary_a)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PARTIAL)
        self.assertEqual(inv.balance_due, Decimal("20.00"))
        billing.record_payment(primary_invoice=inv, amount=Decimal("20.00"),
                               method="CASH", by_user=self.secretary_a)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)
        self.assertEqual(inv.balance_due, Decimal("0.00"))

    def test_overpayment_is_rejected(self):
        from secretary import billing
        from secretary.models import Payment
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        with self.assertRaises(billing.BillingError):
            billing.record_payment(primary_invoice=inv, amount=Decimal("50.01"),
                                   method="CASH", by_user=self.secretary_a)
        self.assertEqual(Payment.objects.count(), 0)

    def test_payment_settles_old_debt_fifo(self):
        from secretary import billing
        from secretary.models import Invoice
        # Old invoice → debt of 50 (completed, unpaid).
        old_appt = self._checked_in(appointment_time=time(9, 0))
        old_inv = billing.open_billing_session(old_appt, by_user=self.secretary_a)
        old_appt.status = Appointment.Status.COMPLETED
        old_appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(old_appt, Appointment.Status.COMPLETED)

        # New session → 50.
        new_appt = self._checked_in(appointment_time=time(11, 0))
        new_inv = billing.open_billing_session(new_appt, by_user=self.secretary_a)

        self.assertEqual(billing.patient_outstanding(self.clinic_a, self.patient_a), Decimal("100.00"))

        # Pay 70: 50 → current session, 20 → oldest debt (FIFO).
        billing.record_payment(primary_invoice=new_inv, amount=Decimal("70.00"),
                               method="CASH", breakdown="كشف + سداد دين", by_user=self.secretary_a)
        new_inv.refresh_from_db()
        old_inv.refresh_from_db()
        self.assertEqual(new_inv.balance_due, Decimal("0.00"))
        self.assertEqual(new_inv.status, Invoice.Status.PAID)
        self.assertEqual(old_inv.balance_due, Decimal("30.00"))  # 50 - 20

    def test_patient_outstanding_excludes_cancelled(self):
        from secretary import billing
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.assertEqual(billing.patient_outstanding(self.clinic_a, self.patient_a), Decimal("50.00"))
        appt.status = Appointment.Status.CANCELLED
        appt.save(update_fields=["status"])
        billing.on_appointment_status_changed(appt, Appointment.Status.CANCELLED)
        self.assertEqual(billing.patient_outstanding(self.clinic_a, self.patient_a), Decimal("0.00"))

    # ── Views ────────────────────────────────────────────────────────

    def test_start_billing_view_creates_invoice(self):
        from secretary.models import Invoice
        appt = self._checked_in()
        self.client.force_login(self.secretary_a)
        resp = self.client.post(reverse("secretary:start_billing", args=[appt.id]))
        self.assertEqual(resp.status_code, 302)
        inv = Invoice.objects.get(appointment=appt)
        self.assertIn(reverse("secretary:invoice_detail", args=[inv.id]), resp.url)

    def test_invoice_detail_clinic_isolation(self):
        from secretary import billing
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.client.force_login(self.secretary_b)
        resp = self.client.get(reverse("secretary:invoice_detail", args=[inv.id]))
        self.assertEqual(resp.status_code, 404)

    def test_record_payment_view_blocks_overpayment(self):
        from secretary import billing
        from secretary.models import Payment
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.client.force_login(self.secretary_a)
        resp = self.client.post(
            reverse("secretary:invoice_record_payment", args=[inv.id]),
            {"amount": "999.00", "method": "CASH", "breakdown": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)
        inv.refresh_from_db()
        self.assertEqual(inv.balance_due, Decimal("50.00"))

    def test_debts_page_lists_patient(self):
        from secretary import billing
        appt = self._checked_in()
        billing.open_billing_session(appt, by_user=self.secretary_a)
        self.client.force_login(self.secretary_a)
        # An open (in-progress) session is not yet debt → patient not listed.
        resp = self.client.get(reverse("secretary:patient_debts"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self.patient_a.name)
        # Complete the visit unpaid → the balance becomes debt → now listed.
        self._complete_visit(appt)
        resp = self.client.get(reverse("secretary:patient_debts"))
        self.assertContains(resp, self.patient_a.name)

    def test_debt_badge_htmx_shows_amount(self):
        from secretary import billing
        appt = self._checked_in()
        billing.open_billing_session(appt, by_user=self.secretary_a)
        self.client.force_login(self.secretary_a)
        # Open session → no debt banner.
        resp = self.client.get(
            reverse("secretary:patient_debt_badge_htmx"),
            {"patient_id": self.patient_a.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "دين مستحق")
        # After completing the visit unpaid → the banner shows the amount.
        self._complete_visit(appt)
        resp = self.client.get(
            reverse("secretary:patient_debt_badge_htmx"),
            {"patient_id": self.patient_a.id},
        )
        self.assertContains(resp, "دين مستحق")  # banner shown
        self.assertContains(resp, "50")          # amount (locale may use , or .)

    # ── Debt = finalized only (open session is the current bill, not debt) ──

    def test_open_session_is_not_debt(self):
        from secretary import billing
        appt = self._checked_in()
        billing.open_billing_session(appt, by_user=self.secretary_a)
        # ₪50 open session must not count as debt anywhere it's displayed.
        self.assertEqual(billing.patient_debt(self.clinic_a, self.patient_a), Decimal("0.00"))
        self.assertEqual(billing.debt_map(self.clinic_a, [self.patient_a.id]), {})
        self.assertEqual(billing.clinic_total_debt(self.clinic_a), Decimal("0.00"))
        # But it's still fully payable (overpayment guard unaffected).
        self.assertEqual(billing.patient_outstanding(self.clinic_a, self.patient_a), Decimal("50.00"))

    def test_debt_appears_after_completing_visit_unpaid(self):
        from secretary import billing
        appt = self._checked_in()
        billing.open_billing_session(appt, by_user=self.secretary_a)
        self._complete_visit(appt)
        self.assertEqual(billing.patient_debt(self.clinic_a, self.patient_a), Decimal("50.00"))
        self.assertEqual(
            billing.debt_map(self.clinic_a, [self.patient_a.id]),
            {self.patient_a.id: Decimal("50.00")},
        )

    def test_partial_payment_mid_visit_is_not_debt(self):
        from secretary import billing
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        billing.record_payment(primary_invoice=inv, amount=Decimal("30.00"),
                               method="CASH", by_user=self.secretary_a)
        # Still mid-visit (PARTIAL but appointment IN-queue) → no debt yet.
        self.assertEqual(billing.patient_debt(self.clinic_a, self.patient_a), Decimal("0.00"))
        self.assertEqual(billing.debt_map(self.clinic_a, [self.patient_a.id]), {})
        # Complete unpaid remainder → the ₪20 left becomes debt.
        self._complete_visit(appt)
        self.assertEqual(billing.patient_debt(self.clinic_a, self.patient_a), Decimal("20.00"))

    def test_debt_excludes_current_open_session_only(self):
        from secretary import billing
        # Prior completed unpaid visit → debt of 50.
        old_appt = self._checked_in(appointment_time=time(9, 0))
        billing.open_billing_session(old_appt, by_user=self.secretary_a)
        self._complete_visit(old_appt)
        # New open session → another 50, but in-progress (not debt).
        new_appt = self._checked_in(appointment_time=time(11, 0))
        billing.open_billing_session(new_appt, by_user=self.secretary_a)
        # Debt = only the prior visit; total payable includes both.
        self.assertEqual(billing.patient_debt(self.clinic_a, self.patient_a), Decimal("50.00"))
        self.assertEqual(billing.patient_outstanding(self.clinic_a, self.patient_a), Decimal("100.00"))

    # ── Bilingual (English) rendering ────────────────────────────────

    def test_billing_pages_render_in_english(self):
        """With preferred_language=en, billing UI + status labels are English."""
        from secretary import billing
        self.secretary_a.preferred_language = "en"
        self.secretary_a.save(update_fields=["preferred_language"])
        appt = self._checked_in()
        inv = billing.open_billing_session(appt, by_user=self.secretary_a)
        self.client.force_login(self.secretary_a)

        # Dashboard
        resp = self.client.get(reverse("secretary:billing_invoices"))
        self.assertContains(resp, "Accounting")
        self.assertContains(resp, "Patients in debt")

        # Invoice detail — translated UI strings + status label, no Arabic source
        resp = self.client.get(reverse("secretary:invoice_detail", args=[inv.id]))
        self.assertContains(resp, "Record payment")
        self.assertContains(resp, "Charges")
        self.assertContains(resp, "Draft")          # Invoice.Status label
        self.assertNotContains(resp, "الرسوم")      # Arabic msgid must be translated away

        # Debts page
        resp = self.client.get(reverse("secretary:patient_debts"))
        self.assertContains(resp, "Patients with outstanding balances")


class DeleteNotificationTests(SecretaryTestBase):
    """Hard-delete endpoint: individual, multi-select, all-read; ownership +
    context isolation enforced exactly like mark-all-read."""

    def _notif(self, user, *, context_role, is_read=False, title="N"):
        from appointments.models import AppointmentNotification
        return AppointmentNotification.objects.create(
            patient=user,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=context_role,
            title=title,
            message="msg",
            is_read=is_read,
        )

    def setUp(self):
        super().setUp()
        from appointments.models import AppointmentNotification
        self.Notif = AppointmentNotification
        self.url = reverse("appointments:delete_notifications")
        self.client.force_login(self.secretary_a)

    def test_individual_delete_removes_only_that_row(self):
        keep = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        gone = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        self.client.post(self.url, {
            "mode": "selected",
            "ids": [gone.pk],
            "context_role": self.Notif.ContextRole.SECRETARY,
        })
        self.assertTrue(self.Notif.objects.filter(pk=keep.pk).exists())
        self.assertFalse(self.Notif.objects.filter(pk=gone.pk).exists())

    def test_multi_select_delete(self):
        a = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        b = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        c = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        self.client.post(self.url, {
            "mode": "selected",
            "ids": [a.pk, b.pk],
            "context_role": self.Notif.ContextRole.SECRETARY,
        })
        self.assertFalse(self.Notif.objects.filter(pk__in=[a.pk, b.pk]).exists())
        self.assertTrue(self.Notif.objects.filter(pk=c.pk).exists())

    def test_delete_all_read_keeps_unread(self):
        read1 = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY, is_read=True)
        read2 = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY, is_read=True)
        unread = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY, is_read=False)
        self.client.post(self.url, {
            "mode": "read",
            "context_role": self.Notif.ContextRole.SECRETARY,
        })
        self.assertFalse(self.Notif.objects.filter(pk__in=[read1.pk, read2.pk]).exists())
        self.assertTrue(self.Notif.objects.filter(pk=unread.pk).exists())

    def test_delete_is_scoped_to_context(self):
        """Deleting in the SECRETARY portal must not touch the same user's
        notifications scoped to another portal, even if their id is POSTed."""
        sec = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        owner = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.CLINIC_OWNER)
        self.client.post(self.url, {
            "mode": "selected",
            "ids": [sec.pk, owner.pk],
            "context_role": self.Notif.ContextRole.SECRETARY,
        })
        self.assertFalse(self.Notif.objects.filter(pk=sec.pk).exists())
        self.assertTrue(self.Notif.objects.filter(pk=owner.pk).exists())

    def test_cannot_delete_another_users_notification(self):
        mine = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        other = self._notif(self.main_doctor_a, context_role=self.Notif.ContextRole.SECRETARY)
        self.client.post(self.url, {
            "mode": "selected",
            "ids": [mine.pk, other.pk],
            "context_role": self.Notif.ContextRole.SECRETARY,
        })
        self.assertFalse(self.Notif.objects.filter(pk=mine.pk).exists())
        self.assertTrue(self.Notif.objects.filter(pk=other.pk).exists())

    def test_delete_requires_post(self):
        n = self._notif(self.secretary_a, context_role=self.Notif.ContextRole.SECRETARY)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(self.Notif.objects.filter(pk=n.pk).exists())


class OwnerNotificationCenterRenderTests(SecretaryTestBase):
    """The rewritten clinic-owner notification center renders with the modern
    layout and the delete controls, and stays bilingual."""

    def _owner_notif(self, **kw):
        from appointments.models import AppointmentNotification
        return AppointmentNotification.objects.create(
            patient=self.main_doctor_a,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            context_role=AppointmentNotification.ContextRole.CLINIC_OWNER,
            title="OWNER_NOTIF_TITLE",
            message="OWNER_NOTIF_MSG",
            **kw,
        )

    def test_owner_center_renders_with_delete_controls(self):
        self._owner_notif()
        self._owner_notif(is_read=True)
        self.client.force_login(self.main_doctor_a)
        resp = self.client.get(reverse("appointments:clinic_owner_notifications"))
        self.assertEqual(resp.status_code, 200)
        # Modern layout markers + delete UI hooks shared with the JS partial.
        self.assertContains(resp, "onc-notif")
        self.assertContains(resp, 'id="notif-select-all"')
        self.assertContains(resp, "notif-select")
        self.assertContains(resp, 'id="notif-bulk-form"')
        self.assertContains(resp, reverse("appointments:delete_notifications"))
        # Old inline layout must be gone.
        self.assertNotContains(resp, "max-width:720px")

    def test_owner_center_translates_to_english(self):
        self._owner_notif()
        self.main_doctor_a.preferred_language = "en"
        self.main_doctor_a.save(update_fields=["preferred_language"])
        self.client.force_login(self.main_doctor_a)
        resp = self.client.get(reverse("appointments:clinic_owner_notifications"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Select all")
        self.assertContains(resp, "Delete selected")
