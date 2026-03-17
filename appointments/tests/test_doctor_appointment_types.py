"""
Tests for DoctorClinicAppointmentType (DCAT) feature:
- Service functions (get, set, toggle)
- Backwards-compatibility fallback
- Booking step 3.5 validation
- Data isolation between clinics
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from appointments.models import Appointment, AppointmentType, DoctorClinicAppointmentType
from appointments.services.appointment_type_service import (
    get_appointment_types_for_doctor_in_clinic,
    get_doctor_type_assignments,
    set_doctor_clinic_appointment_types,
    toggle_doctor_clinic_appointment_type,
)
from clinics.models import Clinic, ClinicStaff

User = get_user_model()


class DCATTestBase(TestCase):
    """Shared fixtures for all DCAT tests."""

    def setUp(self):
        # Clinic owner / main doctor
        self.owner = User.objects.create_user(
            phone="0591000001", password="pass", name="Owner", role="MAIN_DOCTOR"
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic", address="Test", main_doctor=self.owner
        )

        # Staff doctor
        self.doctor = User.objects.create_user(
            phone="0591000002", password="pass", name="Dr. Staff", role="DOCTOR"
        )
        ClinicStaff.objects.create(clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True)

        # Appointment types
        self.type_a = AppointmentType.objects.create(
            clinic=self.clinic, name="Type A", duration_minutes=30, price=Decimal("100")
        )
        self.type_b = AppointmentType.objects.create(
            clinic=self.clinic, name="Type B", duration_minutes=60, price=Decimal("200")
        )
        self.type_c = AppointmentType.objects.create(
            clinic=self.clinic, name="Type C", duration_minutes=45, price=Decimal("150"),
            is_active=False  # inactive clinic type
        )

        # Second clinic for isolation tests
        self.owner2 = User.objects.create_user(
            phone="0591000003", password="pass", name="Owner2", role="MAIN_DOCTOR"
        )
        self.clinic2 = Clinic.objects.create(
            name="Other Clinic", address="Test2", main_doctor=self.owner2
        )
        self.type_x = AppointmentType.objects.create(
            clinic=self.clinic2, name="Type X", duration_minutes=30, price=Decimal("50")
        )


class TestGetTypesNoConfig(DCATTestBase):
    """Backwards-compat: no DCAT rows → all active clinic types returned."""

    def test_returns_all_active_types_when_no_config(self):
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        ids = {t.id for t in types}
        self.assertIn(self.type_a.id, ids)
        self.assertIn(self.type_b.id, ids)
        # Inactive clinic type excluded
        self.assertNotIn(self.type_c.id, ids)

    def test_returns_empty_for_wrong_clinic(self):
        """Doctor not in clinic2 → still falls back to clinic2's active types (no DCAT rows)."""
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic2.id)
        ids = {t.id for t in types}
        self.assertIn(self.type_x.id, ids)


class TestGetTypesWithConfig(DCATTestBase):
    """When DCAT rows exist, only is_active=True rows are returned."""

    def setUp(self):
        super().setUp()
        # Configure: doctor has type_a enabled, type_b disabled
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_b, is_active=False
        )

    def test_only_active_dcat_rows_returned(self):
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        ids = {t.id for t in types}
        self.assertIn(self.type_a.id, ids)
        self.assertNotIn(self.type_b.id, ids)

    def test_inactive_clinic_type_excluded_even_if_dcat_active(self):
        """DCAT row for an inactive clinic type should not appear."""
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_c, is_active=True
        )
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        ids = {t.id for t in types}
        self.assertNotIn(self.type_c.id, ids)

    def test_clinic2_not_affected_by_clinic_config(self):
        """Config for clinic does not leak into clinic2 (backwards-compat still applies)."""
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic2.id)
        ids = {t.id for t in types}
        self.assertIn(self.type_x.id, ids)


class TestGetDoctorTypeAssignments(DCATTestBase):
    """get_doctor_type_assignments returns correct is_assigned / is_active flags."""

    def test_no_config_all_unassigned(self):
        assignments = get_doctor_type_assignments(self.doctor.id, self.clinic.id)
        # Only active clinic types should appear
        names = [a["appointment_type"].name for a in assignments]
        self.assertIn("Type A", names)
        self.assertIn("Type B", names)
        self.assertNotIn("Type C", names)  # inactive type excluded
        for a in assignments:
            self.assertFalse(a["is_assigned"])
            self.assertIsNone(a["dcat_id"])

    def test_existing_dcat_reflected(self):
        dcat = DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        assignments = get_doctor_type_assignments(self.doctor.id, self.clinic.id)
        a_entry = next(a for a in assignments if a["appointment_type"].id == self.type_a.id)
        self.assertTrue(a_entry["is_assigned"])
        self.assertTrue(a_entry["is_active"])
        self.assertEqual(a_entry["dcat_id"], dcat.id)

        b_entry = next(a for a in assignments if a["appointment_type"].id == self.type_b.id)
        self.assertFalse(b_entry["is_assigned"])


