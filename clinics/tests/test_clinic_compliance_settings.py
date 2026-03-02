from datetime import timedelta
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone

from clinics.models import Clinic
from clinics.services import (
    get_clinic_compliance_settings,
    update_clinic_compliance_settings,
    should_block_patient,
    apply_auto_forgiveness,
)
from compliance.models import (
    ClinicComplianceSettings,
    PatientClinicCompliance,
    ComplianceEvent,
)
from compliance.services.compliance_service import record_no_show
from patients.models import PatientProfile

User = get_user_model()


class ClinicComplianceSettingsModelTests(TestCase):
    """Tests for ClinicComplianceSettings model validation."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201000000001",
            national_id="100000001",
            password="password123",
            name="Owner Doctor",
            role="MAIN_DOCTOR",
        )
        self.clinic = Clinic.objects.create(
            name="Test Clinic",
            address="123 Test St",
            main_doctor=self.user,
            status="ACTIVE",
        )

    def test_default_settings_created_via_signal(self):
        """Settings should be auto-created when a clinic is created."""
        self.assertTrue(
            ClinicComplianceSettings.objects.filter(clinic=self.clinic).exists()
        )
        settings = self.clinic.compliance_settings
        self.assertEqual(settings.score_threshold_block, 3)
        self.assertFalse(settings.auto_forgive_enabled)
        self.assertIsNone(settings.auto_forgive_after_days)

    def test_validation_forgive_disabled_days_must_be_null(self):
        """When auto_forgive_enabled=False, auto_forgive_after_days must be None."""
        settings = self.clinic.compliance_settings
        settings.auto_forgive_enabled = False
        settings.auto_forgive_after_days = 30
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_validation_forgive_enabled_days_must_be_positive(self):
        """When auto_forgive_enabled=True, auto_forgive_after_days must be > 0."""
        settings = self.clinic.compliance_settings
        settings.auto_forgive_enabled = True
        settings.auto_forgive_after_days = None
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_validation_valid_settings(self):
        """Valid combinations should pass validation."""
        settings = self.clinic.compliance_settings
        # Disabled + None days
        settings.auto_forgive_enabled = False
        settings.auto_forgive_after_days = None
        settings.full_clean()  # Should not raise

        # Enabled + positive days
        settings.auto_forgive_enabled = True
        settings.auto_forgive_after_days = 30
        settings.full_clean()  # Should not raise


class ClinicComplianceSettingsServiceTests(TestCase):
    """Tests for service functions in clinics/services.py."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201000000002",
            national_id="100000002",
            password="password123",
            name="Owner Doctor 2",
            role="MAIN_DOCTOR",
        )
        self.clinic = Clinic.objects.create(
            name="Service Test Clinic",
            address="456 Test Ave",
            main_doctor=self.user,
            status="ACTIVE",
        )
        self.patient_user = User.objects.create_user(
            phone="+201000000003",
            national_id="100000003",
            password="password123",
            name="Test Patient",
            role="PATIENT",
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)

    def test_get_clinic_compliance_settings(self):
        """Should return existing settings (auto-created by signal)."""
        settings = get_clinic_compliance_settings(self.clinic)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.clinic, self.clinic)

    def test_update_clinic_compliance_settings(self):
        """Should update settings with mapped field names."""
        settings = update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=5,
            forgiveness_enabled=True,
            forgiveness_days=14,
        )
        self.assertEqual(settings.score_threshold_block, 5)
        self.assertTrue(settings.auto_forgive_enabled)
        self.assertEqual(settings.auto_forgive_after_days, 14)

    def test_update_disabling_forgiveness_clears_days(self):
        """Disabling forgiveness should set days to None."""
        update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=3,
            forgiveness_enabled=True,
            forgiveness_days=30,
        )
        settings = update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=3,
            forgiveness_enabled=False,
            forgiveness_days=None,
        )
        self.assertFalse(settings.auto_forgive_enabled)
        self.assertIsNone(settings.auto_forgive_after_days)


