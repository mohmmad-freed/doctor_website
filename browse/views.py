"""Guest browse views — public, read-only catalog of clinics & doctors.

Security model (Option A — dedicated public namespace):
- No @login_required and no PATIENT-role gate: anonymous guests are welcome.
- We reuse the *visibility querysets* of the patient-facing browse
  (Clinic.is_active=True; doctors gated by DoctorVerification IDENTITY_VERIFIED)
  but build explicit whitelisted dicts so a template can never reach owner/staff
  PII, internal status, the kiosk display_token, subscription/activation data —
  or AppointmentType.price (guests must "sign in to see prices"; per product).
- The gated patient/booking views are NOT touched.
"""
from datetime import datetime, date

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from accounts import ratelimit
from accounts.models import City
from appointments.services.appointment_type_service import (
    get_appointment_types_for_doctor_in_clinic,
)
from clinics.models import Clinic, ClinicStaff
from doctors.models import DoctorAvailability, DoctorProfile, DoctorVerification
from doctors.services import generate_slots_for_date

User = get_user_model()

# Per-IP cap on the (scrape-prone) live slot computation. Legitimate-but-capped,
# so we use the plain rolling-window limiter (no lockout ladder). Fail-open.
BROWSE_SLOTS_MAX = 40
BROWSE_SLOTS_WINDOW = 5 * 60


def _lang(request):
    return getattr(request, "LANGUAGE_CODE", "ar")


def _fmt_time(t):
    """Render a datetime.time as HH:MM (slot dicts carry time objects)."""
    return t.strftime("%H:%M") if t else ""


def _doctor_card(user, profile):
    """Whitelisted public doctor summary — no phone/email/national_id/PII."""
    return {
        "id": user.id,
        "name": user.name,
        "bio": (profile.bio if profile else "") or "",
        "years_of_experience": profile.years_of_experience if profile else None,
        "specialties": [s.display_name for s in profile.specialties.all()] if profile else [],
    }


def _verified_clinic_doctors(clinic):
    """Bookable, advertise-safe doctors for a clinic.

    Set = the clinic owner (main_doctor) + active DOCTOR staff who have passed
    platform identity verification (IDENTITY_VERIFIED). Mirrors who a patient can
    actually book (book_appointment_view resolves main_doctor + DOCTOR staff),
    while not advertising unverified staff doctors. Batched to avoid N+1.
    """
    staff_users = [
        s.user
        for s in ClinicStaff.objects.filter(
            clinic=clinic, role="DOCTOR", is_active=True
        ).select_related("user")
    ]
    verified_ids = set(
        DoctorVerification.objects.filter(
            user_id__in=[u.id for u in staff_users],
            identity_status="IDENTITY_VERIFIED",
        ).values_list("user_id", flat=True)
    )

    doctor_users = []
    if clinic.main_doctor_id:
        doctor_users.append(clinic.main_doctor)
    for u in staff_users:
        if u.id != clinic.main_doctor_id and u.id in verified_ids:
            doctor_users.append(u)

    profiles = {
        p.user_id: p
        for p in DoctorProfile.objects.filter(
            user_id__in=[u.id for u in doctor_users]
        ).prefetch_related("specialties")
    }
    return [_doctor_card(u, profiles.get(u.id)) for u in doctor_users]


def clinic_list(request):
    """GET /browse/ — public directory of active clinics (search + city filter)."""
    clinics_qs = (
        Clinic.objects.filter(is_active=True)
        .select_related("city")
        .annotate(
            staff_doctor_count=Count(
                "staff_members",
                filter=Q(staff_members__role="DOCTOR", staff_members__is_active=True),
            )
        )
    )

    search_query = (request.GET.get("q") or "").strip()
    if search_query:
        clinics_qs = clinics_qs.filter(
            Q(name__icontains=search_query)
            | Q(address__icontains=search_query)
            | Q(specialization__icontains=search_query)
        )

    selected_city_id = (request.GET.get("city_id") or "").strip()
    if selected_city_id:
        try:
            clinics_qs = clinics_qs.filter(city_id=int(selected_city_id))
        except (TypeError, ValueError):
            selected_city_id = ""

    clinics_qs = clinics_qs.order_by("-created_at")

    clinics = [
        {
            "id": c.id,
            "name": c.name,
            "address": c.address,
            "specialization": c.specialization,
            "city": c.city.name if c.city else "",
            "description": c.description,
            "logo": c.logo.url if c.logo else "",
            # owner (main_doctor, always set) + active DOCTOR staff
            "doctor_count": c.staff_doctor_count + (1 if c.main_doctor_id else 0),
        }
        for c in clinics_qs
    ]

    return render(
        request,
        "browse/clinic_list.html",
        {
            "clinics": clinics,
            "cities": City.objects.order_by("name"),
            "search_query": search_query,
            "selected_city_id": selected_city_id,
        },
    )


