from datetime import time, date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from clinics.models import Clinic
from appointments.models import Appointment, AppointmentType
from .models import DoctorAvailability
from .services import generate_slots_for_date

User = get_user_model()


class DoctorAvailabilityModelTestMixin:
    """Shared setup for availability tests."""

    def setUp(self):
        # Create users
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
            name="Patient Test",
            role="PATIENT",
        )

        # Create clinics
        self.clinic_a = Clinic.objects.create(
            name="Clinic A",
            address="Address A",
            phone="0591111111",
            email="a@clinic.com",
            main_doctor=self.main_doctor,
        )
        self.clinic_b = Clinic.objects.create(
            name="Clinic B",
            address="Address B",
            phone="0592222222",
            email="b@clinic.com",
            main_doctor=self.main_doctor,
        )


class DoctorAvailabilityModelTests(DoctorAvailabilityModelTestMixin, TestCase):
    """Tests for DoctorAvailability model validation."""

    def test_create_availability_success(self):
        """Basic availability creation should work."""
        avail = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        self.assertEqual(avail.doctor, self.doctor)
        self.assertTrue(avail.is_active)

    def test_start_after_end_raises_error(self):
        """start_time >= end_time should raise ValidationError."""
        with self.assertRaises(ValidationError):
            DoctorAvailability.objects.create(
                doctor=self.doctor,
                clinic=self.clinic_a,
                day_of_week=0,
                start_time=time(14, 0),
                end_time=time(9, 0),
            )

    def test_equal_start_end_raises_error(self):
        """start_time == end_time should raise ValidationError."""
        with self.assertRaises(ValidationError):
            DoctorAvailability.objects.create(
                doctor=self.doctor,
                clinic=self.clinic_a,
                day_of_week=0,
                start_time=time(9, 0),
                end_time=time(9, 0),
            )

    def test_same_clinic_overlap_raises_error(self):
        """Overlapping slots at the same clinic should fail."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        with self.assertRaises(ValidationError):
            DoctorAvailability.objects.create(
                doctor=self.doctor,
                clinic=self.clinic_a,
                day_of_week=0,
                start_time=time(11, 0),
                end_time=time(16, 0),
            )

    def test_cross_clinic_overlap_raises_error(self):
        """Overlapping slots across different clinics should fail (R-04)."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        with self.assertRaises(ValidationError):
            DoctorAvailability.objects.create(
                doctor=self.doctor,
                clinic=self.clinic_b,
                day_of_week=0,
                start_time=time(10, 0),
                end_time=time(18, 0),
            )

    def test_non_overlapping_same_day_same_clinic_ok(self):
        """Two non-overlapping blocks on the same day/clinic should work."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        avail2 = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(14, 0),
            end_time=time(17, 0),
        )
        self.assertIsNotNone(avail2.pk)

    def test_non_overlapping_cross_clinic_ok(self):
        """Non-overlapping slots across clinics should work."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(13, 0),
        )
        avail2 = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_b,
            day_of_week=0,
            start_time=time(14, 0),
            end_time=time(18, 0),
        )
        self.assertIsNotNone(avail2.pk)

    def test_different_day_no_conflict(self):
        """Same time range on different days should work."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        avail2 = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_b,
            day_of_week=2,  # Wednesday
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        self.assertIsNotNone(avail2.pk)

    def test_inactive_slot_not_counted_for_overlap(self):
        """Inactive slots should not block new entries."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(14, 0),
            is_active=False,
        )
        avail2 = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_b,
            day_of_week=0,
            start_time=time(10, 0),
            end_time=time(16, 0),
        )
        self.assertIsNotNone(avail2.pk)