class TestSetDoctorClinicAppointmentTypes(DCATTestBase):
    """set_doctor_clinic_appointment_types bulk-upserts correctly."""

    def test_enable_subset(self):
        set_doctor_clinic_appointment_types(self.doctor.id, self.clinic.id, [self.type_a.id])
        self.assertTrue(
            DoctorClinicAppointmentType.objects.get(
                doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a
            ).is_active
        )
        self.assertFalse(
            DoctorClinicAppointmentType.objects.get(
                doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_b
            ).is_active
        )

    def test_enable_all(self):
        count = set_doctor_clinic_appointment_types(
            self.doctor.id, self.clinic.id, [self.type_a.id, self.type_b.id]
        )
        self.assertEqual(count, 2)

    def test_enable_none_disables_all(self):
        set_doctor_clinic_appointment_types(self.doctor.id, self.clinic.id, [])
        self.assertFalse(
            DoctorClinicAppointmentType.objects.get(
                doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a
            ).is_active
        )

    def test_invalid_type_id_raises(self):
        """Passing a type_id from another clinic should raise ValidationError."""
        with self.assertRaises(ValidationError):
            set_doctor_clinic_appointment_types(
                self.doctor.id, self.clinic.id, [self.type_x.id]  # belongs to clinic2
            )

    def test_idempotent_on_repeat_call(self):
        set_doctor_clinic_appointment_types(self.doctor.id, self.clinic.id, [self.type_a.id])
        set_doctor_clinic_appointment_types(self.doctor.id, self.clinic.id, [self.type_a.id])
        count = DoctorClinicAppointmentType.objects.filter(
            doctor=self.doctor, clinic=self.clinic, is_active=True
        ).count()
        self.assertEqual(count, 1)


class TestToggleDoctorClinicAppointmentType(DCATTestBase):
    """toggle_doctor_clinic_appointment_type creates/flips correctly."""

    def test_creates_and_returns_true_on_first_call(self):
        result = toggle_doctor_clinic_appointment_type(self.doctor.id, self.clinic.id, self.type_a.id)
        self.assertTrue(result)
        self.assertTrue(
            DoctorClinicAppointmentType.objects.get(
                doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a
            ).is_active
        )

    def test_toggle_existing_active_to_inactive(self):
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        result = toggle_doctor_clinic_appointment_type(self.doctor.id, self.clinic.id, self.type_a.id)
        self.assertFalse(result)

    def test_toggle_existing_inactive_to_active(self):
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=False
        )
        result = toggle_doctor_clinic_appointment_type(self.doctor.id, self.clinic.id, self.type_a.id)
        self.assertTrue(result)

    def test_wrong_clinic_type_raises(self):
        with self.assertRaises(ValidationError):
            toggle_doctor_clinic_appointment_type(self.doctor.id, self.clinic.id, self.type_x.id)


class TestBookingStep35Logic(DCATTestBase):
    """
    Verify that step 3.5 logic (type enabled for doctor) is correctly gated.

    We test this through get_appointment_types_for_doctor_in_clinic directly,
    which is the exact function called by booking_service step 3.5.
    Full booking integration tests are omitted here because they require
    DoctorVerification + availability slots setup (covered in test_main.py).
    """

    def test_step35_passes_when_no_config(self):
        """No DCAT rows → fall back to all active types → booking gate would pass."""
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        enabled_ids = {t.id for t in types}
        # type_a is active and there's no config → should be enabled
        self.assertIn(self.type_a.id, enabled_ids)
        self.assertIn(self.type_b.id, enabled_ids)

    def test_step35_passes_when_type_explicitly_enabled(self):
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_b, is_active=False
        )
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        enabled_ids = {t.id for t in types}
        self.assertIn(self.type_a.id, enabled_ids)
        # type_b would fail step 3.5
        self.assertNotIn(self.type_b.id, enabled_ids)

    def test_step35_fails_when_type_disabled(self):
        """Once DCAT rows exist, a disabled type would raise in booking step 3.5."""
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_b, is_active=False
        )
        types = get_appointment_types_for_doctor_in_clinic(self.doctor.id, self.clinic.id)
        enabled_ids = {t.id for t in types}
        # Simulate the booking service check
        self.assertFalse(any(t_id == self.type_b.id for t_id in enabled_ids),
                         "type_b should not be in enabled set → booking would raise BookingError")


class TestDCATUniqueConstraint(DCATTestBase):
    """Unique constraint prevents duplicate (doctor, clinic, appointment_type) rows."""

    def test_duplicate_raises(self):
        from django.db import IntegrityError
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        with self.assertRaises(IntegrityError):
            DoctorClinicAppointmentType.objects.create(
                doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=False
            )

    def test_same_doctor_different_clinic_allowed(self):
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic, appointment_type=self.type_a, is_active=True
        )
        # type_x belongs to clinic2 — different clinic, no conflict
        DoctorClinicAppointmentType.objects.create(
            doctor=self.doctor, clinic=self.clinic2, appointment_type=self.type_x, is_active=True
        )
        self.assertEqual(
            DoctorClinicAppointmentType.objects.filter(doctor=self.doctor).count(), 2
        )
