"""
Tests for the Doctor Invitation & Onboarding flow.

Covers:
- New doctor invitation with PendingDoctorIdentity lock
- Existing doctor invitation (identity resolution)
- Patient-to-doctor upgrade
- Expired invitation immutability
- Revoked membership re-invitation
- Accept invitation creates DoctorVerification + ClinicDoctorCredential
- PendingDoctorIdentity lock release on accept
"""

from django.test import TestCase, RequestFactory
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta
from unittest.mock import patch

from accounts.models import CustomUser
from clinics.models import (
    Clinic, ClinicStaff, ClinicSubscription,
    ClinicInvitation, PendingDoctorIdentity,
)
from clinics.services import (
    create_invitation, accept_invitation,
    cancel_invitation, reject_invitation,
)
from doctors.models import (
    DoctorProfile, DoctorSpecialty, Specialty,
    DoctorVerification, ClinicDoctorCredential,
)


class InvitationTestBase(TestCase):
    """Shared setup for invitation tests."""

    def setUp(self):
        # Create owner
        self.owner = CustomUser.objects.create_user(
            phone="0591234567",
            password="testpass123",
            name="Dr. Owner",
            role="MAIN_DOCTOR",
            roles=["MAIN_DOCTOR"],
        )
        self.owner.email = "owner@test.com"
        self.owner.save()

        # Create specialty
        self.specialty = Specialty.objects.create(
            name="Cardiology",
            name_ar="أمراض القلب",
        )

        # Create clinic
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            address="Test Address",
            phone="0591111111",
            status="ACTIVE",
            main_doctor=self.owner,
        )
        self.clinic.specialties.add(self.specialty)

        ClinicStaff.objects.create(
            clinic=self.clinic,
            user=self.owner,
            role="MAIN_DOCTOR",
            added_by=self.owner,
        )

        ClinicSubscription.objects.create(
            clinic=self.clinic,
            plan_type="PRO",
            expires_at=timezone.now() + timedelta(days=365),
            max_doctors=5,
            status="ACTIVE",
        )

        self.factory = RequestFactory()


class CreateInvitationTests(InvitationTestBase):

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_invite_new_doctor_creates_pending_lock(self, mock_email):
        """Inviting a new phone should create a PendingDoctorIdentity lock."""
        data = {
            "doctor_name": "New Doctor",
            "doctor_phone": "0567890123",
            "doctor_email": "newdoc@test.com",
        }
        invitation = create_invitation(self.clinic, self.owner, data, role="DOCTOR")

        self.assertEqual(invitation.status, "PENDING")
        self.assertTrue(
            PendingDoctorIdentity.objects.filter(phone="0567890123").exists()
        )

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_invite_existing_user_no_lock(self, mock_email):
        """Inviting an existing user should NOT create a PendingDoctorIdentity lock."""
        existing = CustomUser.objects.create_user(
            phone="0561111111",
            password="testpass",
            name="Existing Doc",
            role="PATIENT",
            roles=["PATIENT"],
        )
        data = {
            "doctor_name": "Existing Doc",
            "doctor_phone": "0561111111",
            "doctor_email": "existing@test.com",
        }
        create_invitation(self.clinic, self.owner, data, role="DOCTOR")
        self.assertFalse(
            PendingDoctorIdentity.objects.filter(phone="0561111111").exists()
        )

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_invite_self_raises(self, mock_email):
        """Owner cannot invite themselves."""
        data = {
            "doctor_name": "Owner",
            "doctor_phone": self.owner.phone,
            "doctor_email": "owner@test.com",
        }
        with self.assertRaises(ValidationError):
            create_invitation(self.clinic, self.owner, data)

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_duplicate_pending_invite_raises(self, mock_email):
        """Cannot send duplicate pending invitation to same phone at same clinic."""
        data = {
            "doctor_name": "Doc",
            "doctor_phone": "0562222222",
            "doctor_email": "doc@test.com",
        }
        create_invitation(self.clinic, self.owner, data)
        with self.assertRaises(ValidationError):
            create_invitation(self.clinic, self.owner, data)

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_expired_invite_allows_re_invite(self, mock_email):
        """Expired invitation should be marked EXPIRED and allow fresh invite."""
        data = {
            "doctor_name": "Doc",
            "doctor_phone": "0563333333",
            "doctor_email": "doc@test.com",
        }
        inv = create_invitation(self.clinic, self.owner, data)
        # Force expire
        inv.expires_at = timezone.now() - timedelta(hours=1)
        inv.save()

        # Re-invite should work
        inv2 = create_invitation(self.clinic, self.owner, data)
        self.assertEqual(inv2.status, "PENDING")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "EXPIRED")

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_email_delivery_uses_stored_email_for_existing_user(self, mock_email):
        """For existing users, email should be their stored email, not the entered one."""
        existing = CustomUser.objects.create_user(
            phone="0564444444",
            password="testpass",
            name="Doc",
            role="PATIENT",
            roles=["PATIENT"],
        )
        existing.email = "real@test.com"
        existing.save()

        data = {
            "doctor_name": "Doc",
            "doctor_phone": "0564444444",
            "doctor_email": "wrong@test.com",  # This should be overridden
        }
        req = self.factory.get("/")
        inv = create_invitation(self.clinic, self.owner, data, request=req)
        self.assertEqual(inv.doctor_email, "real@test.com")


