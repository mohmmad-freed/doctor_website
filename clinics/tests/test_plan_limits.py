"""
Tests for clinics plan limits, subscription capacity checks, and invitation blocking.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from clinics.models import Clinic, ClinicStaff, ClinicSubscription

User = get_user_model()


def _make_clinic(main_doctor):
    """Helper to create a bare Clinic."""
    return Clinic.objects.create(
        name="Test Clinic",
        address="Test Address",
        main_doctor=main_doctor,
    )


def _make_subscription(clinic, plan_name, max_doctors=None, max_secretaries=None):
    """Helper to create a ClinicSubscription with sane defaults."""
    plan_limits = ClinicSubscription.PLAN_LIMITS.get(plan_name, {})
    return ClinicSubscription.objects.create(
        clinic=clinic,
        plan_name=plan_name,
        expires_at=timezone.now() + timedelta(days=365),
        max_doctors=max_doctors if max_doctors is not None else plan_limits.get("doctors", 2),
        max_secretaries=max_secretaries if max_secretaries is not None else plan_limits.get("secretaries", 5),
        status="ACTIVE",
    )


class PlanLimitsTest(TestCase):
    """PLAN_LIMITS dict correctness tests."""

    def test_small_plan_allows_5_secretaries(self):
        limits = ClinicSubscription.PLAN_LIMITS["SMALL"]
        self.assertEqual(limits["secretaries"], 5)

    def test_small_plan_allows_2_doctors(self):
        limits = ClinicSubscription.PLAN_LIMITS["SMALL"]
        self.assertEqual(limits["doctors"], 2)

    def test_medium_plan_allows_5_secretaries(self):
        limits = ClinicSubscription.PLAN_LIMITS["MEDIUM"]
        self.assertEqual(limits["secretaries"], 5)

    def test_medium_plan_allows_4_doctors(self):
        limits = ClinicSubscription.PLAN_LIMITS["MEDIUM"]
        self.assertEqual(limits["doctors"], 4)

    def test_enterprise_plan_not_in_plan_limits(self):
        """ENTERPRISE should be absent from PLAN_LIMITS — admin sets limits explicitly."""
        self.assertNotIn("ENTERPRISE", ClinicSubscription.PLAN_LIMITS)

    def test_enterprise_plan_enforces_admin_defined_capacity(self):
        """Enterprise subscription uses whatever the admin sets on max_doctors/max_secretaries."""
        main_doctor = User.objects.create_user(
            phone="0591900001", password="pw", name="Dr Owner", role="MAIN_DOCTOR"
        )
        clinic = _make_clinic(main_doctor)
        sub = ClinicSubscription.objects.create(
            clinic=clinic,
            plan_name=ClinicSubscription.PlanName.ENTERPRISE,
            expires_at=timezone.now() + timedelta(days=365),
            max_doctors=10,
            max_secretaries=20,
            status="ACTIVE",
        )
        self.assertEqual(sub.max_doctors, 10)
        self.assertEqual(sub.max_secretaries, 20)
        self.assertTrue(sub.can_add_doctor())
        self.assertTrue(sub.can_add_secretary())


class CanAddSecretaryTest(TestCase):
    """can_add_secretary() and can_add_doctor() logic tests."""

    def setUp(self):
        self.main_doctor = User.objects.create_user(
            phone="0591900002", password="pw", name="Dr Main", role="MAIN_DOCTOR"
        )
        self.clinic = _make_clinic(self.main_doctor)
        # Create MAIN_DOCTOR staff record
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.main_doctor, role="MAIN_DOCTOR", added_by=self.main_doctor
        )

    def _add_secretary(self, phone):
        user = User.objects.create_user(phone=phone, password="pw", name="Sec", role="SECRETARY")
        ClinicStaff.objects.create(
            clinic=self.clinic, user=user, role="SECRETARY", added_by=self.main_doctor
        )
        return user

    def test_can_add_secretary_within_limit(self):
        sub = _make_subscription(self.clinic, "SMALL")  # max_secretaries=5
        # 0 current secretaries — can add
        self.assertTrue(sub.can_add_secretary())

    def test_invitation_blocked_when_secretary_limit_reached(self):
        sub = _make_subscription(self.clinic, "SMALL", max_secretaries=2)
        self._add_secretary("0591900010")
        self._add_secretary("0591900011")
        # Now at limit
        self.assertFalse(sub.can_add_secretary())

    def test_can_add_secretary_with_explicit_zero_is_unlimited(self):
        """max_secretaries=0 means unlimited — explicit admin opt-in."""
        sub = _make_subscription(self.clinic, "ENTERPRISE", max_doctors=0, max_secretaries=0)
        # Add many secretaries
        for i in range(10):
            self._add_secretary(f"059{1900100 + i}")
        self.assertTrue(sub.can_add_secretary())

    def test_can_add_doctor_unlimited_when_zero(self):
        sub = _make_subscription(self.clinic, "ENTERPRISE", max_doctors=0, max_secretaries=0)
        self.assertTrue(sub.can_add_doctor())
