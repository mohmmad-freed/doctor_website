from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from decimal import Decimal

from appointments.models import AppointmentType
from clinics.models import Clinic
from appointments.services.appointment_type_service import (
    get_appointment_types_for_clinic,
    create_appointment_type,
    update_appointment_type,
    toggle_appointment_type_status,
)

User = get_user_model()


class AppointmentTypeModelTests(TestCase):
    """Tests for the AppointmentType model and its constraints."""
    
    def setUp(self):
        self.main_doctor = User.objects.create_user(
            phone="0591000001", password="testpass", name="Dr. Owner", role="MAIN_DOCTOR"
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic", phone="0591111111", email="test@clinic.com", main_doctor=self.main_doctor
        )
        self.other_main_doctor = User.objects.create_user(
            phone="0591000002", password="testpass", name="Dr. Other", role="MAIN_DOCTOR"
        )
        self.other_clinic = Clinic.objects.create(
            name="Other Clinic", phone="0592222222", email="other@clinic.com", main_doctor=self.other_main_doctor
        )

    def test_appointment_type_creation(self):
        """Test successful creation of an AppointmentType."""
        appt_type = AppointmentType.objects.create(
            clinic=self.clinic,
            name="General Checkup",
            duration_minutes=30,
            price=Decimal("150.00")
        )
        self.assertEqual(appt_type.name, "General Checkup")
        self.assertEqual(appt_type.clinic, self.clinic)
        self.assertTrue(appt_type.is_active)
        self.assertEqual(str(appt_type), "General Checkup (30min, ₪150.00)")

    def test_unique_name_per_clinic(self):
        """Test that names must be unique within a single clinic."""
        AppointmentType.objects.create(
            clinic=self.clinic, name="General Checkup", duration_minutes=30, price=Decimal("150.00")
        )
        
        with self.assertRaises(IntegrityError):
            AppointmentType.objects.create(
                clinic=self.clinic, name="General Checkup", duration_minutes=45, price=Decimal("200.00")
            )

    def test_same_name_different_clinics_allowed(self):
        """Test that different clinics can have appointment types with the same name."""
        AppointmentType.objects.create(
            clinic=self.clinic, name="General Checkup", duration_minutes=30, price=Decimal("150.00")
        )
        
        appt_type_2 = AppointmentType.objects.create(
            clinic=self.other_clinic, name="General Checkup", duration_minutes=45, price=Decimal("200.00")
        )
        self.assertIsNotNone(appt_type_2.id)


class AppointmentTypeServiceTests(TestCase):
    """Tests for the AppointmentType service functions."""
    
    def setUp(self):
        self.main_doctor = User.objects.create_user(
            phone="0591000001", password="testpass", name="Dr. Owner", role="MAIN_DOCTOR"
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic", phone="0591111111", email="test@clinic.com", main_doctor=self.main_doctor
        )
        
        # Create some initial types
        self.type1 = AppointmentType.objects.create(
            clinic=self.clinic, name="Type 1", duration_minutes=15, price=Decimal("50.00"), is_active=True
        )
        self.type2 = AppointmentType.objects.create(
            clinic=self.clinic, name="Type 2", duration_minutes=30, price=Decimal("100.00"), is_active=False
        )

    def test_get_appointment_types_for_clinic(self):
        """Test retrieving all appointment types for a clinic."""
        types = get_appointment_types_for_clinic(self.clinic.id)
        self.assertEqual(types.count(), 2)
        # Should be ordered by name
        self.assertEqual(types[0], self.type1)
        self.assertEqual(types[1], self.type2)

    def test_create_appointment_type_success(self):
        """Test creating a valid appointment type via service."""
        data = {
            'name': 'New Type',
            'name_ar': 'نوع جديد',
            'duration_minutes': '45',
            'price': '150.00',
            'is_active': 'on'
        }
        new_type = create_appointment_type(self.clinic, data)
        self.assertIsNotNone(new_type.id)
        self.assertEqual(new_type.name, 'New Type')
        self.assertEqual(new_type.name_ar, 'نوع جديد')
        self.assertEqual(new_type.duration_minutes, 45)
        self.assertTrue(new_type.is_active)

    def test_create_appointment_type_invalid_duration(self):
        """Test creating with invalid duration."""
        data = {
            'name': 'New Type',
            'duration_minutes': '-10',
            'price': '150.00'
        }
        with self.assertRaisesMessage(ValidationError, "يجب أن تكون المدة بالدقائق رقماً صحيحاً موجباً."):
            create_appointment_type(self.clinic, data)

    def test_create_appointment_type_empty_name(self):
        """Test creating with empty name."""
        data = {
            'name': '   ',
            'duration_minutes': '30',
            'price': '150.00'
        }
        with self.assertRaisesMessage(ValidationError, "اسم نوع الموعد مطلوب."):
            create_appointment_type(self.clinic, data)

    def test_create_appointment_type_duplicate_name(self):
        """Test creating with a duplicate name for the same clinic."""
        data = {
            'name': 'Type 1',  # Already exists
            'duration_minutes': '30',
            'price': '150.00'
        }
        with self.assertRaisesMessage(ValidationError, "يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة."):
            create_appointment_type(self.clinic, data)

    def test_update_appointment_type_success(self):
        """Test updating an appointment type successfully."""
        data = {
            'name': 'Updated Type 1',
            'duration_minutes': '20',
        }
        updated = update_appointment_type(self.clinic, self.type1.id, data)
        self.assertEqual(updated.name, 'Updated Type 1')
        self.assertEqual(updated.duration_minutes, 20)
        # Other fields should remain unchanged
        self.assertEqual(updated.price, Decimal("50.00"))

    def test_update_appointment_type_duplicate_name(self):
        """Test updating to a name that already exists."""
        data = {
            'name': 'Type 2',  # Exists and is not self.type1
            'duration_minutes': '20',
        }
        with self.assertRaisesMessage(ValidationError, "يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة."):
            update_appointment_type(self.clinic, self.type1.id, data)

    def test_update_appointment_type_same_name(self):
        """Test updating but keeping the same name is allowed."""
        data = {
            'name': 'Type 1',  # Same as current
            'duration_minutes': '20',
        }
        updated = update_appointment_type(self.clinic, self.type1.id, data)
        self.assertEqual(updated.name, 'Type 1')

    def test_toggle_appointment_type_status(self):
        """Test toggling the active status."""
        self.assertTrue(self.type1.is_active)
        
        toggled = toggle_appointment_type_status(self.clinic, self.type1.id)
        self.assertFalse(toggled.is_active)
        
        toggled_again = toggle_appointment_type_status(self.clinic, self.type1.id)
        self.assertTrue(toggled_again.is_active)