class AcceptInvitationTests(InvitationTestBase):

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_accept_creates_verification_records(self, mock_email):
        """Accepting an invitation should create DoctorVerification + ClinicDoctorCredential."""
        doctor_user = CustomUser.objects.create_user(
            phone="0565555555",
            password="testpass",
            name="Dr. Accept",
            role="PATIENT",
            roles=["PATIENT"],
        )
        data = {
            "doctor_name": "Dr. Accept",
            "doctor_phone": "0565555555",
            "doctor_email": "accept@test.com",
            "specialties": [self.specialty],
        }
        inv = create_invitation(self.clinic, self.owner, data)
        inv.specialties.set([self.specialty])

        accept_invitation(inv, doctor_user)

        # DoctorVerification should exist
        self.assertTrue(DoctorVerification.objects.filter(user=doctor_user).exists())
        dv = DoctorVerification.objects.get(user=doctor_user)
        self.assertEqual(dv.identity_status, "IDENTITY_UNVERIFIED")

        # ClinicDoctorCredential should exist
        self.assertTrue(
            ClinicDoctorCredential.objects.filter(
                doctor=doctor_user, clinic=self.clinic, specialty=self.specialty
            ).exists()
        )

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_accept_releases_pending_lock(self, mock_email):
        """Accepting should release PendingDoctorIdentity lock."""
        data = {
            "doctor_name": "Dr. New",
            "doctor_phone": "0566666666",
            "doctor_email": "new@test.com",
        }
        inv = create_invitation(self.clinic, self.owner, data)
        self.assertTrue(PendingDoctorIdentity.objects.filter(phone="0566666666").exists())

        # Create the user (simulating registration)
        new_user = CustomUser.objects.create_user(
            phone="0566666666",
            password="testpass",
            name="Dr. New",
            role="PATIENT",
            roles=["PATIENT"],
        )
        accept_invitation(inv, new_user)

        # Lock should be released
        self.assertFalse(PendingDoctorIdentity.objects.filter(phone="0566666666").exists())

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_accept_upgrades_patient_to_doctor(self, mock_email):
        """A patient accepting a doctor invitation should gain the DOCTOR role."""
        patient = CustomUser.objects.create_user(
            phone="0567777777",
            password="testpass",
            name="Patient Upgrade",
            role="PATIENT",
            roles=["PATIENT"],
        )
        data = {
            "doctor_name": "Patient Upgrade",
            "doctor_phone": "0567777777",
            "doctor_email": "patient@test.com",
        }
        inv = create_invitation(self.clinic, self.owner, data)
        accept_invitation(inv, patient)

        patient.refresh_from_db()
        self.assertIn("DOCTOR", patient.roles)
        self.assertEqual(patient.role, "DOCTOR")
        self.assertTrue(DoctorProfile.objects.filter(user=patient).exists())

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_accept_expired_raises(self, mock_email):
        """Cannot accept an expired invitation."""
        doctor_user = CustomUser.objects.create_user(
            phone="0568888888",
            password="testpass",
            name="Doc",
            role="PATIENT",
            roles=["PATIENT"],
        )
        data = {
            "doctor_name": "Doc",
            "doctor_phone": "0568888888",
            "doctor_email": "exp@test.com",
        }
        inv = create_invitation(self.clinic, self.owner, data)
        inv.expires_at = timezone.now() - timedelta(hours=1)
        inv.save()

        with self.assertRaises(ValidationError):
            accept_invitation(inv, doctor_user)

    @patch("accounts.email_utils.send_doctor_invitation_email")
    def test_revoked_membership_reactivation(self, mock_email):
        """A doctor with revoked membership can re-accept a new invitation."""
        doctor_user = CustomUser.objects.create_user(
            phone="0569999999",
            password="testpass",
            name="Dr. Revoked",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        # Create a revoked staff record
        staff = ClinicStaff.objects.create(
            clinic=self.clinic,
            user=doctor_user,
            role="DOCTOR",
            added_by=self.owner,
            is_active=False,
            revoked_at=timezone.now() - timedelta(days=30),
        )

        data = {
            "doctor_name": "Dr. Revoked",
            "doctor_phone": "0569999999",
            "doctor_email": "revoked@test.com",
        }
        inv = create_invitation(self.clinic, self.owner, data)
        accept_invitation(inv, doctor_user)

        staff.refresh_from_db()
        self.assertIsNone(staff.revoked_at)
        self.assertTrue(staff.is_active)