"""Doctor reviews (Phase 3) — eligibility, auto-publish, aggregate, moderation,
and public display on the browse pages.
"""
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import City, CustomUser
from clinics.models import Clinic, ClinicStaff
from doctors.models import DoctorProfile, DoctorReview, DoctorVerification
from doctors import services as doc_services
from appointments.models import Appointment, AppointmentType

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _patient(phone, name="Pat Ient"):
    u = CustomUser.objects.create_user(phone=phone, name=name, password="StrongPass123!")
    u.role = "PATIENT"
    u.roles = ["PATIENT"]
    u.is_verified = True
    u.save()
    return u


@override_settings(CACHES=LOCMEM)
class DoctorReviewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.city = City.objects.create(name="Gaza")
        self.owner = CustomUser.objects.create_user(
            phone="0597000001", name="Owner", password="StrongPass123!", role="MAIN_DOCTOR",
        )
        self.clinic = Clinic.objects.create(
            name="Clinic", address="A", main_doctor=self.owner, city=self.city,
            status="ACTIVE", is_active=True,
        )
        self.doctor = CustomUser.objects.create_user(
            phone="0597000002", name="Dr Who", password="StrongPass123!", role="DOCTOR",
        )
        DoctorProfile.objects.create(user=self.doctor, bio="b")
        DoctorVerification.objects.create(user=self.doctor, identity_status="IDENTITY_VERIFIED")
        ClinicStaff.objects.create(clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True)
        self.appt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="C", name_ar="س", duration_minutes=30,
            price=Decimal("50.00"), is_active=True,
        )
        # patient1 HAS a completed appointment with the doctor; patient2 does not.
        self.p1 = _patient("0597000003", "Ahmed Saleh")
        self.p2 = _patient("0597000004", "No Appt")
        Appointment.objects.create(
            patient=self.p1, doctor=self.doctor, clinic=self.clinic,
            appointment_type=self.appt_type,
            appointment_date=date.today() - timedelta(days=2), appointment_time=time(9, 0),
            status="COMPLETED",
        )
        self.submit_url = reverse("reviews:submit", kwargs={"doctor_id": self.doctor.id})

    # ── Eligibility ───────────────────────────────────────────────────────
    def test_eligibility_helper(self):
        self.assertTrue(doc_services.patient_can_review_doctor(self.p1, self.doctor.id))
        self.assertFalse(doc_services.patient_can_review_doctor(self.p2, self.doctor.id))

    def test_ineligible_patient_cannot_submit(self):
        self.client.force_login(self.p2)
        resp = self.client.post(self.submit_url, {"rating": "5", "comment": "great"})
        self.assertEqual(resp.status_code, 302)  # redirected with an error message
        self.assertEqual(DoctorReview.objects.count(), 0)

    def test_eligible_patient_submits_autopublished(self):
        self.client.force_login(self.p1)
        resp = self.client.post(self.submit_url, {"rating": "4", "comment": "good doctor"})
        self.assertEqual(resp.status_code, 302)
        r = DoctorReview.objects.get(doctor=self.doctor, patient=self.p1)
        self.assertEqual(r.rating, 4)
        self.assertFalse(r.is_hidden)  # auto-published

    def test_one_review_per_patient_updates(self):
        self.client.force_login(self.p1)
        self.client.post(self.submit_url, {"rating": "3", "comment": "ok"})
        self.client.post(self.submit_url, {"rating": "5", "comment": "changed my mind"})
        self.assertEqual(DoctorReview.objects.filter(doctor=self.doctor, patient=self.p1).count(), 1)
        r = DoctorReview.objects.get(doctor=self.doctor, patient=self.p1)
        self.assertEqual(r.rating, 5)
        self.assertEqual(r.comment, "changed my mind")

    def test_invalid_rating_rejected(self):
        self.client.force_login(self.p1)
        for bad in ("0", "6", "abc", ""):
            self.client.post(self.submit_url, {"rating": bad, "comment": "x"})
        self.assertEqual(DoctorReview.objects.count(), 0)

    # ── Aggregate ─────────────────────────────────────────────────────────
    def test_rating_summary_excludes_hidden(self):
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=4)
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p2, rating=2, is_hidden=True)
        s = doc_services.doctor_rating_summary(self.doctor.id)
        self.assertEqual(s["count"], 1)       # hidden one excluded
        self.assertEqual(s["avg"], 4.0)

    # ── Public display on browse ──────────────────────────────────────────
    def test_review_shown_on_browse_and_hidden_excluded(self):
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=5, comment="VISIBLEREVIEW")
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p2, rating=1, comment="HIDDENREVIEW", is_hidden=True)
        url = reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id})
        resp = self.client.get(url, {"clinic_id": self.clinic.id})  # anonymous
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "VISIBLEREVIEW")
        self.assertNotContains(resp, "HIDDENREVIEW")
        # Reviews are fully anonymous (medical privacy) — the reviewer's name,
        # first name included, must never appear.
        self.assertNotContains(resp, "Ahmed")
        self.assertNotContains(resp, "Ahmed Saleh")

    def test_eligible_patient_sees_review_form(self):
        self.client.force_login(self.p1)
        url = reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id})
        resp = self.client.get(url, {"clinic_id": self.clinic.id})
        self.assertContains(resp, reverse("reviews:submit", kwargs={"doctor_id": self.doctor.id}))

    # ── Reporting + auto-hide ─────────────────────────────────────────────
    @patch("doctors.review_views.REVIEW_AUTOHIDE_REPORTS", 2)
    def test_reports_accumulate_and_autohide(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=1, comment="spam?")
        report_url = reverse("reviews:report", kwargs={"review_id": review.id})
        # two DISTINCT reporters → reaches the (patched) threshold of 2 → auto-hidden
        for u in (self.p2, self.owner):
            self.client.force_login(u)
            self.client.post(report_url)
        review.refresh_from_db()
        self.assertEqual(review.report_count, 2)
        self.assertTrue(review.is_hidden)

    def test_repeat_report_by_same_user_is_throttled(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=1)
        report_url = reverse("reviews:report", kwargs={"review_id": review.id})
        self.client.force_login(self.p2)
        self.client.post(report_url)
        self.client.post(report_url)  # same user again → throttled, no second increment
        review.refresh_from_db()
        self.assertEqual(review.report_count, 1)

    # ── Moderation ────────────────────────────────────────────────────────
    def test_clinic_owner_can_hide_and_unhide(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=2)
        self.client.force_login(self.owner)  # owns the clinic employing the doctor
        self.client.post(reverse("reviews:hide", kwargs={"review_id": review.id}))
        review.refresh_from_db()
        self.assertTrue(review.is_hidden)
        self.assertEqual(review.hidden_by, self.owner)
        self.client.post(reverse("reviews:unhide", kwargs={"review_id": review.id}))
        review.refresh_from_db()
        self.assertFalse(review.is_hidden)

    def test_unrelated_user_cannot_hide(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=2)
        self.client.force_login(self.p2)  # a random patient
        resp = self.client.post(reverse("reviews:hide", kwargs={"review_id": review.id}))
        self.assertEqual(resp.status_code, 403)
        review.refresh_from_db()
        self.assertFalse(review.is_hidden)

    def test_doctor_cannot_moderate_own_reviews(self):
        self.assertFalse(doc_services.user_can_moderate_doctor_reviews(self.doctor, self.doctor.id))

    def test_unique_review_constraint(self):
        from django.db import IntegrityError, transaction
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=3)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=5)


