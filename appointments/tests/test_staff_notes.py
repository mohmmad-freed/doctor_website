"""
Staff-note tests (secretary-authored notes on appointments + patient profiles).

Covers:
A. Service fan-out — notify_staff_note targets the right audience and excludes the author.
B. View creation — add note via the secretary endpoints (appointment + patient scope).
C. Authorization — a secretary may delete only her OWN note.
D. Doctor visibility — secretary-only notes never reach the doctor context.
"""

from datetime import date, time

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from appointments.models import Appointment, AppointmentNotification, AppointmentType
from appointments.services.appointment_notification_service import notify_staff_note
from clinics.models import Clinic, ClinicStaff
from patients.models import ClinicPatient, StaffNote

User = get_user_model()


def make_user(phone, role="PATIENT", name="User"):
    return User.objects.create_user(phone=phone, password="testpass", name=name, role=role)


class _BaseFixture(TestCase):
    """Owner + doctor + two secretaries + patient registered at one clinic."""

    def setUp(self):
        self.owner = make_user("0590002001", role="MAIN_DOCTOR", name="مالك")
        self.doctor = make_user("0590002002", role="DOCTOR", name="د. أحمد")
        self.secretary = make_user("0590002003", role="SECRETARY", name="منى")
        self.secretary2 = make_user("0590002004", role="SECRETARY", name="هدى")
        self.patient = make_user("0590002005", role="PATIENT", name="سامي")

        self.clinic = Clinic.objects.create(
            name="عيادة", address="Addr", main_doctor=self.owner
        )
        ClinicStaff.objects.create(clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True)
        self.secretary_staff = ClinicStaff.objects.create(
            clinic=self.clinic, user=self.secretary, role="SECRETARY", is_active=True
        )
        self.secretary2_staff = ClinicStaff.objects.create(
            clinic=self.clinic, user=self.secretary2, role="SECRETARY", is_active=True
        )
        self.apt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="Follow-up", duration_minutes=30, price=50
        )
        ClinicPatient.objects.create(clinic=self.clinic, patient=self.patient)

        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            appointment_type=self.apt_type,
            appointment_date=date(2030, 1, 15),
            appointment_time=time(10, 0),
            status=Appointment.Status.CONFIRMED,
            created_by=self.secretary,
        )

    def _note(self, audience, appointment=None, author=None, author_role="SECRETARY"):
        author = author or self.secretary
        return StaffNote.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            appointment=appointment,
            audience=audience,
            body="ملاحظة",
            author=author,
            author_name=author.name,
            author_role=author_role,
        )


# ── A. Service fan-out ────────────────────────────────────────────────────────