def clinic_detail(request, clinic_id):
    """GET /browse/clinics/<id>/ — public clinic page: info, hours, doctors."""
    clinic = get_object_or_404(
        Clinic.objects.select_related("city", "main_doctor"),
        id=clinic_id,
        is_active=True,
    )

    working_hours = [
        {
            "day": wh.get_weekday_display(),
            "is_closed": wh.is_closed,
            "start": _fmt_time(wh.start_time),
            "end": _fmt_time(wh.end_time),
        }
        for wh in clinic.working_hours.all()
    ]

    clinic_ctx = {
        "id": clinic.id,
        "name": clinic.name,
        "address": clinic.address,
        "phone": clinic.phone,
        "email": clinic.email,
        "specialization": clinic.specialization,
        "description": clinic.description,
        "city": clinic.city.name if clinic.city else "",
        "logo": clinic.logo.url if clinic.logo else "",
    }

    return render(
        request,
        "browse/clinic_detail.html",
        {
            "clinic": clinic_ctx,
            "doctors": _verified_clinic_doctors(clinic),
            "working_hours": working_hours,
        },
    )


def doctor_detail(request, doctor_id):
    """GET /browse/doctors/<id>/?clinic_id=<id> — doctor profile, services
    (no price), and REAL open slots for a chosen date+service.

    A clinic context is required because services/availability are per-clinic.
    """
    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])

    clinic_id = (request.GET.get("clinic_id") or "").strip()
    if not clinic_id:
        raise Http404("clinic_id is required")
    clinic = get_object_or_404(Clinic, id=clinic_id, is_active=True)

    # The doctor must actually practise at this clinic (owner or active DOCTOR
    # staff) — prevents browsing arbitrary doctor/clinic pairings.
    is_owner = clinic.main_doctor_id == doctor.id
    is_staff = ClinicStaff.objects.filter(
        clinic=clinic, user=doctor, role="DOCTOR", is_active=True
    ).exists()
    if not (is_owner or is_staff):
        raise Http404("Doctor does not practise at this clinic")

    profile = (
        DoctorProfile.objects.filter(user_id=doctor.id)
        .prefetch_related("specialties")
        .first()
    )

    # Appointment types offered here — PRICE DELIBERATELY OMITTED for guests.
    # Never render the AppointmentType object itself (its __str__ embeds ₪price).
    types = list(get_appointment_types_for_doctor_in_clinic(doctor.id, clinic.id))
    appointment_types = [
        {
            "id": t.id,
            "name": t.display_name,
            "duration_minutes": t.duration_minutes,
            "description": t.description,
        }
        for t in types
    ]

    weekly_schedule = [
        {
            "day": av.get_day_of_week_display(),
            "start": _fmt_time(av.start_time),
            "end": _fmt_time(av.end_time),
        }
        for av in DoctorAvailability.objects.filter(
            doctor_id=doctor.id, clinic_id=clinic.id, is_active=True
        ).order_by("day_of_week", "start_time")
    ]

    # Optional live slots: only when both a date and a service are chosen.
    slots = None
    slot_error = None
    selected_date = (request.GET.get("date") or "").strip()
    selected_type_id = (request.GET.get("appointment_type_id") or "").strip()
    if selected_date and selected_type_id:
        ip = ratelimit.client_ip(request)
        if ratelimit.hit_rate_limit("browse_slots", ip, BROWSE_SLOTS_MAX, BROWSE_SLOTS_WINDOW):
            slot_error = (
                "Too many requests. Please try again shortly."
                if _lang(request) == "en"
                else "طلبات كثيرة. يرجى المحاولة بعد قليل."
            )
        else:
            try:
                target = datetime.strptime(selected_date, "%Y-%m-%d").date()
            except ValueError:
                target = None
            sel_type = next((t for t in types if str(t.id) == selected_type_id), None)
            if target and target >= date.today() and sel_type:
                raw = generate_slots_for_date(
                    doctor.id, clinic.id, target, sel_type.duration_minutes
                )
                slots = [
                    {"time": _fmt_time(s["time"]), "end_time": _fmt_time(s["end_time"])}
                    for s in raw
                    if s.get("is_available")
                ]
            else:
                slot_error = (
                    "Please choose a valid future date and service."
                    if _lang(request) == "en"
                    else "يرجى اختيار تاريخ مستقبلي وخدمة صحيحة."
                )

    # "Sign in to book" target: the gated booking page. For a guest, @login_required
    # bounces to /login/?next=… and the validated-next handoff (Phase 0) resumes here.
    book_url = (
        reverse("appointments:book_appointment", kwargs={"clinic_id": clinic.id})
        + f"?doctor_id={doctor.id}"
    )

    return render(
        request,
        "browse/doctor_detail.html",
        {
            "doctor": _doctor_card(doctor, profile),
            "clinic": {"id": clinic.id, "name": clinic.name, "city": clinic.city.name if clinic.city else ""},
            "appointment_types": appointment_types,
            "weekly_schedule": weekly_schedule,
            "slots": slots,
            "slot_error": slot_error,
            "selected_date": selected_date,
            "selected_type_id": selected_type_id,
            "today": date.today().isoformat(),
            "book_url": book_url,
        },
    )