class BlockingEnforcementTests(TestCase):
    """Tests for blocking patients based on no-show count."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201000000004",
            national_id="100000004",
            password="password123",
            name="Doctor",
            role="MAIN_DOCTOR",
        )
        self.clinic = Clinic.objects.create(
            name="Blocking Clinic",
            address="789 Block Ave",
            main_doctor=self.user,
            status="ACTIVE",
        )
        self.patient_user = User.objects.create_user(
            phone="+201000000005",
            national_id="100000005",
            password="password123",
            name="Bad Patient",
            role="PATIENT",
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)
        # Set threshold to 2 for easy testing
        update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=2,
            forgiveness_enabled=False,
            forgiveness_days=None,
        )

    def test_patient_blocked_after_threshold(self):
        """Patient should be blocked after reaching the no-show threshold."""
        # First no-show — warned
        record_no_show(self.clinic, self.patient_profile)
        self.assertFalse(should_block_patient(self.clinic, self.patient_profile))

        # Second no-show — blocked (threshold=2)
        record_no_show(self.clinic, self.patient_profile)
        self.assertTrue(should_block_patient(self.clinic, self.patient_profile))

    def test_patient_not_blocked_below_threshold(self):
        """Patient should not be blocked before reaching threshold."""
        record_no_show(self.clinic, self.patient_profile)
        self.assertFalse(should_block_patient(self.clinic, self.patient_profile))


class ForgivenessLogicTests(TestCase):
    """Tests for auto-forgiveness functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+201000000006",
            national_id="100000006",
            password="password123",
            name="Doctor Forgive",
            role="MAIN_DOCTOR",
        )
        self.clinic = Clinic.objects.create(
            name="Forgiveness Clinic",
            address="321 Forgive Ln",
            main_doctor=self.user,
            status="ACTIVE",
        )
        self.patient_user = User.objects.create_user(
            phone="+201000000007",
            national_id="100000007",
            password="password123",
            name="Forgivable Patient",
            role="PATIENT",
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)

    def test_auto_forgiveness_unblocks_patient(self):
        """Patient should be unblocked after forgiveness_days have elapsed."""
        update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=1,
            forgiveness_enabled=True,
            forgiveness_days=7,
        )
        # Record no-show to block the patient
        record_no_show(self.clinic, self.patient_profile)
        self.assertTrue(should_block_patient(self.clinic, self.patient_profile))

        # Backdate the violation
        compliance = PatientClinicCompliance.objects.get(
            clinic=self.clinic, patient=self.patient_profile
        )
        compliance.last_violation_at = timezone.now() - timedelta(days=10)
        # Use update to bypass clean/save override on PatientClinicCompliance if any
        PatientClinicCompliance.objects.filter(pk=compliance.pk).update(
            last_violation_at=compliance.last_violation_at
        )

        # Run forgiveness
        apply_auto_forgiveness(self.clinic)

        # Should be unblocked now
        self.assertFalse(should_block_patient(self.clinic, self.patient_profile))

        # Verify event logged
        event = ComplianceEvent.objects.filter(
            clinic=self.clinic,
            patient=self.patient_profile,
            event_type='AUTO_FORGIVENESS',
        ).first()
        self.assertIsNotNone(event)

    def test_no_forgiveness_when_disabled(self):
        """Should not forgive when forgiveness is disabled."""
        update_clinic_compliance_settings(
            clinic=self.clinic,
            max_no_show_count=1,
            forgiveness_enabled=False,
            forgiveness_days=None,
        )
        record_no_show(self.clinic, self.patient_profile)
        self.assertTrue(should_block_patient(self.clinic, self.patient_profile))

        # Backdate violation
        PatientClinicCompliance.objects.filter(
            clinic=self.clinic, patient=self.patient_profile
        ).update(last_violation_at=timezone.now() - timedelta(days=100))

        apply_auto_forgiveness(self.clinic)
        # Should still be blocked
        self.assertTrue(should_block_patient(self.clinic, self.patient_profile))


class ClinicIsolationTests(TestCase):
    """Tests verifying strict multi-tenant isolation."""

    def setUp(self):
        self.doc1 = User.objects.create_user(
            phone="+201000000008",
            national_id="100000008",
            password="password123",
            name="Doctor A",
            role="MAIN_DOCTOR",
        )
        self.doc2 = User.objects.create_user(
            phone="+201000000009",
            national_id="100000009",
            password="password123",
            name="Doctor B",
            role="MAIN_DOCTOR",
        )
        self.clinic_a = Clinic.objects.create(
            name="Clinic A",
            address="A Street",
            main_doctor=self.doc1,
            status="ACTIVE",
        )
        self.clinic_b = Clinic.objects.create(
            name="Clinic B",
            address="B Street",
            main_doctor=self.doc2,
            status="ACTIVE",
        )
        self.patient_user = User.objects.create_user(
            phone="+201000000010",
            national_id="100000010",
            password="password123",
            name="Shared Patient",
            role="PATIENT",
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)

    def test_settings_isolated_between_clinics(self):
        """Each clinic has its own independent settings."""
        update_clinic_compliance_settings(
            clinic=self.clinic_a,
            max_no_show_count=5,
            forgiveness_enabled=True,
            forgiveness_days=30,
        )
        update_clinic_compliance_settings(
            clinic=self.clinic_b,
            max_no_show_count=2,
            forgiveness_enabled=False,
            forgiveness_days=None,
        )
        settings_a = get_clinic_compliance_settings(self.clinic_a)
        settings_b = get_clinic_compliance_settings(self.clinic_b)

        self.assertEqual(settings_a.score_threshold_block, 5)
        self.assertTrue(settings_a.auto_forgive_enabled)
        self.assertEqual(settings_b.score_threshold_block, 2)
        self.assertFalse(settings_b.auto_forgive_enabled)

    def test_block_in_one_clinic_does_not_affect_other(self):
        """A block in Clinic A should not affect Clinic B."""
        update_clinic_compliance_settings(
            clinic=self.clinic_a,
            max_no_show_count=1,
            forgiveness_enabled=False,
            forgiveness_days=None,
        )
        record_no_show(self.clinic_a, self.patient_profile)
        self.assertTrue(should_block_patient(self.clinic_a, self.patient_profile))
        self.assertFalse(should_block_patient(self.clinic_b, self.patient_profile))