class ServiceFanOutTests(_BaseFixture):

    def test_secretary_note_notifies_other_secretaries_excluding_author(self):
        note = self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment)
        notify_staff_note(note, self.secretary)

        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
        )
        recipient_ids = set(notifs.values_list("patient_id", flat=True))
        # secretary2 notified; the author (secretary) is excluded; doctor not involved.
        self.assertEqual(recipient_ids, {self.secretary2.id})
        n = notifs.first()
        self.assertEqual(n.context_role, AppointmentNotification.ContextRole.SECRETARY)
        self.assertEqual(n.actor_role, AppointmentNotification.ActorRole.SECRETARY)

    def test_appointment_doctor_note_notifies_only_the_doctor(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment)
        notify_staff_note(note, self.secretary)

        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
        )
        self.assertEqual(set(notifs.values_list("patient_id", flat=True)), {self.doctor.id})
        self.assertEqual(notifs.first().context_role, AppointmentNotification.ContextRole.DOCTOR)

    def test_secretary_doctor_note_reaches_doctor_even_when_author_is_that_doctor(self):
        # Multi-role: a SECRETARY-authored doctor-note where the author also happens to
        # be the appointment's doctor — the doctor must still be notified.
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                          author=self.doctor, author_role="SECRETARY")
        notify_staff_note(note, self.doctor)
        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
        )
        self.assertEqual(set(notifs.values_list("patient_id", flat=True)), {self.doctor.id})

    def test_doctor_author_doctor_note_notifies_secretaries_not_doctors(self):
        # A DOCTOR writing a DOCTOR-audience note = a "note for secretaries" → notify the
        # secretaries (the other party), never a doctor.
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        notify_staff_note(note, self.doctor)
        self.assertEqual(
            set(AppointmentNotification.objects.filter(
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
            ).values_list("patient_id", flat=True)),
            {self.secretary.id, self.secretary2.id},
        )
        self.assertEqual(
            AppointmentNotification.objects.filter(
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
            ).count(),
            0,
        )

    def test_doctor_note_for_secretaries_reaches_authors_own_secretary_context(self):
        # Multi-role sole operator: the author is a doctor who is ALSO an active secretary.
        # Their "note for secretaries" must still land in their SECRETARY context (a different
        # portal) — the author is NOT excluded here (mirrors the secretary→doctor branch).
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.doctor, role="SECRETARY", is_active=True
        )
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        notify_staff_note(note, self.doctor)

        recipients = set(
            AppointmentNotification.objects.filter(
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
            ).values_list("patient_id", flat=True)
        )
        # The author (doctor who is also a secretary) is included alongside the others.
        self.assertEqual(recipients, {self.doctor.id, self.secretary.id, self.secretary2.id})
        own = AppointmentNotification.objects.get(patient=self.doctor)
        self.assertEqual(own.context_role, AppointmentNotification.ContextRole.SECRETARY)
        self.assertEqual(own.actor_role, AppointmentNotification.ActorRole.DOCTOR)

    def test_private_note_notifies_nobody(self):
        # DOCTOR_PRIVATE is visible to the authoring doctor only → notify nobody.
        note = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        notify_staff_note(note, self.doctor)
        self.assertEqual(AppointmentNotification.objects.count(), 0)

    def test_doctor_author_secretary_note_notifies_secretaries(self):
        # A DOCTOR writing a secretary-note → all secretaries notified, actor flagged DOCTOR.
        note = self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        notify_staff_note(note, self.doctor)
        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
        )
        self.assertEqual(
            set(notifs.values_list("patient_id", flat=True)),
            {self.secretary.id, self.secretary2.id},
        )
        self.assertEqual(notifs.first().actor_role, AppointmentNotification.ActorRole.DOCTOR)

    def test_patient_doctor_note_notifies_treating_doctors(self):
        # Patient-scoped note (no appointment) → treating doctors (have appts with patient).
        note = self._note(StaffNote.Audience.DOCTOR, appointment=None)
        notify_staff_note(note, self.secretary)

        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
        )
        self.assertEqual(set(notifs.values_list("patient_id", flat=True)), {self.doctor.id})
        n = notifs.first()
        # No appointment, but subject_patient set for routing.
        self.assertIsNone(n.appointment_id)
        self.assertEqual(n.subject_patient_id, self.patient.id)

    def test_patient_doctor_note_no_treating_doctor_notifies_nobody(self):
        # A patient with no appointments anywhere → no doctor to notify.
        other_patient = make_user("0590002009", role="PATIENT", name="بلا مواعيد")
        ClinicPatient.objects.create(clinic=self.clinic, patient=other_patient)
        note = StaffNote.objects.create(
            clinic=self.clinic, patient=other_patient, appointment=None,
            audience=StaffNote.Audience.DOCTOR, body="x",
            author=self.secretary, author_name=self.secretary.name, author_role="SECRETARY",
        )
        notify_staff_note(note, self.secretary)
        self.assertEqual(
            AppointmentNotification.objects.filter(
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
            ).count(),
            0,
        )


# ── B/C. Views + authorization ────────────────────────────────────────────────


