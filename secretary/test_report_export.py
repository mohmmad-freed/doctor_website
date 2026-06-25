"""
Security tests for the secretary-portal CSV report exports
(report_daily / report_visits / report_noshows / report_doctors).

Covers the hardening added by the data-export audit:
- tenant isolation in the exported rows (no cross-clinic leakage);
- the date-range cap (wide ranges clamped, reversed ranges reset);
- a non-numeric doctor_id is ignored instead of 500-ing;
- every CSV export writes exactly one REPORT_EXPORTED ActivityLog row while the
  on-screen HTML view writes none;
- the per-secretary export rate cap returns 429 once tripped, and fails open
  when the cache is unavailable.
"""

import csv
import io
from datetime import date, time, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from accounts import ratelimit
from appointments.models import Appointment
from clinics.models import ActivityLog
from secretary.tests import SecretaryTestBase


def _csv_rows(response):
    """Decode a CSV export response into a list of rows (header included)."""
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


class ReportExportSecurityTests(SecretaryTestBase):

    def setUp(self):
        super().setUp()
        # Rate-limit counters live in the shared locmem cache — start each test
        # from a clean slate so export caps don't bleed across tests.
        cache.clear()
        self.client.force_login(self.secretary_a)
        self.recent = date.today() - timedelta(days=2)

    # ── Tenant isolation ────────────────────────────────────────────────
    def test_noshows_export_excludes_other_clinic(self):
        # One no-show in each clinic on the same in-range date.
        self._make_appointment(
            clinic=self.clinic_a, status=Appointment.Status.NO_SHOW,
            appointment_date=self.recent, appointment_time=time(9, 0),
        )
        self._make_appointment(
            clinic=self.clinic_b, status=Appointment.Status.NO_SHOW,
            appointment_date=self.recent, appointment_time=time(9, 30),
        )

        resp = self.client.get(
            reverse("secretary:report_noshows"), {"export": "csv"}
        )
        self.assertEqual(resp.status_code, 200)
        rows = _csv_rows(resp)
        data = rows[1:]
        # Only clinic_a's no-show is exported. Doctor column (index 4) of the
        # foreign clinic_b row ("Dr. B") must never appear.
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0][4], "Dr. Ahmad")
        self.assertNotIn("Dr. B", [r[4] for r in data])

    # ── Date-range cap ──────────────────────────────────────────────────
    def test_wide_range_is_clamped(self):
        today = date.today()
        resp = self.client.get(reverse("secretary:report_visits"), {
            "date_from": "2000-01-01", "date_to": today.isoformat(),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["range_clamped"])
        self.assertEqual(
            (resp.context["date_to"] - resp.context["date_from"]).days, 366
        )

    @override_settings(REPORT_MAX_RANGE_DAYS=30)
    def test_max_range_days_setting_is_honored(self):
        today = date.today()
        resp = self.client.get(reverse("secretary:report_noshows"), {
            "date_from": "2000-01-01", "date_to": today.isoformat(),
        })
        self.assertTrue(resp.context["range_clamped"])
        self.assertEqual(
            (resp.context["date_to"] - resp.context["date_from"]).days, 30
        )

    def test_reversed_range_resets_to_defaults(self):
        today = date.today()
        resp = self.client.get(reverse("secretary:report_visits"), {
            "date_from": today.isoformat(),
            "date_to": (today - timedelta(days=10)).isoformat(),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["range_clamped"])
        self.assertEqual(resp.context["date_from"], today - timedelta(days=29))
        self.assertEqual(resp.context["date_to"], today)

    # ── Robustness ──────────────────────────────────────────────────────
    def test_non_numeric_doctor_id_does_not_500(self):
        resp = self.client.get(reverse("secretary:report_visits"), {
            "doctor_id": "abc",
        })
        self.assertEqual(resp.status_code, 200)

    # ── Audit logging ───────────────────────────────────────────────────
    def test_csv_export_writes_one_activity_log(self):
        self._make_appointment(
            clinic=self.clinic_a, status=Appointment.Status.COMPLETED,
            appointment_date=self.recent, appointment_time=time(10, 0),
        )
        resp = self.client.get(
            reverse("secretary:report_visits"), {"export": "csv"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

        logs = ActivityLog.objects.filter(
            action=ActivityLog.Action.REPORT_EXPORTED
        )
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.actor, self.secretary_a)
        self.assertEqual(log.clinic, self.clinic_a)
        self.assertEqual(log.target_type, "Clinic")
        self.assertEqual(log.metadata["report"], "visits")
        self.assertEqual(log.metadata["row_count"], 1)
        self.assertIsNotNone(log.ip)  # test client supplies REMOTE_ADDR

    def test_html_view_writes_no_activity_log(self):
        resp = self.client.get(reverse("secretary:report_visits"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            ActivityLog.objects.filter(
                action=ActivityLog.Action.REPORT_EXPORTED
            ).count(),
            0,
        )

    # ── Rate limiting ───────────────────────────────────────────────────
    @override_settings(EXPORT_MAX_PER_WINDOW=2, EXPORT_WINDOW_SECONDS=600)
    def test_export_rate_limited_after_cap(self):
        url = reverse("secretary:report_doctors")
        self.assertEqual(self.client.get(url, {"export": "csv"}).status_code, 200)
        self.assertEqual(self.client.get(url, {"export": "csv"}).status_code, 200)
        # 3rd export within the window is capped.
        self.assertEqual(self.client.get(url, {"export": "csv"}).status_code, 429)

    @override_settings(EXPORT_MAX_PER_WINDOW=2, EXPORT_WINDOW_SECONDS=600)
    def test_rate_limit_fails_open_on_cache_error(self):
        url = reverse("secretary:report_doctors")
        # Simulate a cache outage: the counter can't be written, so the export
        # must proceed (fail-open) rather than 429.
        with mock.patch.object(ratelimit.cache, "add", side_effect=Exception), \
             mock.patch.object(ratelimit.cache, "incr", side_effect=Exception):
            for _ in range(5):
                self.assertEqual(
                    self.client.get(url, {"export": "csv"}).status_code, 200
                )