class SlotGenerationServiceTests(DoctorAvailabilityModelTestMixin, TestCase):
    """Tests for the slot generation engine."""

    def setUp(self):
        super().setUp()
        # Monday availability: 9:00-12:00
        self.availability = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.appointment_type = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            name="General Checkup",
            duration_minutes=30,
            price=Decimal("50.00"),
        )
        # Find next Monday
        today = date.today()
        days_until_monday = (0 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        self.next_monday = today + timedelta(days=days_until_monday)

    def test_generate_slots_basic(self):
        """Should generate 6 x 30min slots from 9:00-12:00."""
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=self.next_monday,
            duration_minutes=30,
        )
        self.assertEqual(len(slots), 6)
        self.assertEqual(slots[0]["time"], time(9, 0))
        self.assertEqual(slots[0]["end_time"], time(9, 30))
        self.assertEqual(slots[-1]["time"], time(11, 30))
        self.assertTrue(all(s["is_available"] for s in slots))

    def test_generate_slots_with_booking(self):
        """Booked slot should appear as is_available=False."""
        Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic_a,
            doctor=self.doctor,
            appointment_type=self.appointment_type,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
            status="CONFIRMED",
        )
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=self.next_monday,
            duration_minutes=30,
        )
        self.assertEqual(len(slots), 6)
        # 10:00 slot should be unavailable
        slot_10 = next(s for s in slots if s["time"] == time(10, 0))
        self.assertFalse(slot_10["is_available"])
        # 9:00 should still be available
        slot_9 = next(s for s in slots if s["time"] == time(9, 0))
        self.assertTrue(slot_9["is_available"])

    def test_cross_clinic_booking_blocks_slot(self):
        """Appointment at Clinic B should block slot at Clinic A (R-03)."""
        # Doctor also available at Clinic B on Monday
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_b,
            day_of_week=0,
            start_time=time(14, 0),
            end_time=time(17, 0),
        )
        # Appointment at Clinic B
        Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic_b,
            doctor=self.doctor,
            appointment_type=self.appointment_type,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
            status="CONFIRMED",
        )
        # Check Clinic A slots — 10:00 should be blocked
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=self.next_monday,
            duration_minutes=30,
        )
        slot_10 = next(s for s in slots if s["time"] == time(10, 0))
        self.assertFalse(slot_10["is_available"])

    def test_no_availability_returns_empty(self):
        """Day with no availability should return empty list."""
        # Tuesday — no availability defined
        tuesday = self.next_monday + timedelta(days=1)
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=tuesday,
            duration_minutes=30,
        )
        self.assertEqual(slots, [])

    def test_cancelled_appointment_does_not_block(self):
        """Cancelled appointments should not block slots."""
        Appointment.objects.create(
            patient=self.patient,
            clinic=self.clinic_a,
            doctor=self.doctor,
            appointment_type=self.appointment_type,
            appointment_date=self.next_monday,
            appointment_time=time(10, 0),
            status="CANCELLED",
        )
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=self.next_monday,
            duration_minutes=30,
        )
        slot_10 = next(s for s in slots if s["time"] == time(10, 0))
        self.assertTrue(slot_10["is_available"])

    def test_multiple_blocks_same_day(self):
        """Two availability blocks should generate slots from both."""
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(14, 0),
            end_time=time(16, 0),
        )
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=self.next_monday,
            duration_minutes=30,
        )
        # 9-12: 6 slots + 14-16: 4 slots = 10 total
        self.assertEqual(len(slots), 10)

    def test_duration_larger_than_block(self):
        """60min slots in a 90min block should give 1 slot."""
        # Create a 90min block on Tuesday
        tuesday = self.next_monday + timedelta(days=1)
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=1,  # Tuesday
            start_time=time(9, 0),
            end_time=time(10, 30),
        )
        slots = generate_slots_for_date(
            doctor_id=self.doctor.id,
            clinic_id=self.clinic_a.id,
            target_date=tuesday,
            duration_minutes=60,
        )
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["time"], time(9, 0))
        self.assertEqual(slots[0]["end_time"], time(10, 0))


