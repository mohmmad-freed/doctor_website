"""Guest browse (Phase 1) tests — public catalog access + data-exposure guards.

Verifies anonymous users can browse clinics/doctors/real-slots, and that the
whitelist holds: no owner/staff PII, internal status, kiosk token, or price ever
reaches a guest. Uses an isolated locmem cache for the slot rate-limiter.
"""
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import City, CustomUser
from clinics.models import Clinic, ClinicStaff, ClinicWorkingHours
from doctors.models import (
    DoctorAvailability,
    DoctorProfile,
    DoctorSpecialty,
    DoctorVerification,
    Specialty,
)
from appointments.models import AppointmentType

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Distinctive values that must never appear in guest-facing HTML.
OWNER_PHONE = "0599000111"
OWNER_NID = "123123123"
PRICE = Decimal("77.00")  # distinctive: 77 should not appear anywhere for guests


@override_settings(CACHES=LOCMEM)
class GuestBrowseTests(TestCase):
    def setUp(self):
        cache.clear()  # isolate the slot rate-limiter counter from other tests
        self.client = Client()
        # Render English so UI-string assertions are readable + deterministic
        # (language-independent data like names/prices is asserted regardless).
        self.client.cookies["lang"] = "en"
        self.city = City.objects.create(name="Nablus")

        self.owner = CustomUser.objects.create_user(
            phone=OWNER_PHONE, name="Owner Doc", password="StrongPass123!",
            national_id=OWNER_NID, role="MAIN_DOCTOR",
        )
        self.owner.roles = ["PATIENT", "MAIN_DOCTOR"]
        self.owner.save()

        self.clinic = Clinic.objects.create(
            name="Shifa Clinic", address="Main St 1", phone="0567000000",
            email="clinic@example.com", specialization="Cardiology",
            description="Best cardiology care.", main_doctor=self.owner,
            city=self.city, status="ACTIVE", is_active=True,
        )
        self.inactive_clinic = Clinic.objects.create(
            name="HiddenInactiveClinic", address="Nowhere", main_doctor=self.owner,
            city=self.city, status="SUSPENDED", is_active=False,
        )
        ClinicWorkingHours.objects.create(
            clinic=self.clinic, weekday=0, start_time=time(9, 0),
            end_time=time(17, 0), is_closed=False,
        )

        self.specialty = Specialty.objects.create(name="Cardiology", name_ar="قلب")

        # Verified DOCTOR staff (should be advertised).
        self.doctor = CustomUser.objects.create_user(
            phone="0599000222", name="Verified Doc", password="StrongPass123!",
            role="DOCTOR",
        )
        self.doctor.roles = ["DOCTOR"]
        self.doctor.save()
        prof = DoctorProfile.objects.create(
            user=self.doctor, bio="Heart specialist.", years_of_experience=8,
        )
        DoctorSpecialty.objects.create(
            doctor_profile=prof, specialty=self.specialty, is_primary=True,
        )
        DoctorVerification.objects.create(
            user=self.doctor, identity_status="IDENTITY_VERIFIED",
        )
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.doctor, role="DOCTOR", is_active=True,
        )

        # Unverified DOCTOR staff (must NOT be advertised).
        self.unverified = CustomUser.objects.create_user(
            phone="0599000333", name="UnverifiedHiddenDoc", password="StrongPass123!",
            role="DOCTOR",
        )
        DoctorProfile.objects.create(user=self.unverified, bio="x")
        DoctorVerification.objects.create(
            user=self.unverified, identity_status="IDENTITY_PENDING_REVIEW",
        )
        ClinicStaff.objects.create(
            clinic=self.clinic, user=self.unverified, role="DOCTOR", is_active=True,
        )

        self.appt_type = AppointmentType.objects.create(
            clinic=self.clinic, name="Consultation", name_ar="استشارة",
            duration_minutes=30, price=PRICE, is_active=True,
        )

        self.future = date.today() + timedelta(days=7)
        DoctorAvailability.objects.create(
            doctor=self.doctor, clinic=self.clinic, day_of_week=self.future.weekday(),
            start_time=time(9, 0), end_time=time(12, 0), is_active=True,
        )

    # ── Clinic list ───────────────────────────────────────────────────────
    def test_clinic_list_is_public_and_lists_active_only(self):
        resp = self.client.get(reverse("browse:index"))  # anonymous
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shifa Clinic")
        self.assertNotContains(resp, "HiddenInactiveClinic")

    def test_clinic_list_search(self):
        resp = self.client.get(reverse("browse:index"), {"q": "Cardiology"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shifa Clinic")
        resp2 = self.client.get(reverse("browse:index"), {"q": "zzznomatch"})
        self.assertNotContains(resp2, "Shifa Clinic")

    # ── Clinic detail ─────────────────────────────────────────────────────
    def test_inactive_clinic_detail_404(self):
        resp = self.client.get(
            reverse("browse:clinic_detail", kwargs={"clinic_id": self.inactive_clinic.id})
        )
        self.assertEqual(resp.status_code, 404)

    def test_clinic_detail_shows_safe_info_and_hides_pii(self):
        resp = self.client.get(
            reverse("browse:clinic_detail", kwargs={"clinic_id": self.clinic.id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shifa Clinic")
        self.assertContains(resp, "Main St 1")
        self.assertContains(resp, "Verified Doc")        # verified doctor advertised
        # Guards: owner PII, kiosk token, unverified doctor must NOT leak.
        self.assertNotContains(resp, OWNER_PHONE)
        self.assertNotContains(resp, OWNER_NID)
        self.assertNotContains(resp, str(self.clinic.display_token))
        self.assertNotContains(resp, "UnverifiedHiddenDoc")

    # ── Doctor detail ─────────────────────────────────────────────────────
    def test_doctor_detail_requires_valid_clinic_association(self):
        # missing clinic_id
        self.assertEqual(
            self.client.get(reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id})).status_code,
            404,
        )
        # doctor not at an unrelated active clinic
        other = Clinic.objects.create(
            name="Other", address="x", main_doctor=self.owner, is_active=True,
        )
        resp = self.client.get(
            reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id}),
            {"clinic_id": other.id},
        )
        self.assertEqual(resp.status_code, 404)

    def test_doctor_detail_shows_services_without_price(self):
        resp = self.client.get(
            reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id}),
            {"clinic_id": self.clinic.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Consultation")     # service name shown
        self.assertContains(resp, "30")               # duration shown
        self.assertNotContains(resp, "77")            # price NEVER shown
        self.assertNotContains(resp, "₪")             # AppointmentType.__str__ price marker
        self.assertContains(resp, "Sign in to see price")

    def test_doctor_detail_real_open_slots_and_book_cta(self):
        resp = self.client.get(
            reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id}),
            {
                "clinic_id": self.clinic.id,
                "date": self.future.isoformat(),
                "appointment_type_id": self.appt_type.id,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="slot-pill"')  # a real open-slot link rendered
        self.assertContains(resp, "09:00")              # 9:00 block start → first slot
        # Slot / book CTA routes through login with a next back to the booking page.
        # (urlencode keeps "/" unencoded, so the booking path appears verbatim.)
        booking = reverse("appointments:book_appointment", kwargs={"clinic_id": self.clinic.id})
        self.assertContains(resp, reverse("accounts:login"))
        self.assertContains(resp, f"next={booking}")

    @patch("browse.views.ratelimit.hit_rate_limit", return_value=True)
    def test_slots_suppressed_when_rate_limited(self, mock_rl):
        """When the per-IP limiter trips, the view hides slots and shows a notice
        instead of computing more — the page still renders (no error)."""
        resp = self.client.get(
            reverse("browse:doctor_detail", kwargs={"doctor_id": self.doctor.id}),
            {
                "clinic_id": self.clinic.id,
                "date": self.future.isoformat(),
                "appointment_type_id": self.appt_type.id,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_rl.called)
        self.assertNotContains(resp, 'class="slot-pill"')    # slot links suppressed
        self.assertContains(resp, "Too many requests")       # guest sees a notice

    # ── Entry point ───────────────────────────────────────────────────────
    def test_landing_exposes_browse_entry_point(self):
        resp = self.client.get(reverse("accounts:landing"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("browse:index"))
