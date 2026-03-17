"""
Tests for booking restriction gates added to booking_service.py.

Covers Phase 7D:
- booking blocked for unverified doctor (IDENTITY_UNVERIFIED, IDENTITY_PENDING_REVIEW)
- booking blocked for revoked doctor
- booking blocked when doctor is not active staff at the clinic
- booking blocked when clinic subscription is inactive
- booking blocked when patient argument is not a PATIENT role user
- double-book protection still works after new checks are in place
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from appointments.models import Appointment, AppointmentType
from appointments.services import (
    book_appointment, BookingError, SlotUnavailableError,
)
from clinics.models import Clinic, ClinicStaff, ClinicSubscription
from doctors.models import DoctorAvailability, DoctorVerification

User = get_user_model()


class BookingRestrictionTestBase(TestCase):
    """
    Full booking fixture: verified doctor, active staff, active subscription.
    Individual tests override specific conditions to verify the gate.
    """

    def setUp(self):
        self.main_doctor = User.objects.create_user(
            phone="0591500001", password="pass1234",
            name="Dr. Owner", role="MAIN_DOCTOR", roles=["MAIN_DOCTOR"],
        )
        self.clinic = Clinic.objects.create(
            name="Restriction Test Clinic", address="Test St",
            phone="0591500010", email="r@test.com",
            main_doctor=self.main_doctor, is_active=True,
        )
        self.doctor = User.objects.create_user(
            phone="0591500002", password="pass1234",
            name="Dr. Test", role="DOCTOR", roles=["DOCTOR"],
        )
        self.staff = ClinicStaff.objects.create(
            clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True,
        )
        self.verification = DoctorVerification.objects.create(
            user=self.doctor, identity_status="IDENTITY_VERIFIED",
        )
        DoctorAvailability.objects.create(
            doctor=self.doctor, clinic=self.clinic,
            day_of_week=0, start_time=time(9, 0), end_time=time(17, 0),
        )
        self.patient = User.objects.create_user(
            phone="0591500003", password="pass1234",
            name="Patient Test", role="PATIENT", roles=["PATIENT"],
        )
        self.appt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="General",
            duration_minutes=30, price=Decimal("50.00"),
        )

        # Active subscription
        self.subscription = ClinicSubscription.objects.create(
            clinic=self.clinic,
            plan_type="MONTHLY",
            expires_at=timezone.now() + timedelta(days=30),
            max_doctors=5,
            status="ACTIVE",
        )

        # Next Monday as a consistent future date
        today = date.today()
        days_ahead = -today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        self.next_monday = today + timedelta(days=days_ahead)

    def _book(self, appt_time=time(9, 0)):
        return book_appointment(
            patient=self.patient,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appt_type.id,
            appointment_date=self.next_monday,
            appointment_time=appt_time,
        )


# ════════════════════════════════════════════════════════════════════
#  Happy path baseline
# ════════════════════════════════════════════════════════════════════

class BookingHappyPathTests(BookingRestrictionTestBase):

    def test_baseline_booking_succeeds(self):
        """All checks pass — booking must succeed."""
        appt = self._book()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)
        self.assertEqual(appt.doctor, self.doctor)
        self.assertEqual(appt.patient, self.patient)


# ════════════════════════════════════════════════════════════════════
#  Doctor verification gate (check 2c)
# ════════════════════════════════════════════════════════════════════

class DoctorVerificationGateTests(BookingRestrictionTestBase):

    def test_unverified_doctor_blocked(self):
        self.verification.identity_status = "IDENTITY_UNVERIFIED"
        self.verification.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_verified")

    def test_pending_review_doctor_blocked(self):
        self.verification.identity_status = "IDENTITY_PENDING_REVIEW"
        self.verification.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_verified")

    def test_rejected_doctor_blocked(self):
        self.verification.identity_status = "IDENTITY_REJECTED"
        self.verification.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_verified")

    def test_revoked_doctor_blocked(self):
        self.verification.identity_status = "IDENTITY_REVOKED"
        self.verification.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_verified")

    def test_no_verification_record_blocked(self):
        self.verification.delete()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_verified")


# ════════════════════════════════════════════════════════════════════
#  Doctor active-in-clinic gate (check 2b)
# ════════════════════════════════════════════════════════════════════

class DoctorActiveInClinicGateTests(BookingRestrictionTestBase):

    def test_revoked_staff_cannot_be_booked(self):
        """If the doctor's ClinicStaff record is inactive, booking must fail."""
        self.staff.is_active = False
        self.staff.revoked_at = timezone.now()
        self.staff.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_active")

    def test_no_staff_record_cannot_be_booked(self):
        """If doctor has no ClinicStaff record at all (and is not main_doctor), booking must fail."""
        self.staff.delete()
        # The clinic's main_doctor is self.main_doctor, not self.doctor,
        # so self.doctor has no other route in.
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "doctor_not_active")

    def test_main_doctor_without_staff_record_can_be_booked(self):
        """main_doctor is always allowed even without a separate ClinicStaff record."""
        # Book via main_doctor (who is also IDENTITY_VERIFIED — create verification)
        DoctorVerification.objects.create(
            user=self.main_doctor, identity_status="IDENTITY_VERIFIED",
        )
        DoctorAvailability.objects.create(
            doctor=self.main_doctor, clinic=self.clinic,
            day_of_week=0, start_time=time(9, 0), end_time=time(17, 0),
        )
        appt = book_appointment(
            patient=self.patient,
            doctor_id=self.main_doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appt_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 0),
        )
        self.assertEqual(appt.doctor, self.main_doctor)