class DoctorAvailabilityAPITests(DoctorAvailabilityModelTestMixin, TestCase):
    """Tests for the API endpoints."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.patient)

        self.availability = DoctorAvailability.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        self.appointment_type = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            name="Checkup",
            duration_minutes=30,
            price=Decimal("50.00"),
        )

    # --- Availability Schedule API ---

    def test_get_availability_success(self):
        url = reverse(
            "doctors:api_doctor_availability", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(url, {"clinic_id": self.clinic_a.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_get_availability_missing_clinic_id(self):
        url = reverse(
            "doctors:api_doctor_availability", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_availability_empty(self):
        url = reverse(
            "doctors:api_doctor_availability", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(url, {"clinic_id": self.clinic_b.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_get_availability_unauthenticated(self):
        self.client.force_authenticate(user=None)
        url = reverse(
            "doctors:api_doctor_availability", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(url, {"clinic_id": self.clinic_a.id})
        # DRF with session auth returns 403; JWT-only would return 401
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    # --- Available Slots API ---

    def test_get_slots_success(self):
        # Find next Monday
        today = date.today()
        days_until_monday = (0 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(
            url,
            {
                "clinic_id": self.clinic_a.id,
                "date": next_monday.isoformat(),
                "appointment_type_id": self.appointment_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data["results"]), 0)

    def test_get_slots_missing_params(self):
        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_slots_past_date(self):
        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(
            url,
            {
                "clinic_id": self.clinic_a.id,
                "date": "2020-01-01",
                "appointment_type_id": self.appointment_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_slots_invalid_date_format(self):
        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(
            url,
            {
                "clinic_id": self.clinic_a.id,
                "date": "not-a-date",
                "appointment_type_id": self.appointment_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_slots_invalid_appointment_type(self):
        today = date.today()
        days_until_monday = (0 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(
            url,
            {
                "clinic_id": self.clinic_a.id,
                "date": next_monday.isoformat(),
                "appointment_type_id": 99999,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_slots_no_availability_on_date(self):
        # Tuesday — no availability
        today = date.today()
        days_until_tuesday = (1 - today.weekday()) % 7
        if days_until_tuesday == 0:
            days_until_tuesday = 7
        next_tuesday = today + timedelta(days=days_until_tuesday)

        url = reverse(
            "doctors:api_doctor_available_slots", kwargs={"doctor_id": self.doctor.id}
        )
        response = self.client.get(
            url,
            {
                "clinic_id": self.clinic_a.id,
                "date": next_tuesday.isoformat(),
                "appointment_type_id": self.appointment_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    # --- Appointment Types API ---

    def test_get_appointment_types_success(self):
        url = reverse(
            "doctors:api_doctor_appointment_types",
            kwargs={"doctor_id": self.doctor.id},
        )
        response = self.client.get(url, {"clinic_id": self.clinic_a.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "Checkup")

    def test_get_appointment_types_empty(self):
        url = reverse(
            "doctors:api_doctor_appointment_types",
            kwargs={"doctor_id": self.doctor.id},
        )
        response = self.client.get(url, {"clinic_id": self.clinic_b.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_get_appointment_types_missing_clinic_id(self):
        url = reverse(
            "doctors:api_doctor_appointment_types",
            kwargs={"doctor_id": self.doctor.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_type_not_returned(self):
        self.appointment_type.is_active = False
        self.appointment_type.save()

        url = reverse(
            "doctors:api_doctor_appointment_types",
            kwargs={"doctor_id": self.doctor.id},
        )
        response = self.client.get(url, {"clinic_id": self.clinic_a.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])


class AppointmentTypeModelTests(DoctorAvailabilityModelTestMixin, TestCase):
    """Tests for AppointmentType model."""

    def test_create_appointment_type(self):
        apt = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            name="Follow-up",
            duration_minutes=15,
            price=Decimal("30.00"),
            description="Quick follow-up visit",
        )
        self.assertEqual(apt.name, "Follow-up")
        self.assertEqual(apt.duration_minutes, 15)
        self.assertTrue(apt.is_active)

    def test_unique_name_per_doctor_clinic(self):
        """Same name for same doctor+clinic should fail."""
        AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            name="Checkup",
            duration_minutes=30,
            price=Decimal("50.00"),
        )
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            AppointmentType.objects.create(
                doctor=self.doctor,
                clinic=self.clinic_a,
                name="Checkup",
                duration_minutes=60,
                price=Decimal("100.00"),
            )

    def test_same_name_different_clinic_ok(self):
        """Same name at different clinics should work."""
        AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_a,
            name="Checkup",
            duration_minutes=30,
            price=Decimal("50.00"),
        )
        apt2 = AppointmentType.objects.create(
            doctor=self.doctor,
            clinic=self.clinic_b,
            name="Checkup",
            duration_minutes=30,
            price=Decimal("60.00"),
        )
        self.assertIsNotNone(apt2.pk)