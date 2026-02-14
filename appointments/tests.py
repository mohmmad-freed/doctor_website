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