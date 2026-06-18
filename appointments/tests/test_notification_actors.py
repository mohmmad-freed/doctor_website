"""
Notification actor flag tests.

Covers:
A. Helper classification — _actor_from_booking / _actor_from_staff
B. Service population — booking/staff notifiers store actor_role + actor_name
C. Doctor follow-up booking notifies secretary + owner (not the booking doctor)
D. Template name-visibility per portal (secretary name hidden in patient portal,
   doctor name shown in secretary portal)
"""

from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from appointments.models import Appointment, AppointmentNotification, AppointmentType
from appointments.services.appointment_notification_service import (
    _actor_from_booking,
    _actor_from_staff,
    notify_appointment_booked,
    notify_staff_appointment_booked,
)
from clinics.models import Clinic, ClinicStaff
from patients.models import ClinicPatient

User = get_user_model()


def make_user(phone, role="PATIENT", name="User"):
    return User.objects.create_user(phone=phone, password="testpass", name=name, role=role)


class _BaseClinicFixture(TestCase):
    """Owner + doctor + secretary + patient registered at one clinic."""

    def setUp(self):
        self.owner = make_user("0590001001", role="MAIN_DOCTOR", name="مالك العيادة")
        self.doctor = make_user("0590001002", role="DOCTOR", name="أحمد الطبيب")
        self.secretary = make_user("0590001003", role="SECRETARY", name="منى السكرتيرة")
        self.patient = make_user("0590001004", role="PATIENT", name="سامي المريض")

        self.clinic = Clinic.objects.create(
            name="عيادة الاختبار", address="Addr", main_doctor=self.owner
        )
        self.doctor_staff = ClinicStaff.objects.create(
            clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True
        )
        self.secretary_staff = ClinicStaff.objects.create(
            clinic=self.clinic, user=self.secretary, role="SECRETARY", is_active=True
        )
        self.apt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="Follow-up", duration_minutes=30, price=50
        )
        ClinicPatient.objects.create(clinic=self.clinic, patient=self.patient)

    def _appointment(self, created_by, status=Appointment.Status.CONFIRMED, day=15):
        return Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            appointment_type=self.apt_type,
            appointment_date=date(2030, 1, day),
            appointment_time=time(10, 0),
            status=status,
            created_by=created_by,
        )


class ActorHelperTests(_BaseClinicFixture):
    """A. Classification helpers."""

    def test_booking_actor_patient(self):
        appt = self._appointment(created_by=self.patient)
        role, name = _actor_from_booking(appt)
        self.assertEqual(role, AppointmentNotification.ActorRole.PATIENT)
        self.assertEqual(name, self.patient.name)

    def test_booking_actor_doctor(self):
        appt = self._appointment(created_by=self.doctor)
        role, name = _actor_from_booking(appt)
        self.assertEqual(role, AppointmentNotification.ActorRole.DOCTOR)
        self.assertEqual(name, self.doctor.name)

    def test_booking_actor_secretary(self):
        appt = self._appointment(created_by=self.secretary)
        role, name = _actor_from_booking(appt)
        self.assertEqual(role, AppointmentNotification.ActorRole.SECRETARY)
        self.assertEqual(name, self.secretary.name)

    def test_booking_actor_blank_without_creator(self):
        appt = self._appointment(created_by=None)
        self.assertEqual(_actor_from_booking(appt), ("", ""))

    def test_staff_actor_mapping(self):
        self.assertEqual(
            _actor_from_staff(self.secretary_staff),
            (AppointmentNotification.ActorRole.SECRETARY, self.secretary.name),
        )
        self.assertEqual(
            _actor_from_staff(self.doctor_staff),
            (AppointmentNotification.ActorRole.DOCTOR, self.doctor.name),
        )
        self.assertEqual(_actor_from_staff(None), ("", ""))