class ViewTests(_BaseFixture):

    def test_appointment_note_add_creates_note_and_notifies(self):
        self.client.force_login(self.secretary)
        url = reverse("secretary:appointment_note_add", args=[self.appointment.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "DOCTOR", "body": "افحص الضغط"})
        self.assertEqual(resp.status_code, 302)
        note = StaffNote.objects.get(appointment=self.appointment)
        self.assertEqual(note.audience, StaffNote.Audience.DOCTOR)
        self.assertEqual(note.author_id, self.secretary.id)
        self.assertEqual(note.patient_id, self.patient.id)
        # Doctor got a notification.
        self.assertTrue(
            AppointmentNotification.objects.filter(
                patient=self.doctor,
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR,
            ).exists()
        )

    def test_patient_note_add_creates_patient_scoped_note(self):
        self.client.force_login(self.secretary)
        url = reverse("secretary:patient_note_add", args=[self.patient.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "SECRETARY", "body": "مريض متعاون"})
        self.assertEqual(resp.status_code, 302)
        note = StaffNote.objects.get(patient=self.patient, appointment__isnull=True)
        self.assertEqual(note.audience, StaffNote.Audience.SECRETARY)

    def test_empty_body_is_rejected(self):
        self.client.force_login(self.secretary)
        url = reverse("secretary:appointment_note_add", args=[self.appointment.id])
        self.client.post(url, {"audience": "DOCTOR", "body": "   "})
        self.assertFalse(StaffNote.objects.exists())

    def test_secretary_can_delete_own_note(self):
        note = self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment, author=self.secretary)
        self.client.force_login(self.secretary)
        url = reverse("secretary:appointment_note_delete", args=[self.appointment.id, note.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=note.id).exists())

    def test_secretary_cannot_delete_other_secretary_note(self):
        note = self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment, author=self.secretary2)
        self.client.force_login(self.secretary)
        url = reverse("secretary:appointment_note_delete", args=[self.appointment.id, note.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())

    def test_patient_note_delete_authorization(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=None, author=self.secretary2)
        self.client.force_login(self.secretary)
        url = reverse("secretary:patient_note_delete", args=[self.patient.id, note.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())

    def test_bilingual_messages_respect_active_language(self):
        from django.utils import translation
        from secretary.views import _bilingual
        with translation.override("en"):
            self.assertEqual(_bilingual("عربي", "English"), "English")
        with translation.override("ar"):
            self.assertEqual(_bilingual("عربي", "English"), "عربي")


# ── D. Routing for patient-scoped note notifications ──────────────────────────


class RoutingTests(_BaseFixture):

    def test_patient_note_notification_routes_to_patient_profile(self):
        from appointments.notification_views import _resolve_appointment_url

        note = self._note(StaffNote.Audience.DOCTOR, appointment=None)
        notify_staff_note(note, self.secretary)
        notif = AppointmentNotification.objects.get(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
        )
        url = _resolve_appointment_url(notif)
        self.assertEqual(url, reverse("doctors:patient_workspace", kwargs={"patient_id": self.patient.id}))


# ── E. Doctor-side authoring ──────────────────────────────────────────────────


class DoctorViewTests(_BaseFixture):

    def test_doctor_cannot_add_secretary_note_via_portal(self):
        # The doctor portal only offers private / for-secretaries — never secretary-only.
        self.client.force_login(self.doctor)
        url = reverse("doctors:appointment_note_add", args=[self.appointment.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "SECRETARY", "body": "x"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(StaffNote.objects.exists())

    def test_doctor_adds_private_note_notifies_nobody(self):
        self.client.force_login(self.doctor)
        url = reverse("doctors:appointment_note_add", args=[self.appointment.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "DOCTOR_PRIVATE", "body": "reminder to self"})
        self.assertEqual(resp.status_code, 302)
        note = StaffNote.objects.get(appointment=self.appointment)
        self.assertEqual(note.audience, StaffNote.Audience.DOCTOR_PRIVATE)
        self.assertEqual(note.author_role, "DOCTOR")
        self.assertEqual(AppointmentNotification.objects.count(), 0)

    def test_doctor_for_secretaries_note_notifies_secretaries(self):
        self.client.force_login(self.doctor)
        url = reverse("doctors:appointment_note_add", args=[self.appointment.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "DOCTOR", "body": "please call the lab"})
        self.assertEqual(resp.status_code, 302)
        note = StaffNote.objects.get(appointment=self.appointment)
        self.assertEqual(note.audience, StaffNote.Audience.DOCTOR)
        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
        )
        # Both secretaries notified; the doctor never notifies a doctor for this.
        self.assertEqual(
            set(notifs.values_list("patient_id", flat=True)),
            {self.secretary.id, self.secretary2.id},
        )
        self.assertEqual(notifs.first().actor_role, AppointmentNotification.ActorRole.DOCTOR)
        self.assertEqual(
            AppointmentNotification.objects.filter(
                notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_DOCTOR
            ).count(),
            0,
        )

    def test_doctor_cannot_add_note_on_other_doctors_appointment(self):
        other = make_user("0590002099", role="DOCTOR", name="طبيب آخر")
        ClinicStaff.objects.create(clinic=self.clinic, user=other, role="DOCTOR", is_active=True)
        self.client.force_login(other)
        url = reverse("doctors:appointment_note_add", args=[self.appointment.id])
        resp = self.client.post(url, {"audience": "DOCTOR", "body": "x"})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(StaffNote.objects.exists())

    def test_doctor_deletes_own_note_only(self):
        sec_note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment, author=self.secretary)
        self.client.force_login(self.doctor)
        resp = self.client.post(
            reverse("doctors:appointment_note_delete", args=[self.appointment.id, sec_note.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=sec_note.id).exists())

        own = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                         author=self.doctor, author_role="DOCTOR")
        resp2 = self.client.post(
            reverse("doctors:appointment_note_delete", args=[self.appointment.id, own.id])
        )
        self.assertEqual(resp2.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=own.id).exists())


# ── F. Doctor visibility (private + secretary-only scoping) ───────────────────


class DoctorVisibilityTests(_BaseFixture):

    def test_doctor_never_sees_secretary_only_note_even_his_own(self):
        from doctors.views import _appointment_doctor_notes
        # A secretary-only note the doctor authored himself (e.g. via the secretary portal).
        self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment,
                   author=self.doctor, author_role="DOCTOR")
        visible = _appointment_doctor_notes(self.appointment, viewer=self.doctor)
        self.assertEqual(visible, [])

    def test_doctor_sees_own_private_note_but_not_another_doctors(self):
        from doctors.views import _appointment_doctor_notes
        other = make_user("0590002098", role="DOCTOR", name="طبيب آخر")
        mine = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                   author=other, author_role="DOCTOR")
        visible = _appointment_doctor_notes(self.appointment, viewer=self.doctor)
        self.assertEqual([n.id for n in visible], [mine.id])

    def test_doctor_sees_shared_doctor_audience_note(self):
        from doctors.views import _appointment_doctor_notes
        shared = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                            author=self.secretary)
        visible = _appointment_doctor_notes(self.appointment, viewer=self.doctor)
        self.assertEqual([n.id for n in visible], [shared.id])

    def test_patient_profile_helper_scopes_private_to_author(self):
        from doctors.views import _patient_doctor_notes
        mine = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=None,
                          author=self.doctor, author_role="DOCTOR")
        shared = self._note(StaffNote.Audience.DOCTOR, appointment=None, author=self.secretary)
        self._note(StaffNote.Audience.SECRETARY, appointment=None, author=self.secretary)
        visible = _patient_doctor_notes(self.patient, [self.clinic.id], viewer=self.doctor)
        self.assertEqual({n.id for n in visible}, {mine.id, shared.id})

    def test_secretary_overview_excludes_doctor_private_notes(self):
        self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                   author=self.doctor, author_role="DOCTOR")
        shared = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                            author=self.secretary)
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("secretary:appointment_overview", args=[self.appointment.id]))
        ids = {n.id for n in resp.context["appointment_notes"]}
        self.assertEqual(ids, {shared.id})


