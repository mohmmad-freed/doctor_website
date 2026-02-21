"""
Tests for the appointment booking feature.

Covers:
- Booking service (happy path, validations, race conditions)
- API endpoint (POST /appointments/api/book/)
- Edge cases (past dates, unavailable slots, invalid data)
"""

from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from appointments.models import Appointment, AppointmentType
from appointments.services import (
    book_appointment,
    BookingError,
    InvalidSlotError,
    PastDateError,
    SlotUnavailableError,
)
from clinics.models import Clinic
from doctors.models import DoctorAvailability

User = get_user_model()


class BookingTestMixin:
    """Shared setup for booking tests."""

    def setUp(self):
        # Users
        self.main_doctor = User.objects.create_user(
            phone="0591000001",
            password="testpass123",
            name="Dr. Owner",
            role="MAIN_DOCTOR",
        )
        self.doctor = User.objects.create_user(
            phone="0591000002",
            password="testpass123",
            name="Dr. Ahmad",
            role="DOCTOR",
        )
        self.patient = User.objects.create_user(
            phone="0591000003",
            password="testpass123",
            name="Patient Ali",
            role="PATIENT",
        )
        self.patient2 = User.objects.create_user(
            phone="0591000004",
            password="testpass123",
            name="Patient Sara",
            role="PATIENT",
        )

        # Clinic
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            address="Test Address",
            phone="0591111111",
            email="test@clinic.com",
            main_doctor=self.main_doctor,
        )

        # Find next Monday for consistent test dates
        today = date.today()
        days_ahead = 0 - today.weekday()  # Monday is 0
        if days_ahead <= 0:
            days_ahead += 7
        self.next_monday = today + timedelta(days=days_ahead)

        # Doctor availability: Monday 09:00-12:00
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(12, 0),
        )

        # Appointment type: 30 min, 100 ILS
        self.appointment_type = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic,
            name="General Checkup",
            duration_minutes=30,
            price=Decimal("100.00"),
        )


# ═══════════════════════════════════════════════════════════════════
#  Service Layer Tests
# ═══════════════════════════════════════════════════════════════════


