"""
View tests for the doctor-side improvements:
  #3  Start-from-last-note (copy-forward clinical documentation)
  #2  Prescribing safety net (allergy + active-medication banner)
  #4  Doctor practice analytics
  #1  "My Day" live queue board + day timeline
  #5  Schedule control — time-off / vacation + doctor reschedule

Reuses the two-tenant fixture from test_views.DoctorViewTestBase.
"""

from datetime import date, time, timedelta

from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, AppointmentNotification
from patients.models import (
    ClinicPatient, ClinicalNote, PatientProfile, Prescription, PrescriptionItem, Order,
)
from clinics.models import DoctorAvailabilityException
from doctors.models import DoctorReview
from doctors.services import generate_slots_for_date

from doctors.test_views import DoctorViewTestBase


# ════════════════════════════════════════════════════════════════════
#  #3 — Start from last note (copy-forward)
# ════════════════════════════════════════════════════════════════════

class StartFromLastNoteTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)

    def _make_note(self, **kw):
        defaults = dict(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            subjective="SUBJ_TOKEN_XYZ", plan="PLAN_TOKEN_XYZ",
        )
        defaults.update(kw)
        return ClinicalNote.objects.create(**defaults)

    def test_bare_get_redirects_to_workspace(self):
        """GET without a prefill param keeps the original redirect behaviour."""
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:ws_note_add", args=[self.patient_a.id]))
        self.assertEqual(resp.status_code, 302)

    def test_prefill_last_opens_form_with_prior_values(self):
        source = self._make_note()
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]) + "?prefill=last",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["note_form_open"])
        self.assertEqual(resp.context["prefill_source"].id, source.id)
        # The form must NOT be in edit mode (so submitting creates a new note).
        self.assertIsNone(resp.context.get("edit_note"))
        values = [s["value"] for s in resp.context["active_note_sections"]]
        self.assertIn("SUBJ_TOKEN_XYZ", values)

    def test_prefill_last_empty_when_no_prior_note(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]) + "?prefill=last",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["prefill_empty"])
        self.assertIsNone(resp.context["prefill_source"])

    def test_from_note_prefills_specific_note(self):
        older = self._make_note(subjective="OLDER_ONE")
        newer = self._make_note(subjective="NEWER_ONE")
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]) + f"?from_note={older.id}",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["prefill_source"].id, older.id)
        self.assertNotEqual(older.id, newer.id)

    def test_copy_forward_does_not_mutate_source_and_creates_new(self):
        source = self._make_note()
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]),
            {"clinic_id": self.clinic_a.id, "subjective": "EDITED_COPY", "plan": "PLAN_TOKEN_XYZ"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ClinicalNote.objects.filter(patient=self.patient_a).count(), 2)
        source.refresh_from_db()
        self.assertEqual(source.subjective, "SUBJ_TOKEN_XYZ")  # unchanged

    def test_cross_doctor_prefill_forbidden(self):
        """Doctor B (no shared clinic with patient_a) cannot copy-forward."""
        self._make_note()
        self.client.force_login(self.doctor_b)
        resp = self.client.get(
            reverse("doctors:ws_note_add", args=[self.patient_a.id]) + "?prefill=last",
        )
        self.assertEqual(resp.status_code, 403)


# ════════════════════════════════════════════════════════════════════
#  #2 — Prescribing safety net
# ════════════════════════════════════════════════════════════════════

class PrescribingSafetyTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)
        PatientProfile.objects.create(user=self.patient_a, allergies="Penicillin")

    def test_prescriptions_tab_shows_allergy_banner(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + "?tab=prescriptions",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Penicillin")
        self.assertContains(resp, "allergy_reviewed")
        self.assertIn("safety_active_meds", resp.context)

    def test_active_meds_surface_in_banner(self):
        rx = Prescription.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a, is_active=True,
        )
        PrescriptionItem.objects.create(
            prescription=rx, medication_name="Aspirin", dosage="100mg", frequency="1x",
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + "?tab=prescriptions",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(resp, "Aspirin")

    def test_prescription_records_acknowledgement(self):
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:ws_prescription_add", args=[self.patient_a.id]),
            {
                "clinic_id": self.clinic_a.id,
                "med_name_1": "Amoxicillin", "dosage_1": "500mg", "frequency_1": "3x",
                "allergy_reviewed": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        rx = Prescription.objects.filter(patient=self.patient_a).latest("created_at")
        self.assertIsNotNone(rx.allergy_acknowledged_at)

    def test_prescription_without_acknowledgement_is_null(self):
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:ws_prescription_add", args=[self.patient_a.id]),
            {
                "clinic_id": self.clinic_a.id,
                "med_name_1": "Amoxicillin", "dosage_1": "500mg", "frequency_1": "3x",
            },
            HTTP_HX_REQUEST="true",
        )
        rx = Prescription.objects.filter(patient=self.patient_a).latest("created_at")
        self.assertIsNone(rx.allergy_acknowledged_at)

    def test_drug_order_records_acknowledgement(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:ws_order_add", args=[self.patient_a.id]),
            {
                "clinic_id": self.clinic_a.id, "order_type": "DRUG",
                "title": "Amoxicillin", "dosage": "500mg", "allergy_reviewed": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        # Confirms the orders partial + drug-scoped safety banner render cleanly.
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "allergy_reviewed")
        order = Order.objects.filter(patient=self.patient_a).latest("created_at")
        self.assertIsNotNone(order.allergy_acknowledged_at)


# ════════════════════════════════════════════════════════════════════
#  #4 — Doctor practice analytics
# ════════════════════════════════════════════════════════════════════

class DoctorAnalyticsTests(DoctorViewTestBase):

    URL = None

    def _appt_on(self, the_date, t, status, patient=None):
        patient = patient or self.patient_a
        ClinicPatient.objects.get_or_create(patient=patient, clinic=self.clinic_a)
        return Appointment.objects.create(
            patient=patient, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=the_date, appointment_time=t, status=status,
        )

    def test_requires_login(self):
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.status_code, 302)

    def test_non_doctor_blocked(self):
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.status_code, 403)

    def test_status_totals_and_rates(self):
        today = date.today()
        self._appt_on(today, time(9, 0), Appointment.Status.COMPLETED)
        self._appt_on(today, time(9, 30), Appointment.Status.COMPLETED)
        self._appt_on(today, time(10, 0), Appointment.Status.NO_SHOW)
        self._appt_on(today, time(10, 30), Appointment.Status.CANCELLED)
        self._appt_on(today, time(11, 0), Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total"], 5)
        self.assertEqual(resp.context["completed"], 2)
        self.assertEqual(resp.context["no_show"], 1)
        self.assertEqual(resp.context["cancelled"], 1)
        self.assertEqual(resp.context["no_show_rate"], 20.0)

    def test_only_counts_own_appointments(self):
        today = date.today()
        self._appt_on(today, time(9, 0), Appointment.Status.COMPLETED)
        # Doctor B appointment in another clinic must not be counted.
        ClinicPatient.objects.get_or_create(patient=self.patient_b, clinic=self.clinic_b)
        Appointment.objects.create(
            patient=self.patient_b, clinic=self.clinic_b, doctor=self.doctor_b,
            appointment_type=self.appt_type_b, appointment_date=today,
            appointment_time=time(9, 0), status=Appointment.Status.COMPLETED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.context["total"], 1)

    def test_date_filter_excludes_out_of_range(self):
        today = date.today()
        self._appt_on(today, time(9, 0), Appointment.Status.COMPLETED)
        self._appt_on(today - timedelta(days=60), time(9, 0), Appointment.Status.COMPLETED)
        self.client.force_login(self.doctor_a)
        # Default range (last 30 days) → only the recent one.
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.context["total"], 1)
        # Widen the range → both.
        wide_from = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        resp2 = self.client.get(
            reverse("doctors:doctor_analytics") + f"?date_from={wide_from}&date_to={today:%Y-%m-%d}"
        )
        self.assertEqual(resp2.context["total"], 2)

    def test_rating_breakdown_wired(self):
        DoctorReview.objects.create(doctor=self.doctor_a, patient=self.patient_a, rating=5)
        DoctorReview.objects.create(doctor=self.doctor_a, patient=self.patient_b, rating=3)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:doctor_analytics"))
        self.assertEqual(resp.context["total_reviews"], 2)
        rows = {r["star"]: r["count"] for r in resp.context["rating_rows"]}
        self.assertEqual(rows[5], 1)
        self.assertEqual(rows[3], 1)
        self.assertEqual(rows[4], 0)

    def test_my_reviews_renders_distribution(self):
        """my_reviews.html now surfaces the rating distribution (renders cleanly)."""
        DoctorReview.objects.create(doctor=self.doctor_a, patient=self.patient_a, rating=5)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:my_reviews"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_reviews"], 1)
        self.assertContains(resp, "Full analytics")


# ════════════════════════════════════════════════════════════════════
#  #1 — My Day (live queue board + day timeline)
# ════════════════════════════════════════════════════════════════════