class ServiceActorPopulationTests(_BaseClinicFixture):
    """B + C. Notifiers store the actor; doctor booking excludes the doctor."""

    def test_patient_selfbook_sets_patient_actor(self):
        appt = self._appointment(created_by=self.patient)
        notify_appointment_booked(appt)
        notif = AppointmentNotification.objects.get(
            patient=self.patient,
            context_role=AppointmentNotification.ContextRole.PATIENT,
        )
        self.assertEqual(notif.actor_role, AppointmentNotification.ActorRole.PATIENT)
        self.assertEqual(notif.actor_name, self.patient.name)

    def test_staff_booked_carries_actor(self):
        appt = self._appointment(created_by=self.secretary)
        notify_staff_appointment_booked(appt)
        for ctx in (
            AppointmentNotification.ContextRole.DOCTOR,
            AppointmentNotification.ContextRole.SECRETARY,
            AppointmentNotification.ContextRole.CLINIC_OWNER,
        ):
            notif = AppointmentNotification.objects.filter(
                appointment=appt, context_role=ctx
            ).first()
            self.assertIsNotNone(notif, f"missing notification for {ctx}")
            self.assertEqual(notif.actor_role, AppointmentNotification.ActorRole.SECRETARY)
            self.assertEqual(notif.actor_name, self.secretary.name)

    def test_exclude_user_ids_skips_recipient(self):
        appt = self._appointment(created_by=self.doctor)
        notify_staff_appointment_booked(appt, exclude_user_ids=[self.doctor.id])
        # Doctor (the actor) is excluded; secretary + owner still notified.
        self.assertFalse(
            AppointmentNotification.objects.filter(
                appointment=appt,
                patient=self.doctor,
                context_role=AppointmentNotification.ContextRole.DOCTOR,
            ).exists()
        )
        self.assertTrue(
            AppointmentNotification.objects.filter(
                appointment=appt, patient=self.secretary
            ).exists()
        )
        self.assertTrue(
            AppointmentNotification.objects.filter(
                appointment=appt, patient=self.owner
            ).exists()
        )

    def test_secretary_portal_booking_by_doctor_secretary_flags_secretary(self):
        """A user holding BOTH doctor and secretary roles, booking via the
        secretary portal (even selecting themselves as the doctor), must be
        flagged SECRETARY — the portal/flow decides, not created_by inference."""
        from secretary.services import secretary_book_appointment

        docsec = make_user("0590001099", role="DOCTOR", name="طبيب وسكرتير")
        docsec.roles = ["DOCTOR", "SECRETARY"]
        docsec.save(update_fields=["roles"])
        ClinicStaff.objects.create(clinic=self.clinic, user=docsec, role="DOCTOR", is_active=True)
        ClinicStaff.objects.create(clinic=self.clinic, user=docsec, role="SECRETARY", is_active=True)

        with self.captureOnCommitCallbacks(execute=True):
            appt = secretary_book_appointment(
                patient=self.patient,
                doctor_id=docsec.id,  # books themselves as the doctor
                clinic_id=self.clinic.id,
                appointment_type_id=self.apt_type.id,
                appointment_date=date.today() + timedelta(days=5),
                appointment_time=time(11, 0),
                created_by=docsec,
            )

        notifs = AppointmentNotification.objects.filter(appointment=appt)
        self.assertTrue(notifs.exists())
        for n in notifs:
            self.assertEqual(
                n.actor_role,
                AppointmentNotification.ActorRole.SECRETARY,
                f"context {n.context_role} mis-flagged as {n.actor_role}",
            )
            self.assertEqual(n.actor_name, docsec.name)

    def test_doctor_followup_notifies_staff_not_doctor(self):
        from appointments.services.doctor_booking_service import schedule_followup

        with self.captureOnCommitCallbacks(execute=True):
            appt = schedule_followup(
                doctor=self.doctor,
                patient_id=self.patient.id,
                clinic_id=self.clinic.id,
                appointment_date=date.today() + timedelta(days=7),
                appointment_time=time(9, 30),
                appointment_type_id=self.apt_type.id,
                allow_conflict=True,
            )

        # Secretary + owner notified, flagged as a doctor-initiated booking.
        sec = AppointmentNotification.objects.get(
            appointment=appt, patient=self.secretary
        )
        self.assertEqual(sec.notification_type, AppointmentNotification.Type.APPOINTMENT_BOOKED)
        self.assertEqual(sec.actor_role, AppointmentNotification.ActorRole.DOCTOR)
        self.assertEqual(sec.actor_name, self.doctor.name)
        self.assertTrue(
            AppointmentNotification.objects.filter(appointment=appt, patient=self.owner).exists()
        )
        # The booking doctor is NOT notified about their own action.
        self.assertFalse(
            AppointmentNotification.objects.filter(appointment=appt, patient=self.doctor).exists()
        )


class ActorNameVisibilityTests(_BaseClinicFixture):
    """D. Per-portal name visibility in the rendered notification center."""

    SECRET_NAME = "منى-سيكرتيرة-فريدة"
    DOCTOR_NAME = "أحمد-طبيب-فريد"

    def _make_notif(self, recipient, context_role, actor_role, actor_name,
                    ntype=AppointmentNotification.Type.APPOINTMENT_BOOKED):
        appt = self._appointment(created_by=self.patient, day=20)
        return AppointmentNotification.objects.create(
            patient=recipient,
            appointment=appt,
            context_role=context_role,
            notification_type=ntype,
            title="عنوان عام",
            message="نص عام بدون اسم",
            actor_role=actor_role,
            actor_name=actor_name,
            is_read=False,
        )

    def test_secretary_name_hidden_in_patient_portal(self):
        self._make_notif(
            self.patient,
            AppointmentNotification.ContextRole.PATIENT,
            AppointmentNotification.ActorRole.SECRETARY,
            self.SECRET_NAME,
            ntype=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
        )
        self.client.force_login(self.patient)
        resp = self.client.get(reverse("appointments:patient_notifications"))
        self.assertEqual(resp.status_code, 200)
        # The "by secretary" flag shows, but the secretary's name must not.
        self.assertNotContains(resp, self.SECRET_NAME)

    def test_secretary_name_shown_in_secretary_portal(self):
        self._make_notif(
            self.secretary,
            AppointmentNotification.ContextRole.SECRETARY,
            AppointmentNotification.ActorRole.SECRETARY,
            self.SECRET_NAME,
        )
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.SECRET_NAME)

    def test_doctor_name_shown_in_secretary_portal(self):
        self._make_notif(
            self.secretary,
            AppointmentNotification.ContextRole.SECRETARY,
            AppointmentNotification.ActorRole.DOCTOR,
            self.DOCTOR_NAME,
        )
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("appointments:secretary_notifications"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.DOCTOR_NAME)