class BookingServiceTests(BookingTestMixin, TestCase):
    """Tests for the book_appointment service function."""

    def test_successful_booking(self):
        """Happy path: patient books an available slot."""
        appointment = book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
            reason="Annual checkup",
        )

        self.assertIsNotNone(appointment.id)
        self.assertEqual(appointment.patient, self.patient)
        self.assertEqual(appointment.doctor, self.doctor)
        self.assertEqual(appointment.clinic, self.clinic)
        self.assertEqual(appointment.appointment_type, self.appointment_type)
        self.assertEqual(appointment.appointment_date, self.next_monday)
        self.assertEqual(appointment.appointment_time, time(9, 0))
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)
        self.assertEqual(appointment.reason, "Annual checkup")
        self.assertEqual(appointment.created_by, self.patient)

    def test_booking_links_to_patient(self):
        """Appointment is correctly linked to the booking patient."""
        appointment = book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )
        self.assertEqual(
            Appointment.objects.filter(patient=self.patient).count(), 1
        )
        self.assertEqual(appointment.patient.id, self.patient.id)

    def test_booking_links_to_doctor(self):
        """Appointment is correctly linked to the selected doctor."""
        appointment = book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
        )
        self.assertEqual(appointment.doctor.id, self.doctor.id)

    def test_past_date_raises_error(self):
        """Booking a past date should raise PastDateError."""
        yesterday = date.today() - timedelta(days=1)
        with self.assertRaises(PastDateError):
            book_appointment(
                patient=self.patient,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appointment_type.id,
                appointment_date=yesterday,
                appointment_time=time(9, 0),
            )

    def test_invalid_slot_time_raises_error(self):
        """Booking a time that doesn't match a generated slot should fail."""
        with self.assertRaises(InvalidSlotError):
            book_appointment(
                patient=self.patient,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appointment_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 15),  # Not a valid 30-min slot boundary
            )

    def test_invalid_appointment_type_raises_error(self):
        """Non-existent appointment type should fail."""
        with self.assertRaises(BookingError):
            book_appointment(
                patient=self.patient,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=99999,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )

    def test_invalid_clinic_raises_error(self):
        """Non-existent clinic should fail."""
        with self.assertRaises(BookingError):
            book_appointment(
                patient=self.patient,
                doctor_id=self.doctor.id,
                clinic_id=99999,
                appointment_type_id=self.appointment_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )

    def test_slot_already_booked_raises_error(self):
        """Booking an already-taken slot should raise SlotUnavailableError."""
        # First booking succeeds
        book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )

        # Second booking for same slot fails
        with self.assertRaises(SlotUnavailableError):
            book_appointment(
                patient=self.patient2,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appointment_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )

    def test_cancelled_slot_can_be_rebooked(self):
        """A cancelled appointment's slot should become available again."""
        appointment = book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )

        # Cancel the appointment
        appointment.status = Appointment.Status.CANCELLED
        appointment.save()

        # Re-book the same slot with a different patient
        new_appointment = book_appointment(
            patient=self.patient2,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )
        self.assertIsNotNone(new_appointment.id)
        self.assertEqual(new_appointment.status, Appointment.Status.CONFIRMED)

    def test_global_conflict_check(self):
        """
        R-03: A slot booked at Clinic A should block the same time
        at Clinic B for the same doctor.

        DoctorAvailability enforces non-overlapping windows across clinics,
        so we use non-overlapping availability and directly create a
        conflicting appointment to test the booking service's global check.
        """
        clinic_b = Clinic.objects.create(
            name="Clinic B",
            address="Address B",
            phone="0592222222",
            email="b@clinic.com",
            main_doctor=self.main_doctor,
        )
        # Clinic B: non-overlapping availability (14:00-17:00)
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=clinic_b,
            day_of_week=0,
            start_time=time(14, 0),
            end_time=time(17, 0),
        )
        type_b = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=clinic_b,
            name="General Checkup",
            duration_minutes=30,
            price=Decimal("100.00"),
        )

        # Simulate existing appointment at Clinic A at 14:00
        # (directly created to represent a cross-clinic conflict)
        Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            appointment_type=self.appointment_type,
            appointment_date=self.next_monday,
            appointment_time=time(14, 0),
            status=Appointment.Status.CONFIRMED,
        )

        # Try to book at Clinic B at 14:00 — should fail (global conflict)
        with self.assertRaises(SlotUnavailableError):
            book_appointment(
                patient=self.patient2,
                doctor_id=self.doctor.id,
                clinic_id=clinic_b.id,
                appointment_type_id=type_b.id,
                appointment_date=self.next_monday,
                appointment_time=time(14, 0),
            )

    def test_different_time_same_day_ok(self):
        """Different time slots on the same day should both work."""
        book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )

        appointment2 = book_appointment(
            patient=self.patient2,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
        )
        self.assertIsNotNone(appointment2.id)

    def test_booking_without_reason(self):
        """Booking without a reason should work (reason is optional)."""
        appointment = book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appointment_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )
        self.assertEqual(appointment.reason, "")

    def test_no_availability_day_raises_error(self):
        """Booking on a day with no availability should fail."""
        # Tuesday — no availability defined
        tuesday = self.next_monday + timedelta(days=1)
        with self.assertRaises(InvalidSlotError):
            book_appointment(
                patient=self.patient,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appointment_type.id,
                appointment_date=tuesday,
                appointment_time=time(9, 0),
            )


# ═══════════════════════════════════════════════════════════════════
#  API Endpoint Tests
# ═══════════════════════════════════════════════════════════════════