# ════════════════════════════════════════════════════════════════════
#  Clinic subscription gate (check 2d)
# ════════════════════════════════════════════════════════════════════

class ClinicSubscriptionGateTests(BookingRestrictionTestBase):

    def test_expired_subscription_blocks_booking(self):
        self.subscription.status = "EXPIRED"
        self.subscription.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "clinic_subscription_inactive")

    def test_suspended_subscription_blocks_booking(self):
        self.subscription.status = "SUSPENDED"
        self.subscription.save()
        with self.assertRaises(BookingError) as ctx:
            self._book()
        self.assertEqual(ctx.exception.code, "clinic_subscription_inactive")

    def test_no_subscription_record_allows_booking(self):
        """If no ClinicSubscription exists, booking is allowed (backward compat)."""
        self.subscription.delete()
        appt = self._book()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)


# ════════════════════════════════════════════════════════════════════
#  Patient role gate (check 0)
# ════════════════════════════════════════════════════════════════════

class PatientRoleGateTests(BookingRestrictionTestBase):

    def test_booking_with_doctor_as_patient_blocked(self):
        """Using a doctor user as the patient argument must raise BookingError."""
        with self.assertRaises(BookingError) as ctx:
            book_appointment(
                patient=self.doctor,  # not a patient
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appt_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )
        self.assertEqual(ctx.exception.code, "not_a_patient")

    def test_booking_with_secretary_as_patient_blocked(self):
        secretary = User.objects.create_user(
            phone="0591500099", password="pass1234",
            name="Sec", role="SECRETARY", roles=["SECRETARY"],
        )
        with self.assertRaises(BookingError) as ctx:
            book_appointment(
                patient=secretary,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appt_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )
        self.assertEqual(ctx.exception.code, "not_a_patient")


# ════════════════════════════════════════════════════════════════════
#  Double-booking protection still works after new checks
# ════════════════════════════════════════════════════════════════════

class DoubleBookingProtectionTests(BookingRestrictionTestBase):

    def test_same_slot_raises_slot_unavailable(self):
        self._book(time(9, 0))
        patient2 = User.objects.create_user(
            phone="0591500004", password="pass1234",
            name="Patient 2", role="PATIENT", roles=["PATIENT"],
        )
        with self.assertRaises(SlotUnavailableError):
            book_appointment(
                patient=patient2,
                doctor_id=self.doctor.id,
                clinic_id=self.clinic.id,
                appointment_type_id=self.appt_type.id,
                appointment_date=self.next_monday,
                appointment_time=time(9, 0),
            )

    def test_adjacent_slot_allowed(self):
        self._book(time(9, 0))
        patient2 = User.objects.create_user(
            phone="0591500004", password="pass1234",
            name="Patient 2", role="PATIENT", roles=["PATIENT"],
        )
        appt2 = book_appointment(
            patient=patient2,
            doctor_id=self.doctor.id,
            clinic_id=self.clinic.id,
            appointment_type_id=self.appt_type.id,
            appointment_date=self.next_monday,
            appointment_time=time(9, 30),  # next 30-min slot
        )
        self.assertEqual(appt2.appointment_time, time(9, 30))