@override_settings(CACHES=LOCMEM)
class ReviewEnhancementsTests(DoctorReviewTests):
    """Enhancements: doctor reply, notification, breakdown, portals (extends the
    base setUp from DoctorReviewTests)."""

    # ── Doctor reply (E3b) ────────────────────────────────────────────────
    def test_doctor_replies_and_reply_shows_on_browse(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=4, comment="good")
        self.client.force_login(self.doctor)
        self.client.post(reverse("reviews:reply", kwargs={"review_id": review.id}), {"response": "Thanks REPLYBODY"})
        review.refresh_from_db()
        self.assertEqual(review.doctor_response, "Thanks REPLYBODY")
        self.assertIsNotNone(review.doctor_response_at)
        # The reply is shown publicly on the doctor page.
        self.client.logout()
        resp = self.client.get(
            reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id}),
            {"clinic_id": self.clinic.id},
        )
        self.assertContains(resp, "Thanks REPLYBODY")

    def test_non_doctor_cannot_reply(self):
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=4)
        self.client.force_login(self.p2)
        resp = self.client.post(reverse("reviews:reply", kwargs={"review_id": review.id}), {"response": "x"})
        self.assertEqual(resp.status_code, 403)
        review.refresh_from_db()
        self.assertEqual(review.doctor_response, "")

    # ── Notification (E3c) ────────────────────────────────────────────────
    def test_new_review_notifies_doctor_once(self):
        from appointments.models import AppointmentNotification
        self.client.force_login(self.p1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("reviews:submit", kwargs={"doctor_id": self.doctor.id}),
                             {"rating": "5", "comment": "great"})
        notifs = AppointmentNotification.objects.filter(
            patient=self.doctor, notification_type="DOCTOR_REVIEW_RECEIVED")
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(notifs.first().actor_name, "")  # reviewer stays anonymous
        # Editing the review does NOT create a second notification.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("reviews:submit", kwargs={"doctor_id": self.doctor.id}),
                             {"rating": "2", "comment": "edited"})
        self.assertEqual(notifs.count(), 1)

    # ── Breakdown (E3a) ───────────────────────────────────────────────────
    def test_rating_breakdown(self):
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=5)
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p2, rating=3)
        bd = doc_services.doctor_rating_breakdown(self.doctor.id)
        self.assertEqual(bd[5], 1)
        self.assertEqual(bd[3], 1)
        self.assertEqual(bd[4], 0)

    # ── Doctor "My reviews" page (E3b) ────────────────────────────────────
    def test_doctor_my_reviews_page(self):
        DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=4, comment="MYREVIEWBODY")
        self.client.force_login(self.doctor)
        resp = self.client.get(reverse("doctors:my_reviews"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "MYREVIEWBODY")

    # ── Staff moderation page (E2b) ───────────────────────────────────────
    def test_secretary_moderation_lists_and_hides(self):
        sec = CustomUser.objects.create_user(phone="0597000055", name="Sec", password="p")
        sec.role = "SECRETARY"
        sec.roles = ["SECRETARY"]  # middleware derives staff status from roles[]
        sec.save()
        ClinicStaff.objects.create(clinic=self.clinic, user=sec, role="SECRETARY", is_active=True)
        review = DoctorReview.objects.create(doctor=self.doctor, patient=self.p1, rating=2, comment="MODERATEME")
        self.client.force_login(sec)
        resp = self.client.get(reverse("secretary:reviews"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "MODERATEME")
        # The secretary is authorized to hide it via the /reviews/ endpoint.
        self.client.post(reverse("reviews:hide", kwargs={"review_id": review.id}))
        review.refresh_from_db()
        self.assertTrue(review.is_hidden)

    # ── Patient browse smoke (E1) — view runs with rating wiring ──────────
    def test_patient_browse_doctors_loads_with_ratings(self):
        self.client.force_login(self.p1)
        resp = self.client.get(reverse("patients:browse_doctors"))
        self.assertEqual(resp.status_code, 200)
