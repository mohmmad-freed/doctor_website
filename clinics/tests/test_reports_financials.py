"""
Tests for the owner Reports page financial summary:
revenue = actual payments collected, costs = approved purchase requests,
net = revenue − costs, and debt = live outstanding balance (NOT period-filtered).
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from clinics.models import Clinic
from secretary.models import Invoice, Payment, PurchaseRequest

User = get_user_model()


class ReportsFinancialsTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            phone="0590002001", password="pass", name="مالك", role="MAIN_DOCTOR"
        )
        self.patient = User.objects.create_user(
            phone="0590002002", password="pass", name="مريض", role="PATIENT"
        )
        self.clinic = Clinic.objects.create(
            name="عيادة المالية", address="Addr", main_doctor=self.owner
        )

        # Collected revenue: a payment of ₪300 received now.
        invoice = Invoice.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            invoice_number="INV-2030-000001",
            status=Invoice.Status.PAID,
            subtotal=300,
            total=300,
            amount_paid=300,
            balance_due=0,
            created_by=self.owner,
        )
        Payment.objects.create(
            invoice=invoice,
            clinic=self.clinic,
            amount=300,
            method=Payment.Method.CASH,
            received_by=self.owner,
        )

        # Live debt: a finalized (ISSUED, standalone) invoice with ₪100 owed.
        Invoice.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            invoice_number="INV-2030-000002",
            status=Invoice.Status.ISSUED,
            subtotal=100,
            total=100,
            amount_paid=0,
            balance_due=100,
            created_by=self.owner,
        )

    def _make_approved_purchase(self, number, total, reviewed_at):
        return PurchaseRequest.objects.create(
            clinic=self.clinic,
            requested_by=self.owner,
            request_number=number,
            title="مستلزمات",
            category=PurchaseRequest.Category.CLINIC,
            status=PurchaseRequest.Status.APPROVED,
            total=total,
            reviewed_by=self.owner,
            reviewed_at=reviewed_at,
        )

    def test_all_time_financials(self):
        self._make_approved_purchase("PR-2030-000001", 120, timezone.now())
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("clinics:reports"), {"date_range": "all_time"})
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx["gross_revenue"], 300)
        self.assertEqual(ctx["total_costs"], 120)
        self.assertEqual(ctx["net_revenue"], 180)
        self.assertEqual(ctx["total_debt"], 100)

    def test_costs_respect_period_but_debt_does_not(self):
        # Purchase approved 60 days ago — outside "this week".
        self._make_approved_purchase(
            "PR-2030-000002", 120, timezone.now() - timedelta(days=60)
        )
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("clinics:reports"), {"date_range": "this_week"})
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        # Cost falls outside the window → excluded; revenue (paid now) stays in.
        self.assertEqual(ctx["total_costs"], 0)
        self.assertEqual(ctx["gross_revenue"], 300)
        self.assertEqual(ctx["net_revenue"], 300)
        # Debt is a live snapshot, unaffected by the period filter.
        self.assertEqual(ctx["total_debt"], 100)