class MyDayTests(DoctorViewTestBase):

    def _appt_today(self, doctor, clinic, patient, status, t=time(9, 0), checked_in=False):
        from patients.models import ClinicPatient as _CP
        _CP.objects.get_or_create(patient=patient, clinic=clinic)
        appt = Appointment.objects.create(
            patient=patient, clinic=clinic, doctor=doctor,
            appointment_type=(self.appt_type_a if clinic == self.clinic_a else self.appt_type_b),
            appointment_date=date.today(), appointment_time=t, status=status,
        )
        if checked_in:
            appt.checked_in_at = timezone.now()
            appt.queue_priority = 1
            appt.save(update_fields=["checked_in_at", "queue_priority"])
        return appt

    def test_requires_login(self):
        resp = self.client.get(reverse("doctors:my_day"))
        self.assertEqual(resp.status_code, 302)

    def test_non_doctor_blocked(self):
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("doctors:my_day"))
        self.assertEqual(resp.status_code, 403)

    def test_queue_lists_only_own_checked_in(self):
        mine = self._appt_today(self.doctor_a, self.clinic_a, self.patient_a,
                                Appointment.Status.CHECKED_IN, checked_in=True)
        theirs = self._appt_today(self.doctor_b, self.clinic_b, self.patient_b,
                                  Appointment.Status.CHECKED_IN, checked_in=True)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:my_day"))
        self.assertEqual(resp.status_code, 200)
        waiting_ids = [row["appt"].id for row in resp.context["waiting"]]
        self.assertIn(mine.id, waiting_ids)
        self.assertNotIn(theirs.id, waiting_ids)

    def test_transition_check_in_stamps_arrival_and_queue(self):
        appt = self._appt_today(self.doctor_a, self.clinic_a, self.patient_a,
                                Appointment.Status.CONFIRMED)
        self.assertIsNone(appt.checked_in_at)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:my_day_transition", args=[appt.id]),
            {"status": "CHECKED_IN"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CHECKED_IN)
        self.assertIsNotNone(appt.checked_in_at)
        self.assertIsNotNone(appt.queue_priority)

    def test_transition_start_visit(self):
        appt = self._appt_today(self.doctor_a, self.clinic_a, self.patient_a,
                                Appointment.Status.CHECKED_IN, checked_in=True)
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:my_day_transition", args=[appt.id]),
            {"status": "IN_PROGRESS"}, HTTP_HX_REQUEST="true",
        )
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.IN_PROGRESS)

    def test_other_date_shows_timeline_without_queue(self):
        future = date.today() + timedelta(days=3)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:my_day") + f"?date={future:%Y-%m-%d}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_today"])
        self.assertNotIn("waiting", resp.context)

    def test_transition_idor_404(self):
        theirs = self._appt_today(self.doctor_b, self.clinic_b, self.patient_b,
                                  Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:my_day_transition", args=[theirs.id]),
            {"status": "CHECKED_IN"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 404)


# ════════════════════════════════════════════════════════════════════
#  #5A — Self-service time-off / vacation
# ════════════════════════════════════════════════════════════════════

class TimeOffTests(DoctorViewTestBase):

    def test_add_exception_creates_row(self):
        self.client.force_login(self.doctor_a)
        start = date.today() + timedelta(days=5)
        end = date.today() + timedelta(days=8)
        self.client.post(
            reverse("doctors:my_schedule"),
            {
                "action": "add_exception", "clinic_id": self.clinic_a.id,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "reason": "Conference",
            },
        )
        exc = DoctorAvailabilityException.objects.filter(
            doctor=self.doctor_a, clinic=self.clinic_a
        ).first()
        self.assertIsNotNone(exc)
        self.assertEqual(exc.start_date, start)
        self.assertEqual(exc.created_by, self.doctor_a)

    def test_exception_blocks_slot_generation(self):
        """Integration: a date inside an active exception yields no bookable slots."""
        target = date.today() + timedelta(days=6)
        DoctorAvailabilityException.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            start_date=target - timedelta(days=1), end_date=target + timedelta(days=1),
            reason="Vacation", created_by=self.doctor_a,
        )
        slots = generate_slots_for_date(
            doctor_id=self.doctor_a.id, clinic_id=self.clinic_a.id,
            target_date=target, duration_minutes=30,
        )
        self.assertEqual(slots, [])

    def test_invalid_range_rejected(self):
        self.client.force_login(self.doctor_a)
        start = date.today() + timedelta(days=8)
        end = date.today() + timedelta(days=5)  # end before start
        self.client.post(
            reverse("doctors:my_schedule"),
            {
                "action": "add_exception", "clinic_id": self.clinic_a.id,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
            },
        )
        self.assertFalse(
            DoctorAvailabilityException.objects.filter(doctor=self.doctor_a).exists()
        )

    def test_delete_exception(self):
        exc = DoctorAvailabilityException.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            start_date=date.today() + timedelta(days=2),
            end_date=date.today() + timedelta(days=3),
            created_by=self.doctor_a,
        )
        self.client.force_login(self.doctor_a)
        self.client.post(
            reverse("doctors:my_schedule"),
            {"action": "delete_exception", "clinic_id": self.clinic_a.id, "exception_id": exc.id},
        )
        self.assertFalse(DoctorAvailabilityException.objects.filter(id=exc.id).exists())

    def test_exceptions_scoped_to_selected_clinic(self):
        DoctorAvailabilityException.objects.create(
            doctor=self.doctor_a, clinic=self.clinic_a,
            start_date=date.today(), end_date=date.today() + timedelta(days=1),
            created_by=self.doctor_a,
        )
        # doctor_a is not staff at clinic_b, so the context only ever shows clinic_a.
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:my_schedule"))
        self.assertEqual(len(resp.context["exceptions"]), 1)


