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
import re
from datetime import datetime, date

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts import ratelimit
from accounts.models import City
from appointments.services.appointment_type_service import (
    get_appointment_types_for_doctor_in_clinic,
)
from clinics.models import Clinic, ClinicStaff
from doctors.models import DoctorAvailability, DoctorProfile, DoctorReview, DoctorVerification
from doctors.services import (
    doctor_rating_breakdown,
    doctor_rating_summaries,
    doctor_rating_summary,
    generate_slots_for_date,
    patient_can_review_doctor,
    user_can_moderate_doctor_reviews,
    visible_reviews_for_doctor,
)

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
    ratings = doctor_rating_summaries([u.id for u in doctor_users])  # batched, no N+1
    cards = []
    for u in doctor_users:
        card = _doctor_card(u, profiles.get(u.id))
        card["rating"] = ratings.get(u.id, {"avg": None, "count": 0})
        cards.append(card)
    return cards


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

    # ── Reviews / rating (Phase 3) ──────────────────────────────────────────
    rating = doctor_rating_summary(doctor.id)
    _bd = doctor_rating_breakdown(doctor.id)  # {5..1: count} of visible reviews
    _total = rating["count"] or 1
    breakdown = [
        {"star": s, "n": _bd[s], "pct": round(_bd[s] / _total * 100)}
        for s in (5, 4, 3, 2, 1)
    ]
    can_review = patient_can_review_doctor(request.user, doctor.id)
    can_moderate = user_can_moderate_doctor_reviews(request.user, doctor.id)
    # Moderators also see hidden reviews (to unhide); everyone else sees visible only.
    if can_moderate:
        review_objs = (
            DoctorReview.objects.filter(doctor_id=doctor.id)
            .select_related("patient").order_by("-created_at")
        )
    else:
        review_objs = visible_reviews_for_doctor(doctor.id)
    review_page = Paginator(review_objs, 5).get_page(request.GET.get("rpage"))
    reviews = [
        {
            "id": r.id,
            "rating": r.rating,
            "stars": "★" * r.rating + "☆" * (5 - r.rating),
            "comment": r.comment,
            # Privacy: a review reveals a patient-of-doctor relationship, so reviews
            # are shown fully anonymously — no reviewer name is exposed at all.
            "created_at": r.created_at,
            "is_hidden": r.is_hidden,
            "response": r.doctor_response,
            "response_at": r.doctor_response_at,
        }
        for r in review_page
    ]
    my_review = None
    if request.user.is_authenticated:
        mr = DoctorReview.objects.filter(doctor_id=doctor.id, patient=request.user).first()
        if mr:
            my_review = {"rating": mr.rating, "comment": mr.comment}

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
            "rating": rating,
            "breakdown": breakdown,
            "reviews": reviews,
            "review_page": review_page,
            "can_review": can_review,
            "can_moderate": can_moderate,
            "my_review": my_review,
            "review_next": request.get_full_path(),
        },
    )


def booking_prefill_url(intent):
    """Booking-page URL carrying a validated intent as prefill query params.

    Shared by book_intent (authenticated fast-path) and the auth views that
    consume a stashed intent after login / sign-up.
    """
    url = reverse("appointments:book_appointment", kwargs={"clinic_id": intent["clinic_id"]})
    q = f"?doctor_id={intent['doctor_id']}&appointment_type_id={intent['appointment_type_id']}"
    if intent.get("date"):
        q += f"&prefill_date={intent['date']}"
    if intent.get("time"):
        q += f"&prefill_time={intent['time']}"
    return url + q


def book_intent(request):
    """Stash a guest's chosen booking (clinic/doctor/service/date/time) then route
    to auth. After login OR sign-up the intent is consumed and the booking page
    opens pre-filled (see accounts.views._pop_booking_intent_redirect).

    Everything is validated server-side against the SAME public visibility rules
    as the browse pages, so the session can never hold an attacker-chosen pairing,
    a service the doctor doesn't offer, or a past date.
    """
    clinic = get_object_or_404(Clinic, id=request.GET.get("clinic_id"), is_active=True)
    doctor = get_object_or_404(
        User, pk=request.GET.get("doctor_id"), role__in=["DOCTOR", "MAIN_DOCTOR"]
    )

    is_owner = clinic.main_doctor_id == doctor.id
    is_staff = ClinicStaff.objects.filter(
        clinic=clinic, user=doctor, role="DOCTOR", is_active=True
    ).exists()
    if not (is_owner or is_staff):
        raise Http404("Doctor does not practise at this clinic")

    offered = {t.id for t in get_appointment_types_for_doctor_in_clinic(doctor.id, clinic.id)}
    try:
        type_id = int(request.GET.get("appointment_type_id"))
    except (TypeError, ValueError):
        type_id = None
    if type_id not in offered:
        raise Http404("Service not offered by this doctor at this clinic")

    intent = {"clinic_id": clinic.id, "doctor_id": doctor.id, "appointment_type_id": type_id}
    date_str = (request.GET.get("date") or "").strip()
    try:
        if date_str and datetime.strptime(date_str, "%Y-%m-%d").date() >= date.today():
            intent["date"] = date_str
    except ValueError:
        pass
    time_str = (request.GET.get("time") or "").strip()
    if re.match(r"^\d{2}:\d{2}$", time_str):
        intent["time"] = time_str

    request.session["booking_intent"] = intent
    request.session.modified = True

    # Already a signed-in patient → skip auth, go straight to the prefilled page.
    if request.user.is_authenticated and request.user.has_role("PATIENT"):
        request.session.pop("booking_intent", None)
        return redirect(booking_prefill_url(intent))

    # Guest (or non-patient): send to login. The session carries the intent
    # through login OR the multi-step sign-up; it's consumed on auth completion.
    return redirect("accounts:login")
