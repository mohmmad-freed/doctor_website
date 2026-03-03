from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta

from accounts.models import CustomUser
from clinics.models import Clinic, ClinicStaff, ClinicSubscription, ClinicInvitation
from doctors.models import DoctorProfile, DoctorSpecialty
from doctors.models import Specialty
from clinics.services import create_invitation, accept_invitation, cancel_invitation, reject_invitation

class ClinicInvitationServiceTests(TestCase):
    def setUp(self):
        # Create Owner
        self.owner = CustomUser.objects.create(
            phone="970591234567",
            name="Owner Doctor",
            national_id="123456789",
            roles=["MAIN_DOCTOR"]
        )
        self.owner.set_password("pass123")
        self.owner.save()

        # Create Clinic
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            main_doctor=self.owner,
            status="ACTIVE"
        )
        ClinicStaff.objects.create(
            clinic=self.clinic,
            user=self.owner,
            role="MAIN_DOCTOR"
        )

        # Create Subscription (Max 2 doctors: Owner + 1 invitee)
        self.subscription = ClinicSubscription.objects.create(
            clinic=self.clinic,
            plan_type="MONTHLY",
            expires_at=timezone.now() + timedelta(days=30),
            max_doctors=2,
            status="ACTIVE"
        )

        # Create Specialties
        self.spec1 = Specialty.objects.create(name="Cardiology", name_ar="أمراض القلب")
        self.spec2 = Specialty.objects.create(name="Pediatrics", name_ar="طب الأطفال")

    @patch('clinics.services.send_sms')
    def test_create_invitation_new_doctor_sends_sms(self, mock_send_sms):
        data = {
            "doctor_name": "New Doc",
            "doctor_phone": "0590000000",
            "doctor_email": "new@example.com",
            "specialties": [self.spec1.id]
        }
        
        invitation = create_invitation(self.clinic, self.owner, data)
        
        self.assertEqual(invitation.doctor_phone, "0590000000")
        self.assertEqual(invitation.status, "PENDING")
        self.assertEqual(invitation.specialties.count(), 1)
        mock_send_sms.assert_called_once()
        
    @patch('clinics.services.send_sms')
    def test_create_invitation_existing_doctor_no_sms(self, mock_send_sms):
        # Existing user
        existing_user = CustomUser.objects.create(
            phone="0591111111",
            name="Existing Doc",
            national_id="987654321",
            roles=["DOCTOR"]
        )
        
        # Add to staff already so we trigger that block maybe? No, the requirement is "If user does NOT exist: Use accounts.services.tweetsms.send_sms... (If exists, no SMS)".
        # Our check is user_exists = CustomUser.objects.filter(phone=normalized_phone).first()

        data = {
            "doctor_name": "Existing Doc",
            "doctor_phone": "0591111111", # Will be normalized to 0591111111
            "doctor_email": "doc@test.com",
            "specialties": []
        }
        
        invitation = create_invitation(self.clinic, self.owner, data)
        self.assertEqual(invitation.status, "PENDING")
        mock_send_sms.assert_not_called()

    def test_create_invitation_exceeds_subscription_limit(self):
        # We have max=2. Owner is 1. We invite one (staff not created yet though).
        # Let's add another doctor to staff so we hit the limit (Owner + Doc = 2).
        doc2 = CustomUser.objects.create(phone="0592222222", name="Doc2")
        ClinicStaff.objects.create(clinic=self.clinic, user=doc2, role="DOCTOR")
        
        data = {
            "doctor_name": "Too Many",
            "doctor_phone": "0593333333",
            "doctor_email": "too@many.com"
        }
        
        with self.assertRaises(ValidationError) as context:
            create_invitation(self.clinic, self.owner, data)
            
        self.assertIn("لقد وصلت للحد الأقصى لعدد الأطباء", str(context.exception))

    def test_accept_invitation_creates_staff_and_profile(self):
        # Create an invite
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_phone="0594444444",
            expires_at=timezone.now() + timedelta(days=2)
        )
        invitation.specialties.add(self.spec2)
        
        # User who will accept it
        user = CustomUser.objects.create(phone="0594444444", name="Accepting User")
        
        accept_invitation(invitation, user)
        
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "ACCEPTED")
        
        # Check Staff
        staff = ClinicStaff.objects.get(clinic=self.clinic, user=user)
        self.assertEqual(staff.role, "DOCTOR")
        
        # Check Profile & Specialties
        profile = DoctorProfile.objects.get(user=user)
        ds = DoctorSpecialty.objects.get(doctor_profile=profile)
        self.assertEqual(ds.specialty, self.spec2)
        self.assertTrue(ds.is_primary)
        
        # Check Roles
        user.refresh_from_db()
        self.assertIn("DOCTOR", user.roles)

    def test_accept_expired_invitation_fails(self):
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_phone="970594444444",
            expires_at=timezone.now() - timedelta(days=1), # Expired!
            status="PENDING"
        )
        user = CustomUser.objects.create(phone="0594444444", name="User")
        
        with self.assertRaises(ValidationError) as ctx:
            accept_invitation(invitation, user)
            
        self.assertIn("صلاحية", str(ctx.exception))
        # Due to @transaction.atomic rollback, status on DB will remain PENDING, not EXPIRED.
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "PENDING")

    def test_reject_invitation(self):
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_phone="0595555555",
            expires_at=timezone.now() + timedelta(days=2),
            status="PENDING"
        )
        user = CustomUser.objects.create(phone="0595555555")
        
        reject_invitation(invitation, user)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "REJECTED")

    def test_cancel_invitation(self):
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_phone="0596666666",
            expires_at=timezone.now() + timedelta(days=2),
            status="PENDING"
        )
        
        cancel_invitation(invitation, self.owner)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "CANCELLED")

    def test_wrong_phone_fails_to_accept(self):
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_phone="0594444444",
            expires_at=timezone.now() + timedelta(days=2)
        )
        
        # User with different phone. Using standard input for user so it passes validation.
        wrong_user = CustomUser.objects.create(phone="0599999999")
        
        with self.assertRaises(ValidationError) as ctx:
            accept_invitation(invitation, wrong_user)
            
        self.assertIn("رقم الهاتف غير متطابق", str(ctx.exception))

    @patch('clinics.services.send_sms')
    def test_create_secretary_invitation_skips_doctor_limit(self, mock_send_sms):
        # We have max=2. Let's add two doctors so we are at the limit.
        doc2 = CustomUser.objects.create(phone="0592222222", name="Doc2")
        ClinicStaff.objects.create(clinic=self.clinic, user=doc2, role="DOCTOR")
        
        # Max doctors limit reached, but creating a secretary should succeed
        data = {
            "secretary_name": "New Secretary",
            "secretary_phone": "0597777777",
            "secretary_email": "sec@example.com"
        }
        
        invitation = create_invitation(self.clinic, self.owner, data, role="SECRETARY")
        
        self.assertEqual(invitation.role, "SECRETARY")
        self.assertEqual(invitation.doctor_phone, "0597777777")
        self.assertEqual(invitation.status, "PENDING")
        mock_send_sms.assert_called_once()
        self.assertIn("سكرتير/ة", mock_send_sms.call_args[0][1])

    @patch('clinics.services.send_sms')
    def test_create_secretary_invitation_existing_user_no_sms(self, mock_send_sms):
        existing_user = CustomUser.objects.create(
            phone="0598888888",
            name="Existing Sec",
            roles=["PATIENT"]
        )

        data = {
            "secretary_name": "Existing Sec",
            "secretary_phone": "0598888888",
            "secretary_email": "sec@example.com"
        }
        
        invitation = create_invitation(self.clinic, self.owner, data, role="SECRETARY")
        self.assertEqual(invitation.status, "PENDING")
        mock_send_sms.assert_not_called()
        
    def test_accept_secretary_invitation_creates_staff_no_profile(self):
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic,
            doctor_name="Sec",
            doctor_phone="0599999999",
            role="SECRETARY",
            expires_at=timezone.now() + timedelta(days=2)
        )
        
        user = CustomUser.objects.create(phone="0599999999", name="Accepting Sec", roles=["PATIENT"])
        
        staff = accept_invitation(invitation, user)
        
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "ACCEPTED")
        
        # Check Staff
        self.assertEqual(staff.role, "SECRETARY")
        self.assertEqual(staff.clinic, self.clinic)
        
        # Check Profile & Specialties were NOT created
        self.assertFalse(DoctorProfile.objects.filter(user=user).exists())
        
        # Check Roles
        user.refresh_from_db()
        self.assertIn("SECRETARY", user.roles)
