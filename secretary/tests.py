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
        appt = Appointment.objects.get(patient=self.patient_a, clinic=self.clinic_a)
        self.assertEqual(appt.created_by, self.secretary_a)
        # After booking, the view redirects to the new appointment's detail page
        # so the secretary gets immediate confirmation of what they just booked.
        self.assertRedirects(
            resp,
            reverse("secretary:appointment_detail", kwargs={"appointment_id": appt.id}),
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