class BookAppointmentAPITests(BookingTestMixin, TestCase):
    """Tests for POST /appointments/api/book/ endpoint."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("appointments:api_book_appointment")

    def _book_payload(self, **overrides):
        """Helper to generate a valid booking payload."""
        payload = {
            "doctor_id": self.doctor.id,
            "clinic_id": self.clinic.id,
            "appointment_type_id": self.appointment_type.id,
            "appointment_date": self.next_monday.isoformat(),
            "appointment_time": "09:00",
            "reason": "Test booking",
        }
        payload.update(overrides)
        return payload

    def test_successful_api_booking(self):
        """POST with valid data returns 201 and appointment details."""
        self.client.force_authenticate(user=self.patient)
        response = self.client.post(self.url, self._book_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        self.assertEqual(response.data["status"], "CONFIRMED")
        self.assertEqual(response.data["doctor_name"], "Dr. Ahmad")
        self.assertEqual(response.data["clinic_name"], "Test Clinic")
        self.assertEqual(response.data["appointment_type_name"], "General Checkup")

    def test_unauthenticated_returns_forbidden(self):
        """Unauthenticated request should be rejected."""
        response = self.client.post(self.url, self._book_payload(), format="json")
        self.assertIn(response.status_code, [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ])

    def test_non_patient_returns_403(self):
        """Non-patient user should be rejected."""
        self.client.force_authenticate(user=self.doctor)
        response = self.client.post(self.url, self._book_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_fields_returns_400(self):
        """Missing required fields should return validation errors."""
        self.client.force_authenticate(user=self.patient)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_slot_unavailable_returns_409(self):
        """Booking an already-taken slot via API should return 409."""
        self.client.force_authenticate(user=self.patient)
        # First booking
        self.client.post(self.url, self._book_payload(), format="json")

        # Second booking (different patient, same slot)
        self.client.force_authenticate(user=self.patient2)
        response = self.client.post(self.url, self._book_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "slot_unavailable")

    def test_past_date_returns_400(self):
        """Past date should return 400."""
        self.client.force_authenticate(user=self.patient)
        yesterday = date.today() - timedelta(days=1)
        response = self.client.post(
            self.url,
            self._book_payload(appointment_date=yesterday.isoformat()),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "past_date")

    def test_invalid_slot_returns_400(self):
        """Invalid time slot should return 400."""
        self.client.force_authenticate(user=self.patient)
        response = self.client.post(
            self.url,
            self._book_payload(appointment_time="09:15"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "invalid_slot")

    def test_response_includes_all_fields(self):
        """Successful response should include all expected fields."""
        self.client.force_authenticate(user=self.patient)
        response = self.client.post(self.url, self._book_payload(), format="json")

        expected_fields = [
            "id", "patient", "patient_name", "doctor", "doctor_name",
            "clinic", "clinic_name", "appointment_type", "appointment_type_name",
            "appointment_type_duration", "appointment_type_price",
            "appointment_date", "appointment_time", "status", "status_display",
            "reason", "created_at",
        ]
        for field in expected_fields:
            self.assertIn(field, response.data, f"Missing field: {field}")


# ═══════════════════════════════════════════════════════════════════
#  Staff Cancellation & Notification Tests
# ═══════════════════════════════════════════════════════════════════


class StaffCancellationNotificationTests(TransactionTestCase):
    """
    Tests for cancel_appointment_by_staff() and the notification pipeline.

    Uses TransactionTestCase (not TestCase) so that transaction.on_commit()
    callbacks fire immediately within the test, matching production behaviour.
    """

    def setUp(self):
        # Users
        self.main_doctor = User.objects.create_user(
            phone="0592000001", password="testpass", name="Dr. Owner", role="MAIN_DOCTOR"
        )
        self.doctor = User.objects.create_user(
            phone="0592000002", password="testpass", name="Dr. Ahmad", role="DOCTOR"
        )
        self.patient = User.objects.create_user(
            phone="0592000003", password="testpass", name="Patient Ali", role="PATIENT"
        )

        # Clinics
        self.clinic = Clinic.objects.create(
            name="Main Clinic",
            address="Addr",
            phone="0591111112",
            email="main@clinic.com",
            main_doctor=self.main_doctor,
        )
        self.other_clinic = Clinic.objects.create(
            name="Other Clinic",
            address="Addr2",
            phone="0591111113",
            email="other@clinic.com",
            main_doctor=self.main_doctor,
        )

        from clinics.models import ClinicStaff
        self.clinic_staff = ClinicStaff.objects.create(
            clinic=self.clinic,
            user=self.main_doctor,
            role="SECRETARY",
            added_by=self.main_doctor,
        )
        self.other_clinic_staff = ClinicStaff.objects.create(
            clinic=self.other_clinic,
            user=self.doctor,
            role="SECRETARY",
            added_by=self.main_doctor,
        )

        # Appointment
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            appointment_date=date.today() + timedelta(days=3),
            appointment_time=time(10, 0),
            status=Appointment.Status.CONFIRMED,
        )

    def _cancel_by_staff(self):
        """Helper: call the service under test."""
        from appointments.services.patient_appointments_service import cancel_appointment_by_staff
        return cancel_appointment_by_staff(self.appointment.id, self.clinic_staff)

    # ── Test 1: In-app notification created on successful cancellation ─────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_notification_created_on_cancellation(self, mock_email, mock_sms):
        """ClinicStaff cancels CONFIRMED appointment → in-app notification created."""
        from appointments.models import AppointmentNotification

        result = self._cancel_by_staff()

        self.assertTrue(result)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.CANCELLED)

        notifs = AppointmentNotification.objects.filter(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
        )
        self.assertEqual(notifs.count(), 1)
        notif = notifs.first()
        self.assertTrue(notif.is_delivered)
        self.assertFalse(notif.is_read)
        self.assertIn("Dr. Ahmad", notif.message)
        # FIX 2: audit field populated
        self.assertEqual(notif.cancelled_by_staff, self.clinic_staff)

    # ── Test 2: PENDING appointment also notified ──────────────────────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_pending_appointment_also_notified(self, mock_email, mock_sms):
        """ClinicStaff cancels PENDING appointment → notification created."""
        from appointments.models import AppointmentNotification

        self.appointment.status = Appointment.Status.PENDING
        self.appointment.save(update_fields=["status"])

        self._cancel_by_staff()

        self.assertEqual(
            AppointmentNotification.objects.filter(patient=self.patient).count(), 1
        )

    # ── Test 3: Email sent when patient has verified email ─────────────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_email_sent_to_verified_email(self, mock_email, mock_sms):
        """Email is sent when patient.email is set AND email_verified=True."""
        self.patient.email = "ali@example.com"
        self.patient.email_verified = True
        self.patient.save(update_fields=["email", "email_verified"])

        self._cancel_by_staff()

        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args
        # First positional arg is the recipient email
        self.assertEqual(call_kwargs[0][0], "ali@example.com")

    # ── Test 4: No email when email_verified=False ─────────────────────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_no_email_when_not_verified(self, mock_email, mock_sms):
        """No email when patient has an email but email_verified=False."""
        self.patient.email = "ali@example.com"
        self.patient.email_verified = False
        self.patient.save(update_fields=["email", "email_verified"])

        self._cancel_by_staff()

        mock_email.assert_not_called()

    # ── Test 5: No email when patient has no email at all ─────────────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_no_email_when_email_is_none(self, mock_email, mock_sms):
        """No email when patient.email is None (not set)."""
        self.patient.email = None
        self.patient.email_verified = False
        self.patient.save(update_fields=["email", "email_verified"])

        self._cancel_by_staff()

        mock_email.assert_not_called()

    # ── Test 6: Duplicate cancellation raises ValueError; no double notification

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_duplicate_cancellation_raises_error(self, mock_email, mock_sms):
        """Already-CANCELLED appointment raises ValueError; no extra notification."""
        from appointments.models import AppointmentNotification
        from appointments.services.patient_appointments_service import cancel_appointment_by_staff

        # First cancellation succeeds
        self._cancel_by_staff()
        notif_count_after_first = AppointmentNotification.objects.filter(
            patient=self.patient
        ).count()
        self.assertEqual(notif_count_after_first, 1)

        # Second cancellation must raise
        with self.assertRaises(ValueError):
            cancel_appointment_by_staff(self.appointment.id, self.clinic_staff)

        # No additional notification
        self.assertEqual(
            AppointmentNotification.objects.filter(patient=self.patient).count(),
            1,
        )

    # ── Test 7: Unauthorized staff raises ValueError; no notification ──────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_unauthorized_staff_cannot_cancel(self, mock_email, mock_sms):
        """Staff from a different clinic are rejected; no notification created."""
        from appointments.models import AppointmentNotification
        from appointments.services.patient_appointments_service import cancel_appointment_by_staff

        with self.assertRaises(ValueError):
            cancel_appointment_by_staff(self.appointment.id, self.other_clinic_staff)

        self.assertEqual(
            AppointmentNotification.objects.filter(patient=self.patient).count(), 0
        )

    # ── FIX 2: cancelled_by_staff audit field is correctly stored ─────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_cancelled_by_staff_is_stored(self, mock_email, mock_sms):
        """FIX 2: notification.cancelled_by_staff equals the acting ClinicStaff."""
        from appointments.models import AppointmentNotification

        self._cancel_by_staff()

        notif = AppointmentNotification.objects.get(
            patient=self.patient, appointment=self.appointment
        )
        self.assertEqual(notif.cancelled_by_staff, self.clinic_staff)

    # ── FIX 3: UniqueConstraint prevents DB-level duplicate ───────────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch("accounts.email_utils._send_email")
    def test_unique_constraint_prevents_db_duplicate(self, mock_email, mock_sms):
        """
        FIX 3: the UniqueConstraint on (appointment, notification_type) prevents
        a second DB row even if the service logic somehow allowed it.
        """
        from django.db import IntegrityError
        from appointments.models import AppointmentNotification

        # Create the first notification manually
        AppointmentNotification.objects.create(
            patient=self.patient,
            appointment=self.appointment,
            notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
            title="Test",
            message="Test",
        )
        # Attempting a second one with the same (appointment, type) must fail at DB level
        with self.assertRaises(IntegrityError):
            AppointmentNotification.objects.create(
                patient=self.patient,
                appointment=self.appointment,
                notification_type=AppointmentNotification.Type.APPOINTMENT_CANCELLED,
                title="Duplicate",
                message="Duplicate",
            )

    # ── FIX 4: SMS is skipped when not configured ───────────────────────

    @patch("accounts.email_utils._send_email")
    def test_sms_skipped_when_not_configured(self, mock_email):
        """
        FIX 4: when SMS_PROVIDER is blank, send_sms is never called.
        Uses override_settings to ensure deterministic behaviour regardless
        of the real local .env configuration.
        """
        from django.test import override_settings
        from appointments.services.patient_appointments_service import _is_sms_configured
        from unittest.mock import patch as _patch

        # Force unconfigured state in isolation, regardless of real .env
        with override_settings(SMS_PROVIDER="", TWEETSMS_API_KEY="", TWEETSMS_SENDER=""):
            # Confirm the gate returns False under these settings
            self.assertFalse(_is_sms_configured())

            # Assert send_sms is never reached
            with _patch("accounts.services.tweetsms.send_sms") as mock_sms:
                self._cancel_by_staff()
                mock_sms.assert_not_called()

    # ── FIX 5: email failure does NOT block in-app notification ──────────

    @patch("accounts.services.tweetsms.send_sms")
    @patch(
        "accounts.email_utils._send_email",
        side_effect=Exception("Brevo outage"),
    )
    def test_email_failure_does_not_block_in_app_notification(
        self, mock_failing_email, mock_sms
    ):
        """
        FIX 5: even when _send_email raises an unexpected exception, the
        in-app AppointmentNotification row is still created successfully.
        """
        from appointments.models import AppointmentNotification

        # Give the patient a verified email so the email path is exercised
        self.patient.email = "ali@example.com"
        self.patient.email_verified = True
        self.patient.save(update_fields=["email", "email_verified"])

        # Must not raise despite the email failure
        self._cancel_by_staff()

        # In-app notification still created
        self.assertEqual(
            AppointmentNotification.objects.filter(
                patient=self.patient,
                appointment=self.appointment,
            ).count(),
            1,
        )