# ════════════════════════════════════════════════════════════════════
#  #5B — Doctor-initiated reschedule
# ════════════════════════════════════════════════════════════════════

class RescheduleTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        self.appt = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a,
            status=Appointment.Status.CONFIRMED,
        )
        self.new_date = date.today() + timedelta(days=4)

    def test_requires_login(self):
        resp = self.client.get(reverse("doctors:reschedule_appointment", args=[self.appt.id]))
        self.assertEqual(resp.status_code, 302)

    def test_non_doctor_blocked(self):
        self.client.force_login(self.patient_a)
        resp = self.client.get(reverse("doctors:reschedule_appointment", args=[self.appt.id]))
        self.assertEqual(resp.status_code, 403)

    def test_get_returns_modal(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:reschedule_appointment", args=[self.appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "reschedule-modal")

    def test_valid_reschedule_updates_and_notifies(self):
        self.client.force_login(self.doctor_a)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                reverse("doctors:reschedule_appointment", args=[self.appt.id]),
                {"appointment_date": self.new_date.isoformat(), "appointment_time": "10:30"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("HX-Redirect", resp)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.appointment_date, self.new_date)
        self.assertEqual(self.appt.appointment_time, time(10, 30))
        self.assertTrue(
            AppointmentNotification.objects.filter(
                appointment=self.appt,
                notification_type=AppointmentNotification.Type.APPOINTMENT_RESCHEDULED,
            ).exists()
        )

    def test_past_date_rejected(self):
        self.client.force_login(self.doctor_a)
        past = date.today() - timedelta(days=1)
        resp = self.client.post(
            reverse("doctors:reschedule_appointment", args=[self.appt.id]),
            {"appointment_date": past.isoformat(), "appointment_time": "10:30"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("HX-Redirect", resp)
        self.appt.refresh_from_db()
        self.assertNotEqual(self.appt.appointment_date, past)

    def test_slot_conflict_rejected(self):
        # Another confirmed appt for the same doctor at the target slot.
        Appointment.objects.create(
            patient=self.patient_b, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment_type=self.appt_type_a,
            appointment_date=self.new_date, appointment_time=time(10, 30),
            status=Appointment.Status.CONFIRMED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:reschedule_appointment", args=[self.appt.id]),
            {"appointment_date": self.new_date.isoformat(), "appointment_time": "10:30"},
            HTTP_HX_REQUEST="true",
        )
        self.assertNotIn("HX-Redirect", resp)
        self.appt.refresh_from_db()
        self.assertNotEqual(self.appt.appointment_date, self.new_date)

    def test_completed_appointment_blocked(self):
        done = self._make_appt(
            self.doctor_a, self.clinic_a, self.patient_a, self.appt_type_a,
            appt_time=time(11, 0), status=Appointment.Status.COMPLETED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:reschedule_appointment", args=[done.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["blocked"])

    def test_idor_404(self):
        theirs = self._make_appt(
            self.doctor_b, self.clinic_b, self.patient_b, self.appt_type_b,
            status=Appointment.Status.CONFIRMED,
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:reschedule_appointment", args=[theirs.id]))
        self.assertEqual(resp.status_code, 404)


# ════════════════════════════════════════════════════════════════════
#  Clinical-spine consolidation: Today (merged dashboard) + quick-view
#  drawer + Workspace Visits tab + appt-context handoff.
# ════════════════════════════════════════════════════════════════════

class SpineConsolidationTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)

    def _appt(self, doctor, clinic, patient, status, when=None, t=time(9, 0)):
        return Appointment.objects.create(
            patient=patient, clinic=clinic, doctor=doctor,
            appointment_type=(self.appt_type_a if clinic == self.clinic_a else self.appt_type_b),
            appointment_date=when or date.today(), appointment_time=t, status=status,
        )

    # ── Today (merged dashboard) ──────────────────────────────────────
    def test_today_home_renders_queue_context(self):
        self._appt(self.doctor_a, self.clinic_a, self.patient_a, Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_today"])
        self.assertIn("waiting", resp.context)
        self.assertIn("action_flags", resp.context)

    def test_my_day_alias_matches_dashboard(self):
        """The my_day URL is now an alias returning the Today view (200, queue ctx)."""
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:my_day"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_today"])
        self.assertIn("waiting", resp.context)

    # ── Quick-view drawer ─────────────────────────────────────────────
    def test_quickview_renders_with_workspace_cta(self):
        appt = self._appt(self.doctor_a, self.clinic_a, self.patient_a, Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_quickview", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["workspace_url"],
                         f"/doctors/patients/{self.patient_a.id}/?tab=overview&appt={appt.id}")

    def test_quickview_idor_404(self):
        theirs = self._appt(self.doctor_b, self.clinic_b, self.patient_b, Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(reverse("doctors:appointment_quickview", args=[theirs.id]))
        self.assertEqual(resp.status_code, 404)

    def test_quickview_post_transition_triggers_queue_refresh(self):
        appt = self._appt(self.doctor_a, self.clinic_a, self.patient_a, Appointment.Status.PENDING)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            reverse("doctors:appointment_quickview", args=[appt.id]),
            {"status": "CONFIRMED"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("HX-Trigger"), "queue-refresh")
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    # ── Visits tab ────────────────────────────────────────────────────
    def test_visits_tab_splits_upcoming_and_past(self):
        past = self._appt(self.doctor_a, self.clinic_a, self.patient_a,
                          Appointment.Status.COMPLETED, when=date.today() - timedelta(days=5))
        upcoming = self._appt(self.doctor_a, self.clinic_a, self.patient_a,
                              Appointment.Status.CONFIRMED, when=date.today() + timedelta(days=5))
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + "?tab=visits",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(past.id, [a.id for a in resp.context["visits_past"]])
        self.assertIn(upcoming.id, [a.id for a in resp.context["visits_upcoming"]])

    def test_visit_links_same_day_note_by_fk(self):
        past = self._appt(self.doctor_a, self.clinic_a, self.patient_a,
                          Appointment.Status.COMPLETED, when=date.today() - timedelta(days=3))
        note = ClinicalNote.objects.create(
            patient=self.patient_a, clinic=self.clinic_a, doctor=self.doctor_a,
            appointment=past, assessment="VISIT_NOTE_TOKEN",
        )
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + "?tab=visits",
            HTTP_HX_REQUEST="true",
        )
        target = next(a for a in resp.context["visits_past"] if a.id == past.id)
        self.assertIn(note, target.day_notes)

    def test_visit_intake_partial_forbidden_cross_clinic(self):
        """Doctor B shares no clinic with patient_a → 403 (clinic-scoped guard)."""
        appt = self._appt(self.doctor_a, self.clinic_a, self.patient_a, Appointment.Status.COMPLETED)
        self.client.force_login(self.doctor_b)
        resp = self.client.get(
            reverse("doctors:ws_visit_intake_partial", args=[self.patient_a.id, appt.id]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 403)

    # ── Appointment-context handoff ───────────────────────────────────
    def test_appt_context_resolved_for_shared_clinic(self):
        appt = self._appt(self.doctor_a, self.clinic_a, self.patient_a, Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id])
            + f"?tab=overview&appt={appt.id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["appt_context"])
        self.assertEqual(resp.context["appt_context"].id, appt.id)

    def test_appt_context_none_for_invalid_or_foreign(self):
        foreign = self._appt(self.doctor_b, self.clinic_b, self.patient_b, Appointment.Status.CONFIRMED)
        self.client.force_login(self.doctor_a)
        # Malformed appt → None (no error)
        resp = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + "?appt=notanint"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["appt_context"])
        # Foreign-clinic appt → None (fails closed)
        resp2 = self.client.get(
            reverse("doctors:patient_workspace", args=[self.patient_a.id]) + f"?appt={foreign.id}"
        )
        self.assertIsNone(resp2.context["appt_context"])