# ── G. Doctor patient-profile authoring ──────────────────────────────────────


class DoctorProfileNoteTests(_BaseFixture):

    def test_doctor_adds_private_profile_note(self):
        self.client.force_login(self.doctor)
        url = reverse("doctors:patient_note_add", args=[self.patient.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "DOCTOR_PRIVATE", "body": "watch BP"})
        self.assertEqual(resp.status_code, 302)
        note = StaffNote.objects.get(patient=self.patient, appointment__isnull=True)
        self.assertEqual(note.audience, StaffNote.Audience.DOCTOR_PRIVATE)
        self.assertEqual(note.author_role, "DOCTOR")
        self.assertEqual(note.clinic_id, self.clinic.id)
        self.assertEqual(AppointmentNotification.objects.count(), 0)

    def test_doctor_adds_for_secretaries_profile_note_notifies_secretaries(self):
        self.client.force_login(self.doctor)
        url = reverse("doctors:patient_note_add", args=[self.patient.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"audience": "DOCTOR", "body": "schedule a follow-up"})
        self.assertEqual(resp.status_code, 302)
        notifs = AppointmentNotification.objects.filter(
            notification_type=AppointmentNotification.Type.STAFF_NOTE_FOR_SECRETARY
        )
        self.assertEqual(
            set(notifs.values_list("patient_id", flat=True)),
            {self.secretary.id, self.secretary2.id},
        )

    def test_doctor_cannot_add_secretary_profile_note(self):
        self.client.force_login(self.doctor)
        url = reverse("doctors:patient_note_add", args=[self.patient.id])
        self.client.post(url, {"audience": "SECRETARY", "body": "x"})
        self.assertFalse(StaffNote.objects.exists())

    def test_doctor_deletes_own_profile_note_only(self):
        sec_note = self._note(StaffNote.Audience.DOCTOR, appointment=None, author=self.secretary)
        self.client.force_login(self.doctor)
        resp = self.client.post(
            reverse("doctors:patient_note_delete", args=[self.patient.id, sec_note.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=sec_note.id).exists())

        own = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=None,
                         author=self.doctor, author_role="DOCTOR")
        resp2 = self.client.post(
            reverse("doctors:patient_note_delete", args=[self.patient.id, own.id])
        )
        self.assertEqual(resp2.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=own.id).exists())


# ── H. can_delete unit matrix ─────────────────────────────────────────────────


