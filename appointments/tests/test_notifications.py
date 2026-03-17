"""
Phase 7 — Notification Center tests.

Covers:
A. Visibility — user sees only own notifications; unread count is correct
B. Read/Unread actions — mark single, mark all, cross-user enforcement
C. Dashboard / navbar context — unread_notification_count in context processor
D. Linking — notifications_center resolves; target URLs enforce permissions
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from appointments.models import Appointment, AppointmentNotification, AppointmentType
from clinics.models import Clinic

User = get_user_model()


def make_user(phone, role="PATIENT", name="User"):
    return User.objects.create_user(phone=phone, password="testpass", name=name, role=role)


class NotificationVisibilityTests(TestCase):
    """A. A user can only see their own notifications."""

    def setUp(self):
        self.patient = make_user("0590000001", role="PATIENT", name="Ali")
        self.other = make_user("0590000002", role="PATIENT", name="Sara")
        self.main_doctor = make_user("0590000010", role="MAIN_DOCTOR", name="Dr. Owner")
        self.clinic = Clinic.objects.create(
            name="Test Clinic", address="Addr", main_doctor=self.main_doctor
        )
        self.apt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="General", duration_minutes=30, price=50
        )
        from datetime import date, time
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            appointment_date=date(2030, 1, 15),
            appointment_time=time(10, 0),
            status=Appointment.Status.CONFIRMED,
        )
        # Patient's notifications
        self.n1 = AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="تم تأكيد موعدك",
            message="موعد 15/1/2030",
            is_read=False,
        )
        self.n2 = AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_REMINDER,
            title="تذكير بموعدك",
            message="تذكير",
            is_read=True,
        )
        # Other user's notification
        self.n_other = AppointmentNotification.objects.create(
            patient=self.other,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="تم تأكيد موعدك",
            message="موعد للمريض الآخر",
            is_read=False,
        )

    def test_patient_sees_own_notifications_only(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.status_code, 200)
        notifications = response.context["notifications"]
        ids = [n.pk for n in notifications]
        self.assertIn(self.n1.pk, ids)
        self.assertIn(self.n2.pk, ids)
        self.assertNotIn(self.n_other.pk, ids)

    def test_unread_count_correct(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.context["unread_count"], 1)

    def test_read_notification_still_visible(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        ids = [n.pk for n in response.context["notifications"]]
        # n2 is read but should still appear
        self.assertIn(self.n2.pk, ids)

    def test_unauthenticated_redirects(self):
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])


class UnreadCountContextProcessorTests(TestCase):
    """C. unread_notification_count appears in template context for all pages."""

    def setUp(self):
        self.patient = make_user("0590000020", role="PATIENT", name="Fadi")
        self.main_doctor = make_user("0590000030", role="MAIN_DOCTOR", name="Dr. Samir")
        self.clinic = Clinic.objects.create(
            name="Clinic B", address="Addr", main_doctor=self.main_doctor
        )
        from datetime import date, time
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            appointment_date=date(2030, 2, 1),
            appointment_time=time(9, 0),
            status=Appointment.Status.CONFIRMED,
        )
        AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="تم حجز الموعد",
            message="msg",
            is_read=False,
        )
        AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_REMINDER,
            title="تذكير",
            message="msg2",
            is_read=False,
        )

    def test_unread_count_in_context(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.context["unread_notification_count"], 2)

    def test_anonymous_unread_count_zero(self):
        response = self.client.get(reverse("patients:dashboard"), follow=True)
        # Redirected to login; context processor should still return 0 without error
        # (Unauthenticated users won't see dashboard, but context processor is safe)
        self.assertNotIn("unread_notification_count", response.context or {})


class MarkNotificationReadTests(TestCase):
    """B. Mark single notification read; cross-user protection."""

    def setUp(self):
        self.patient = make_user("0590000040", role="PATIENT", name="Nour")
        self.other = make_user("0590000041", role="PATIENT", name="Hiba")
        self.main_doctor = make_user("0590000042", role="MAIN_DOCTOR", name="Dr. X")
        self.clinic = Clinic.objects.create(
            name="Clinic C", address="Addr", main_doctor=self.main_doctor
        )
        from datetime import date, time
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            appointment_date=date(2030, 3, 1),
            appointment_time=time(11, 0),
            status=Appointment.Status.CONFIRMED,
        )
        self.notif = AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="تم تأكيد موعدك",
            message="msg",
            is_read=False,
        )
        self.other_notif = AppointmentNotification.objects.create(
            patient=self.other,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="للمريضة الأخرى",
            message="msg",
            is_read=False,
        )

    def test_mark_own_notification_read(self):
        self.client.force_login(self.patient)
        response = self.client.post(
            reverse("appointments:mark_notification_read", args=[self.notif.pk]),
            data={"next": reverse("appointments:notifications_center")},
        )
        self.assertEqual(response.status_code, 302)
        self.notif.refresh_from_db()
        self.assertTrue(self.notif.is_read)

    def test_cannot_mark_other_users_notification(self):
        """Returns 404 when trying to mark another user's notification."""
        self.client.force_login(self.patient)
        response = self.client.post(
            reverse("appointments:mark_notification_read", args=[self.other_notif.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.other_notif.refresh_from_db()
        self.assertFalse(self.other_notif.is_read)

    def test_mark_read_requires_post(self):
        self.client.force_login(self.patient)
        response = self.client.get(
            reverse("appointments:mark_notification_read", args=[self.notif.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_mark_read_requires_login(self):
        response = self.client.post(
            reverse("appointments:mark_notification_read", args=[self.notif.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])


class MarkAllReadTests(TestCase):
    """B. Mark all notifications as read."""

    def setUp(self):
        self.patient = make_user("0590000050", role="PATIENT", name="Reem")
        self.other = make_user("0590000051", role="PATIENT", name="Lana")
        self.main_doctor = make_user("0590000052", role="MAIN_DOCTOR", name="Dr. Y")
        self.clinic = Clinic.objects.create(
            name="Clinic D", address="Addr", main_doctor=self.main_doctor
        )
        from datetime import date, time
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            appointment_date=date(2030, 4, 1),
            appointment_time=time(8, 0),
            status=Appointment.Status.CONFIRMED,
        )
        # 3 unread for patient
        for i in range(3):
            AppointmentNotification.objects.create(
                patient=self.patient,
                appointment=self.appointment,
                notification_type=AppointmentNotification.Type.APPOINTMENT_REMINDER,
                title=f"إشعار {i}",
                message="msg",
                is_read=False,
            )
        # 1 unread for other
        self.other_notif = AppointmentNotification.objects.create(
            patient=self.other,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="للمريضة الأخرى",
            message="msg",
            is_read=False,
        )

    def test_mark_all_read_only_affects_own(self):
        self.client.force_login(self.patient)
        response = self.client.post(reverse("appointments:mark_all_notifications_read"))
        self.assertEqual(response.status_code, 302)

        # Patient's notifications are all read now
        unread_count = AppointmentNotification.objects.filter(
            patient=self.patient, is_read=False
        ).count()
        self.assertEqual(unread_count, 0)

        # Other user's notification is untouched
        self.other_notif.refresh_from_db()
        self.assertFalse(self.other_notif.is_read)

    def test_mark_all_read_requires_post(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:mark_all_notifications_read"))
        self.assertEqual(response.status_code, 405)

    def test_mark_all_read_requires_login(self):
        response = self.client.post(reverse("appointments:mark_all_notifications_read"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])


class NotificationLinkingTests(TestCase):
    """D. Notification center renders; target URL resolves for patients."""

    def setUp(self):
        self.patient = make_user("0590000060", role="PATIENT", name="Khaled")
        self.main_doctor = make_user("0590000061", role="MAIN_DOCTOR", name="Dr. Z")
        self.clinic = Clinic.objects.create(
            name="Clinic E", address="Addr", main_doctor=self.main_doctor
        )
        from datetime import date, time
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            appointment_date=date(2030, 5, 1),
            appointment_time=time(10, 30),
            status=Appointment.Status.CONFIRMED,
        )
        AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_BOOKED,
            title="تم تأكيد موعدك",
            message="msg",
            is_read=False,
        )

    def test_notifications_page_200(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.status_code, 200)

    def test_target_url_annotated(self):
        """Each notification with an appointment has a target_url set."""
        self.client.force_login(self.patient)
        response = self.client.get(reverse("appointments:notifications_center"))
        notifications = response.context["notifications"]
        self.assertTrue(len(notifications) > 0)
        for notif in notifications:
            if notif.appointment_id:
                self.assertIsNotNone(notif.target_url)

    def test_my_appointments_page_accessible_after_click(self):
        """Target URL (my_appointments) returns 200 for authenticated patient."""
        self.client.force_login(self.patient)
        response = self.client.get(reverse("patients:my_appointments"))
        self.assertEqual(response.status_code, 200)

    def test_staff_notification_center_200(self):
        """Doctors also see their notification center without error."""
        doctor = make_user("0590000070", role="DOCTOR", name="Dr. Staff")
        AppointmentNotification.objects.create(
            patient=doctor,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
            title="إلغاء من مريض",
            message="msg",
            is_read=False,
        )
        self.client.force_login(doctor)
        response = self.client.get(reverse("appointments:notifications_center"))
        self.assertEqual(response.status_code, 200)
