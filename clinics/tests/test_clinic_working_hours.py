import datetime
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from clinics.models import Clinic, ClinicWorkingHours
from clinics.services import (
    create_working_hours,
    update_working_hours,
    delete_working_hours,
    get_clinic_working_hours,
    validate_doctor_availability_within_clinic_hours
)
from doctors.models import DoctorAvailability

User = get_user_model()

class ClinicWorkingHoursModelAndServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201234567890",
            national_id="123456789",
            password="password123",
            name="Test User",
            role="MAIN_DOCTOR"
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            address="Test Address",
            main_doctor=self.user,
            status="ACTIVE"
        )
        
    def test_create_working_hours_success(self):
        wh = create_working_hours(
            clinic=self.clinic,
            weekday=0,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(17, 0)
        )
        self.assertEqual(ClinicWorkingHours.objects.count(), 1)
        self.assertEqual(wh.start_time.hour, 9)
        self.assertFalse(wh.is_closed)
        
        # Test get_clinic_working_hours
        hours = get_clinic_working_hours(self.clinic)
        self.assertEqual(len(hours), 1)
        self.assertEqual(hours[0].id, wh.id)

    def test_validation_start_before_end(self):
        with self.assertRaisesMessage(ValidationError, "End time must be after start time"):
            create_working_hours(
                clinic=self.clinic,
                weekday=0,
                start_time=datetime.time(17, 0),
                end_time=datetime.time(9, 0)
            )

    def test_prevent_overlap(self):
        create_working_hours(
            clinic=self.clinic,
            weekday=0,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(12, 0)
        )
        
        with self.assertRaisesMessage(ValidationError, "overlaps with an existing working hour range"):
            create_working_hours(
                clinic=self.clinic,
                weekday=0,
                start_time=datetime.time(10, 0),
                end_time=datetime.time(13, 0)
            )

        # Non-overlapping should succeed
        create_working_hours(
            clinic=self.clinic,
            weekday=0,
            start_time=datetime.time(13, 0),
            end_time=datetime.time(17, 0)
        )
        self.assertEqual(ClinicWorkingHours.objects.count(), 2)

    def test_update_working_hours(self):
        wh = create_working_hours(
            clinic=self.clinic,
            weekday=0,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(12, 0)
        )
        
        # Valid update
        update_working_hours(wh, datetime.time(8, 0), datetime.time(11, 0), False)
        wh.refresh_from_db()
        self.assertEqual(wh.start_time.hour, 8)

    def test_delete_working_hours(self):
        wh = create_working_hours(
            clinic=self.clinic,
            weekday=0,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(12, 0)
        )
        self.assertEqual(ClinicWorkingHours.objects.count(), 1)
        delete_working_hours(wh)
        self.assertEqual(ClinicWorkingHours.objects.count(), 0)

    def test_is_closed_logic_option_a(self):
        # Create a closed day
        wh = create_working_hours(
            clinic=self.clinic,
            weekday=1,
            start_time=None,
            end_time=None,
            is_closed=True
        )
        self.assertTrue(wh.is_closed)
        
        # Adding a time range when it's closed should fail
        with self.assertRaisesMessage(ValidationError, "Cannot add working hours to a day that is marked as closed"):
            create_working_hours(
                clinic=self.clinic,
                weekday=1,
                start_time=datetime.time(9, 0),
                end_time=datetime.time(17, 0),
                is_closed=False
            )
            
    def test_is_closed_with_times_provided(self):
        with self.assertRaisesMessage(ValidationError, "If the clinic is closed on this day, start time and end time must be empty."):
            create_working_hours(
                clinic=self.clinic,
                weekday=2,
                start_time=datetime.time(9, 0),
                end_time=datetime.time(17, 0),
                is_closed=True
            )

    def test_doctor_availability_validation_no_hours(self):
        # Should pass (optional enforcement when no hours defined)
        validate_doctor_availability_within_clinic_hours(
            self.clinic, 3, datetime.time(9, 0), datetime.time(17, 0)
        )

    def test_doctor_availability_validation_closed(self):
        create_working_hours(
            clinic=self.clinic,
            weekday=4,
            start_time=None,
            end_time=None,
            is_closed=True
        )
        with self.assertRaisesMessage(ValidationError, "closed on this day"):
            validate_doctor_availability_within_clinic_hours(
                self.clinic, 4, datetime.time(9, 0), datetime.time(17, 0)
            )

    def test_doctor_availability_validation_within_range(self):
        # Two ranges for Monday
        create_working_hours(self.clinic, 0, datetime.time(9, 0), datetime.time(12, 0))
        create_working_hours(self.clinic, 0, datetime.time(16, 0), datetime.time(20, 0))
        
        # Valid: exactly matches range
        validate_doctor_availability_within_clinic_hours(
            self.clinic, 0, datetime.time(9, 0), datetime.time(12, 0)
        )
        
        # Valid: completely within a range
        validate_doctor_availability_within_clinic_hours(
            self.clinic, 0, datetime.time(17, 0), datetime.time(19, 0)
        )
        
        # Invalid: spans the break
        with self.assertRaisesMessage(ValidationError, "falls outside the clinic's operating hours"):
            validate_doctor_availability_within_clinic_hours(
                self.clinic, 0, datetime.time(10, 0), datetime.time(17, 0)
            )

class DoctorAvailabilityIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201234567890",
            national_id="123456789",
            password="password123",
            name="Test Doctor",
            role="MAIN_DOCTOR"
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            address="Test Address",
            main_doctor=self.user,
            status="ACTIVE"
        )
        
    def test_availability_model_clean(self):
        create_working_hours(self.clinic, 0, datetime.time(9, 0), datetime.time(17, 0))
        
        # Creating valid availability
        avail = DoctorAvailability(
            doctor=self.user,
            clinic=self.clinic,
            day_of_week=0,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(14, 0)
        )
        avail.full_clean()  # Should not raise exception
        avail.save()
        self.assertEqual(DoctorAvailability.objects.count(), 1)
        
        # Creating invalid availability
        avail2 = DoctorAvailability(
            doctor=self.user,
            clinic=self.clinic,
            day_of_week=0,
            start_time=datetime.time(16, 0),
            end_time=datetime.time(18, 0) # Clinic closes at 17:00
        )
        with self.assertRaisesMessage(ValidationError, "falls outside"):
            avail2.full_clean()