class CanDeleteUnitTests(_BaseFixture):

    def test_requires_author_and_matching_portal_role(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.appointment,
                          author=self.secretary, author_role="SECRETARY")
        self.assertTrue(note.can_delete(self.secretary, "SECRETARY"))    # author + same portal
        self.assertFalse(note.can_delete(self.secretary, "DOCTOR"))      # author + wrong portal
        self.assertFalse(note.can_delete(self.secretary2, "SECRETARY"))  # non-author
        self.assertFalse(note.can_delete(None, "SECRETARY"))             # no user


# ── I. Cross-portal delete isolation (multi-role users + existence oracle) ────


class CrossPortalDeleteTests(_BaseFixture):
    """A note may be deleted only from the portal it was authored in. Covers the
    multi-role (DOCTOR + SECRETARY) user who is both the author and able to reach both
    portals — the exact gap that author-only deletion left open."""

    def setUp(self):
        super().setUp()
        # One person who is BOTH a doctor and a secretary at the clinic.
        self.multi = make_user("0590002010", role="DOCTOR", name="د. مزدوج")
        self.multi.roles = ["DOCTOR", "SECRETARY"]
        self.multi.save(update_fields=["roles"])
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.multi, role="SECRETARY", is_active=True
        )
        # An appointment where the multi-role user is the treating doctor.
        self.multi_appt = Appointment.objects.create(
            patient=self.patient, clinic=self.clinic, doctor=self.multi,
            appointment_type=self.apt_type, appointment_date=date(2030, 2, 20),
            appointment_time=time(11, 0), status=Appointment.Status.CONFIRMED,
            created_by=self.multi,
        )

    # — appointment-scoped —

    def test_secretary_authored_appt_note_blocked_in_doctor_portal(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.multi_appt,
                          author=self.multi, author_role="SECRETARY")
        self.client.force_login(self.multi)
        blocked = self.client.post(
            reverse("doctors:appointment_note_delete", args=[self.multi_appt.id, note.id])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())
        # ...but the authoring (secretary) portal still deletes it.
        allowed = self.client.post(
            reverse("secretary:appointment_note_delete", args=[self.multi_appt.id, note.id])
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=note.id).exists())

    def test_doctor_authored_appt_note_blocked_in_secretary_portal(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=self.multi_appt,
                          author=self.multi, author_role="DOCTOR")
        self.client.force_login(self.multi)
        blocked = self.client.post(
            reverse("secretary:appointment_note_delete", args=[self.multi_appt.id, note.id])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())
        allowed = self.client.post(
            reverse("doctors:appointment_note_delete", args=[self.multi_appt.id, note.id])
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=note.id).exists())

    # — patient-profile-scoped —

    def test_secretary_authored_profile_note_blocked_in_doctor_portal(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=None,
                          author=self.multi, author_role="SECRETARY")
        self.client.force_login(self.multi)
        blocked = self.client.post(
            reverse("doctors:patient_note_delete", args=[self.patient.id, note.id])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())
        allowed = self.client.post(
            reverse("secretary:patient_note_delete", args=[self.patient.id, note.id])
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=note.id).exists())

    def test_doctor_authored_profile_note_blocked_in_secretary_portal(self):
        note = self._note(StaffNote.Audience.DOCTOR, appointment=None,
                          author=self.multi, author_role="DOCTOR")
        self.client.force_login(self.multi)
        blocked = self.client.post(
            reverse("secretary:patient_note_delete", args=[self.patient.id, note.id])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())
        allowed = self.client.post(
            reverse("doctors:patient_note_delete", args=[self.patient.id, note.id])
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertFalse(StaffNote.objects.filter(id=note.id).exists())

    # — existence-oracle hardening: a hidden note returns 404, not 403 —

    def test_doctor_delete_endpoint_hides_secretary_only_note(self):
        note = self._note(StaffNote.Audience.SECRETARY, appointment=self.appointment,
                          author=self.secretary)
        self.client.force_login(self.doctor)
        resp = self.client.post(
            reverse("doctors:appointment_note_delete", args=[self.appointment.id, note.id])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())

    def test_secretary_delete_endpoint_hides_doctor_private_note(self):
        note = self._note(StaffNote.Audience.DOCTOR_PRIVATE, appointment=self.appointment,
                          author=self.doctor, author_role="DOCTOR")
        self.client.force_login(self.secretary)
        resp = self.client.post(
            reverse("secretary:appointment_note_delete", args=[self.appointment.id, note.id])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(StaffNote.objects.filter(id=note.id).exists())
