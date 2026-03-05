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

    # ==========================================
    # NEW TESTS: Rate Limiting
    # ==========================================

    @patch('clinics.services.send_sms')
    def test_rate_limit_per_phone_exceeded(self, mock_send_sms):
        """Creating more than 3 invitations for the same phone within an hour should fail."""
        for i in range(3):
            # Use different clinics or different clinic contexts? No, the per-phone limit is global.
            # We need to use different clinics or the same clinic but clear the pending constraint.
            # Actually, the unique constraint is per (clinic, doctor_phone) where status=PENDING.
            # So we can create 3 invites for different phones per clinic, but the rate limit is per-phone.
            # Let's create 3 invitations from different clinics for the same phone.
            clinic_i = Clinic.objects.create(
                name=f"Clinic {i}", main_doctor=self.owner, status="ACTIVE"
            )
            ClinicSubscription.objects.create(
                clinic=clinic_i, plan_type="MONTHLY",
                expires_at=timezone.now() + timedelta(days=30),
                max_doctors=5, status="ACTIVE"
            )
            data = {
                "doctor_name": f"Doc {i}",
                "doctor_phone": "0561111111",
                "doctor_email": f"doc{i}@test.com",
            }
            create_invitation(clinic_i, self.owner, data)

        # 4th should fail
        clinic_extra = Clinic.objects.create(
            name="Clinic Extra", main_doctor=self.owner, status="ACTIVE"
        )
        ClinicSubscription.objects.create(
            clinic=clinic_extra, plan_type="MONTHLY",
            expires_at=timezone.now() + timedelta(days=30),
            max_doctors=5, status="ACTIVE"
        )
        data = {
            "doctor_name": "Doc 4",
            "doctor_phone": "0561111111",
            "doctor_email": "doc4@test.com",
        }
        with self.assertRaises(ValidationError) as ctx:
            create_invitation(clinic_extra, self.owner, data)
        self.assertIn("الحد الأقصى لعدد الدعوات لهذا الرقم", str(ctx.exception))

    @patch('clinics.services.send_sms')
    def test_rate_limit_per_clinic_exceeded(self, mock_send_sms):
        """Creating more than 10 invitations from the same clinic within an hour should fail."""
        self.subscription.max_doctors = 20
        self.subscription.save()

        for i in range(10):
            phone = f"056200{i:04d}"
            data = {
                "doctor_name": f"Doc {i}",
                "doctor_phone": phone,
                "doctor_email": f"doc{i}@test.com",
            }
            create_invitation(self.clinic, self.owner, data)

        # 11th should fail
        data = {
            "doctor_name": "Doc Extra",
            "doctor_phone": "0562999999",
            "doctor_email": "extra@test.com",
        }
        with self.assertRaises(ValidationError) as ctx:
            create_invitation(self.clinic, self.owner, data)
        self.assertIn("الحد الأقصى لعدد الدعوات من هذه العيادة", str(ctx.exception))

    # ==========================================
    # NEW TESTS: Audit Logging
    # ==========================================

    @patch('clinics.services.send_sms')
    def test_audit_log_on_create(self, mock_send_sms):
        from clinics.models import InvitationAuditLog
        data = {
            "doctor_name": "Audit Doc",
            "doctor_phone": "0563333333",
            "doctor_email": "audit@test.com",
        }
        invitation = create_invitation(self.clinic, self.owner, data)

        log = InvitationAuditLog.objects.get(invitation=invitation)
        self.assertEqual(log.action, "CREATED")
        self.assertEqual(log.performed_by, self.owner)
        self.assertEqual(log.clinic, self.clinic)

    def test_audit_log_on_cancel(self):
        from clinics.models import InvitationAuditLog
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic, doctor_phone="0564444444",
            expires_at=timezone.now() + timedelta(days=2), status="PENDING"
        )
        cancel_invitation(invitation, self.owner)

        log = InvitationAuditLog.objects.get(invitation=invitation, action="CANCELLED")
        self.assertEqual(log.performed_by, self.owner)

    def test_audit_log_on_accept(self):
        from clinics.models import InvitationAuditLog
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic, doctor_phone="0565555555",
            expires_at=timezone.now() + timedelta(days=2), status="PENDING"
        )
        user = CustomUser.objects.create(phone="0565555555", name="Accepting")
        accept_invitation(invitation, user)

        log = InvitationAuditLog.objects.get(invitation=invitation, action="ACCEPTED")
        self.assertEqual(log.performed_by, user)

    def test_audit_log_on_reject(self):
        from clinics.models import InvitationAuditLog
        invitation = ClinicInvitation.objects.create(
            clinic=self.clinic, doctor_phone="0566666666",
            expires_at=timezone.now() + timedelta(days=2), status="PENDING"
        )
        user = CustomUser.objects.create(phone="0566666666", name="Rejecting")
        reject_invitation(invitation, user)

        log = InvitationAuditLog.objects.get(invitation=invitation, action="REJECTED")
        self.assertEqual(log.performed_by, user)

    # ==========================================
    # NEW TESTS: Strict Existing User Validation
    # ==========================================

    @patch('clinics.services.send_sms')
    def test_strict_validation_email_mismatch(self, mock_send_sms):
        """Inviting with wrong email for an existing user should fail."""
        CustomUser.objects.create(
            phone="0567777777", name="Existing", email="real@example.com"
        )
        data = {
            "doctor_name": "Existing",
            "doctor_phone": "0567777777",
            "doctor_email": "wrong@example.com",
        }
        with self.assertRaises(ValidationError) as ctx:
            create_invitation(self.clinic, self.owner, data)
        self.assertIn("البريد الإلكتروني المدخل لا يتطابق", str(ctx.exception))

    @patch('clinics.services.send_sms')
    def test_strict_validation_national_id_mismatch(self, mock_send_sms):
        """Inviting with wrong national_id for an existing user should fail."""
        CustomUser.objects.create(
            phone="0568888888", name="Existing", national_id="111111111"
        )
        data = {
            "doctor_name": "Existing",
            "doctor_phone": "0568888888",
            "doctor_email": "doc@example.com",
            "doctor_national_id": "999999999",
        }
        with self.assertRaises(ValidationError) as ctx:
            create_invitation(self.clinic, self.owner, data)
        self.assertIn("رقم الهوية الوطنية المدخل لا يتطابق", str(ctx.exception))

    @patch('clinics.services.send_sms')
    def test_strict_validation_matching_data_passes(self, mock_send_sms):
        """Inviting with correct data for an existing user should succeed."""
        CustomUser.objects.create(
            phone="0569999999", name="Existing",
            email="correct@example.com", national_id="222222222"
        )
        data = {
            "doctor_name": "Existing",
            "doctor_phone": "0569999999",
            "doctor_email": "correct@example.com",
            "doctor_national_id": "222222222",
        }
        invitation = create_invitation(self.clinic, self.owner, data)
        self.assertEqual(invitation.status, "PENDING")
