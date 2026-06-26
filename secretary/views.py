import logging
import functools
from datetime import date, datetime, timedelta

from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, get_language
from django.views.decorators.http import require_POST

from appointments.models import Appointment, AppointmentType
from patients.models import ClinicPatient, PatientProfile, StaffNote
from secretary.timefmt import format_clock
from accounts.ratelimit import client_ip, export_rate_limited
from accounts.validators import name_has_disallowed_chars, NAME_DISALLOWED_MESSAGE
from clinics.audit import log_activity
from clinics.models import ActivityLog

User = get_user_model()

logger = logging.getLogger(__name__)


def _is_safe_next(request, url):
    """True if *url* is a same-host, in-app redirect target.

    Guards every ``?next=`` / POST ``next`` / Referer redirect in this portal
    against open redirects. A bare ``url.startswith("/")`` check is NOT enough:
    ``//evil.com`` and ``/\\evil.com`` both start with ``/`` yet send the browser
    off-site (protocol-relative). ``url_has_allowed_host_and_scheme`` rejects all
    of those against the request host."""
    return bool(url) and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def _clock(request, value):
    """Format a time/datetime honoring the requesting user's 24h/12h preference."""
    use_12h = getattr(request.user, "time_format", "24") == "12"
    return format_clock(value, use_12h, getattr(request, "LANGUAGE_CODE", "ar"))


def _sweep_clinic_no_shows(clinic):
    """Persist overdue no-shows for this clinic before listing/aggregating,
    so every status badge, filter, and report stays accurate without the cron."""
    from compliance.services.compliance_service import apply_due_no_shows
    apply_due_no_shows(Appointment.objects.filter(clinic=clinic))


def _int_or_none(value):
    """Coerce a query-param to int, or None when blank/non-numeric.

    Guards `.filter(doctor_id=...)` against a 500 on garbage input (e.g.
    ?doctor_id=abc) — an unparseable value simply means "no doctor filter".
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_report_range(request, default_from, default_to):
    """Parse a report's date_from/date_to query params with safe bounds.

    Returns ``(date_from, date_to, clamped)``:
    - missing/invalid values fall back to the supplied defaults;
    - a reversed range (from > to) resets to the defaults;
    - a window wider than ``settings.REPORT_MAX_RANGE_DAYS`` is narrowed to
      ``[date_to - max, date_to]`` so a single request can never scan — or
      export — unbounded PHI history. ``clamped`` is True when that narrowing
      happened, so the view/template can surface a notice.
    """
    try:
        date_from = date.fromisoformat(request.GET.get("date_from", ""))
    except ValueError:
        date_from = default_from
    try:
        date_to = date.fromisoformat(request.GET.get("date_to", ""))
    except ValueError:
        date_to = default_to

    # Reversed range → fall back to defaults rather than return empty/odd results.
    if date_from > date_to:
        date_from, date_to = default_from, default_to

    max_days = getattr(settings, "REPORT_MAX_RANGE_DAYS", 366)
    clamped = False
    if (date_to - date_from).days > max_days:
        date_from = date_to - timedelta(days=max_days)
        clamped = True
    return date_from, date_to, clamped


def _export_blocked_response(request):
    """Return a 429 response if this secretary has tripped the bulk-export rate
    cap, else None. Keeps the report views' CSV-export branches DRY and ensures
    every export path is throttled identically.
    """
    if export_rate_limited(request.user.pk):
        return HttpResponse(
            _("لقد تجاوزت الحد المسموح به لعمليات التصدير. حاول مرة أخرى بعد قليل."),
            status=429,
            content_type="text/plain; charset=utf-8",
        )
    return None


def _require_secretary(request):
    """Return the secretary's ClinicStaff record, or None if not a secretary."""
    from clinics.models import ClinicStaff
    return ClinicStaff.objects.filter(
        user=request.user, role="SECRETARY", is_active=True
    ).select_related("clinic").first()


def secretary_required(view=None, *, as_json=False):
    """Gate a view behind an authenticated, active SECRETARY ClinicStaff post.

    Enforces BOTH authentication (via login_required) and role membership (an
    active ClinicStaff row with role="SECRETARY", resolved by _require_secretary).
    Membership is keyed on that staff row, NOT on user.role/user.roles, so
    multi-role users (e.g. DOCTOR+SECRETARY, or a PATIENT promoted to SECRETARY)
    are handled correctly. The resolved staff row is injected as the view's
    second positional argument.

    Anonymous users get login_required's redirect; authenticated non-secretaries
    get 403 (HTML by default, or JSON when as_json=True for the calendar feeds).
    """
    def decorator(fn):
        @functools.wraps(fn)
        def _inner(request, *args, **kwargs):
            staff = _require_secretary(request)
            if not staff:
                if as_json:
                    return JsonResponse({"error": "Forbidden"}, status=403)
                return HttpResponseForbidden(_("هذه الصفحة متاحة للسكرتارية فقط."))
            return fn(request, staff, *args, **kwargs)
        return login_required(_inner)
    return decorator(view) if view else decorator


def _secretary_visible_note(clinic, patient_id):
    """The single clinical note a secretary may read/print for this patient, or None.

    Rule: the secretary may only ever see the *latest* note in their clinic, and
    only when the authoring doctor flagged it as secretary-visible. Because we
    always resolve the newest note first, the "latest" condition is structural —
    an older flagged note can never be returned once a newer one exists.
    """
    from patients.models import ClinicalNote
    note = (
        ClinicalNote.objects.filter(patient_id=patient_id, clinic=clinic)
        .select_related("doctor", "clinic")
        .order_by("-created_at")
        .first()
    )
    if note and note.is_secretary_allowed:
        return note
    return None


def _today_filter_counts(clinic, all_rows=None):
    """
    Counts shown on the filter pills. Derived from the "all" row set so the numbers
    stay consistent with what's actually rendered (e.g. slots that overlap a CANCELLED
    appointment are suppressed in the All view, so they don't get double-counted).
    Pass `all_rows` if already computed; otherwise this builds it.
    """
    if all_rows is None:
        all_rows = _build_today_rows(clinic, "all")
    confirmed = sum(
        1 for r in all_rows
        if r["kind"] == "appointment" and r["appointment"].status == Appointment.Status.CONFIRMED
    )
    available = sum(1 for r in all_rows if r["kind"] == "slot")
    return {"all": len(all_rows), "confirmed": confirmed, "available": available}


def _build_today_rows(clinic, filter_type):
    """
    Unified row list for the dashboard "Today's Appointments" table.

    filter_type: "all" | "confirmed" | "available"
    Returns list of dicts: {"kind": "appointment"|"slot", "time", "appointment", "doctor"}.

    Note: walk-ins (is_walk_in=True, status=CHECKED_IN) don't reserve slots —
    generate_slots_for_date only treats CONFIRMED/COMPLETED as blocking.
    """
    from doctors.services import generate_slots_for_date
    from clinics.models import ClinicStaff

    today = date.today()
    rows = []

    # ── Appointment rows ────────────────────────────────────────────
    if filter_type in ("all", "confirmed"):
        qs = (
            Appointment.objects.filter(clinic=clinic, appointment_date=today)
            .select_related("patient", "doctor", "appointment_type")
            .order_by("appointment_time")
        )
        if filter_type == "confirmed":
            qs = qs.filter(status=Appointment.Status.CONFIRMED)
        for appt in qs:
            rows.append({
                "kind": "appointment",
                "time": appt.appointment_time,
                "appointment": appt,
                "doctor": appt.doctor,
            })

    # ── Slot rows ──────────────────────────────────────────────────
    if filter_type in ("all", "available"):
        smallest = (
            AppointmentType.objects.filter(clinic=clinic, is_active=True)
            .order_by("duration_minutes")
            .values_list("duration_minutes", flat=True)
            .first()
        )
        if smallest:
            doctor_staff = ClinicStaff.objects.filter(
                clinic=clinic, role="DOCTOR", is_active=True
            ).select_related("user")
            booked_keys = {(r["doctor"].id, r["time"]) for r in rows if r["kind"] == "appointment"}
            for staff in doctor_staff:
                doctor = staff.user
                slots = generate_slots_for_date(
                    doctor_id=doctor.id,
                    clinic_id=clinic.id,
                    target_date=today,
                    duration_minutes=smallest,
                )
                for slot in slots:
                    if not slot["is_available"] or slot["is_past"]:
                        continue
                    if (doctor.id, slot["time"]) in booked_keys:
                        continue  # defensive: don't duplicate a booked slot
                    rows.append({
                        "kind": "slot",
                        "time": slot["time"],
                        "appointment": None,
                        "doctor": doctor,
                    })

    # Sort: by time ascending; appointments before slots at the same minute; then doctor name.
    rows.sort(key=lambda r: (
        r["time"],
        0 if r["kind"] == "appointment" else 1,
        (r["doctor"].name if r["doctor"] else "") or "",
    ))
    return rows


def _get_doctor_statuses(clinic):
    """
    Build a list of dicts describing each doctor's current status for this clinic.
    Status can be: 'available', 'with_patient', 'off', 'not_scheduled'.
    """
    from clinics.models import ClinicStaff
    today = date.today()
    today_weekday = today.weekday()  # 0=Monday … 6=Sunday (matches DoctorAvailability.day_of_week)

    staff_qs = ClinicStaff.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")

    result = []
    from doctors.models import DoctorAvailability

    for staff in staff_qs:
        doctor = staff.user

        # Check if IN_PROGRESS appointment exists (with patient right now)
        in_progress = Appointment.objects.filter(
            clinic=clinic, doctor=doctor, status=Appointment.Status.IN_PROGRESS
        ).select_related("patient").first()

        if in_progress:
            status = "with_patient"
            status_label = _("مع مريض")
        else:
            # Check regular schedule
            try:
                scheduled = DoctorAvailability.objects.filter(
                    doctor=doctor, clinic=clinic, day_of_week=today_weekday
                ).exists()
            except Exception:
                scheduled = False

            if scheduled:
                status = "available"
                status_label = _("متاح")
            else:
                status = "not_scheduled"
                status_label = _("غير مجدول")

        today_count = Appointment.objects.filter(
            clinic=clinic, doctor=doctor, appointment_date=today
        ).exclude(status=Appointment.Status.CANCELLED).count()

        result.append({
            "doctor": doctor,
            "status": status,
            "status_label": status_label,
            "today_count": today_count,
            "in_progress_patient": in_progress.patient if in_progress else None,
        })

    return result


@secretary_required
def dashboard(request, staff):
    """Secretary daily overview: today's appointments, stats, and live status panels."""

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    from compliance.services.compliance_service import count_blocked_patients
    blocked_count = count_blocked_patients(clinic)
    today = date.today()

    todays_appointments = (
        Appointment.objects.filter(clinic=clinic, appointment_date=today)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_time")
    )

    # Per-status counts for today (include ALL statuses in todays_appointments)
    all_today = list(todays_appointments)
    stat_total = len(all_today)
    stat_pending = sum(1 for a in all_today if a.status == Appointment.Status.PENDING)
    stat_checked_in = sum(1 for a in all_today if a.status == Appointment.Status.CHECKED_IN)
    stat_in_progress = sum(1 for a in all_today if a.status == Appointment.Status.IN_PROGRESS)
    stat_completed = sum(1 for a in all_today if a.status == Appointment.Status.COMPLETED)
    stat_cancelled = sum(1 for a in all_today if a.status == Appointment.Status.CANCELLED)

    # Waiting room count (checked-in appointments)
    waiting_count = stat_checked_in + stat_in_progress

    # Upcoming in next 2 hours (confirmed/pending only)
    now = timezone.localtime()
    cutoff = now + timedelta(hours=2)
    upcoming_2h = [
        a for a in all_today
        if a.status in (Appointment.Status.CONFIRMED, Appointment.Status.PENDING)
        and a.appointment_time is not None
        and now.time() <= a.appointment_time <= cutoff.time()
    ]

    # Recent activity (appointment notifications for this clinic)
    recent_activity = []
    try:
        from appointments.models import AppointmentNotification
        recent_activity = list(
            AppointmentNotification.objects.filter(
                patient=staff.user,
                context_role="SECRETARY",
                appointment__clinic=clinic,
            )
            .select_related("appointment")
            .order_by("-created_at")[:8]
        )
    except Exception:
        pass

    # Unread notification count for sidebar badge
    unread_secretary_notification_count = 0
    try:
        from appointments.models import AppointmentNotification
        unread_secretary_notification_count = AppointmentNotification.objects.filter(
            appointment__clinic=clinic,
            context_role="SECRETARY",
            is_read=False,
        ).count()
    except Exception:
        pass  # AppointmentNotification may not have context_role on older schemas

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]

    current_filter = request.GET.get("filter", "all")
    if current_filter not in ("all", "confirmed", "available"):
        current_filter = "all"
    if current_filter == "all":
        rows = _build_today_rows(clinic, "all")
        counts = _today_filter_counts(clinic, all_rows=rows)
    else:
        rows = _build_today_rows(clinic, current_filter)
        counts = _today_filter_counts(clinic)

    return render(request, "secretary/dashboard.html", {
        "clinic": clinic,
        "todays_appointments": todays_appointments,
        "rows": rows,
        "current_filter": current_filter,
        "count_all": counts["all"],
        "count_confirmed": counts["confirmed"],
        "count_available": counts["available"],
        "today": today,
        "stat_total": stat_total,
        "stat_pending": stat_pending,
        "stat_checked_in": stat_checked_in,
        "stat_in_progress": stat_in_progress,
        "stat_completed": stat_completed,
        "stat_cancelled": stat_cancelled,
        "waiting_count": waiting_count,
        "upcoming_2h": upcoming_2h,
        "recent_activity": recent_activity,
        "terminal_statuses": terminal_statuses,
        "unread_secretary_notification_count": unread_secretary_notification_count,
        "blocked_count": blocked_count,
    })


@secretary_required
def doctor_status_htmx(request, staff):
    """HTMX endpoint: returns the doctor status cards partial (auto-refreshes every 60s)."""

    doctor_statuses = _get_doctor_statuses(staff.clinic)
    return render(request, "secretary/htmx/doctor_status_cards.html", {
        "doctor_statuses": doctor_statuses,
    })


@secretary_required
def todays_appointments_htmx(request, staff):
    """HTMX endpoint: returns the filtered today's-appointments table body partial."""

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    current_filter = request.GET.get("filter", "all")
    if current_filter not in ("all", "confirmed", "available"):
        current_filter = "all"

    if current_filter == "all":
        rows = _build_today_rows(clinic, "all")
        counts = _today_filter_counts(clinic, all_rows=rows)
    else:
        rows = _build_today_rows(clinic, current_filter)
        counts = _today_filter_counts(clinic)
    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]
    return render(request, "secretary/htmx/todays_appointments_body.html", {
        "rows": rows,
        "current_filter": current_filter,
        "count_all": counts["all"],
        "count_confirmed": counts["confirmed"],
        "count_available": counts["available"],
        "today": date.today(),
        "terminal_statuses": terminal_statuses,
        "is_htmx": True,
    })


@secretary_required
@require_POST
def checkin_appointment(request, staff, appointment_id):
    """Mark a CONFIRMED appointment as CHECKED_IN and set checked_in_at timestamp."""

    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)

    if appointment.status == Appointment.Status.CONFIRMED:
        from secretary.services import _next_queue_priority
        appointment.status = Appointment.Status.CHECKED_IN
        appointment.checked_in_at = timezone.now()
        appointment.queue_priority = _next_queue_priority(staff.clinic.id, date.today())
        appointment.save(update_fields=["status", "checked_in_at", "queue_priority", "updated_at"])
        log_activity(
            actor=request.user,
            clinic=staff.clinic,
            action=ActivityLog.Action.APPOINTMENT_STATUS_CHANGED,
            target=appointment,
            request=request,
            metadata={"from": "CONFIRMED", "to": "CHECKED_IN"},
        )
        messages.success(request, _("تم تسجيل وصول %(name)s بنجاح.") % {"name": appointment.patient.name})
    else:
        messages.warning(request, _("لا يمكن تسجيل الوصول إلا للمواعيد المؤكدة."))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "secretary:dashboard"
    if _is_safe_next(request, next_url):
        return redirect(next_url)
    return redirect("secretary:dashboard")


@secretary_required
def appointment_overview(request, staff, appointment_id):
    """Patient-scoped overview of a single appointment for the secretary.

    Reached from the secretary notification center "view appointment" link. Shows the
    patient's details, the notification's appointment highlighted (with its submitted
    intake form and status-aware action controls), and a timeline of the patient's
    other appointments in this clinic — with ANY doctor — split into upcoming + past,
    each card showing the booking doctor so the secretary can tell them apart inline.
    """

    clinic = staff.clinic
    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "patient", "doctor", "appointment_type", "patient__patient_profile"
        ).prefetch_related("answers__question", "attachments"),
        id=appointment_id, clinic=clinic,
    )

    patient = appointment.patient

    from doctors.views import build_appointment_intake_data
    intake_data = build_appointment_intake_data(appointment)

    # The patient's other appointments in this clinic (any doctor), split upcoming/past.
    today = date.today()
    active_statuses = [
        Appointment.Status.PENDING,
        Appointment.Status.CONFIRMED,
        Appointment.Status.CHECKED_IN,
        Appointment.Status.IN_PROGRESS,
    ]
    other_appts = (
        Appointment.objects.filter(clinic=clinic, patient=patient)
        .exclude(id=appointment_id)
        .select_related("doctor", "appointment_type")
    )
    upcoming = list(
        other_appts.filter(
            appointment_date__gte=today, status__in=active_statuses
        ).order_by("appointment_date", "appointment_time")
    )
    upcoming_ids = {a.id for a in upcoming}
    past = list(
        other_appts.exclude(id__in=upcoming_ids).order_by(
            "-appointment_date", "-appointment_time"
        )
    )

    # Patient age.
    profile = getattr(patient, "patient_profile", None)
    age = None
    if profile and profile.date_of_birth:
        dob = profile.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    # Action-control context (mirrors the notification modal this page replaces).
    clinic_patient = ClinicPatient.objects.filter(
        clinic=clinic, patient=patient
    ).first()
    is_new_patient_request = (
        appointment.status == Appointment.Status.PENDING and clinic_patient is None
    )

    # Context-aware back navigation: return the secretary to wherever they opened
    # the overview from (defaults to the notification center, since notification
    # links carry no return_to).
    _back_map = {
        "appointments": reverse("secretary:appointments"),
        "schedule": reverse("secretary:doctor_schedule"),
        "dashboard": reverse("secretary:dashboard"),
        "calendar": reverse("secretary:calendar") + "?restore=1",
        "waiting_room": reverse("secretary:waiting_room"),
        "patient": reverse("secretary:patient_detail", args=[patient.id]),
        "notifications": reverse("appointments:secretary_notifications"),
    }
    back_url = _back_map.get(
        request.GET.get("return_to", ""),
        reverse("appointments:secretary_notifications"),
    )

    # Authored staff notes — secretaries see doctor + secretary-only audiences, but never
    # a doctor's private notes.
    appointment_notes = list(
        StaffNote.objects.filter(appointment=appointment)
        .exclude(audience=StaffNote.Audience.DOCTOR_PRIVATE)
        .select_related("author").order_by("-created_at")
    )
    patient_notes = list(
        StaffNote.objects.filter(
            clinic=clinic, patient=patient, appointment__isnull=True
        ).exclude(audience=StaffNote.Audience.DOCTOR_PRIVATE)
        .select_related("author").order_by("-created_at")
    )

    # Billing: outstanding debt + any open session for this appointment.
    from secretary import billing
    patient_debt = billing.patient_debt(clinic, patient)
    open_invoice = billing.get_open_invoice(appointment)

    return render(request, "secretary/appointment_overview.html", {
        "appointment": appointment,
        "patient": patient,
        "profile": profile,
        "age": age,
        "intake_data": intake_data,
        "upcoming": upcoming,
        "past": past,
        "clinic_patient": clinic_patient,
        "is_new_patient_request": is_new_patient_request,
        "back_url": back_url,
        "appointment_notes": appointment_notes,
        "patient_notes": patient_notes,
        "patient_debt": patient_debt,
        "open_invoice": open_invoice,
    })


@secretary_required
def appointment_intake_partial(request, staff, appointment_id):
    """HTMX endpoint: render an appointment's submitted intake form (inline expander)
    on the secretary appointment-overview timeline. Clinic-scoped."""

    appointment = get_object_or_404(
        Appointment, id=appointment_id, clinic=staff.clinic
    )
    from doctors.views import build_appointment_intake_data
    intake_data = build_appointment_intake_data(appointment)
    return render(request, "doctors/partials/_appointment_intake_panel.html", {
        "intake_data": intake_data,
    })


@secretary_required
@require_POST
def register_new_patient_only(request, staff, appointment_id):
    """Register an unregistered patient in the clinic AND cancel this booking.

    Used when the secretary wants to keep the patient on file for future visits
    but reject the specific slot they requested. Atomic: both happen or neither.
    """

    from django.db import transaction
    from secretary.services import transition_appointment_status
    from appointments.services.booking_service import BookingError

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=clinic)

    already_registered = ClinicPatient.objects.filter(
        clinic=clinic, patient=appointment.patient
    ).exists()
    next_url = request.POST.get("next") or ""
    redirect_to = next_url if _is_safe_next(request, next_url) else "secretary:appointments"

    if appointment.status != Appointment.Status.PENDING or already_registered:
        messages.error(request, _("لم يعد هذا الطلب بحاجة إلى مراجعة."))
        return redirect(redirect_to)

    reason = request.POST.get("cancellation_reason", "").strip() or _(
        "تم تسجيل المريض دون تأكيد هذا الموعد"
    )
    try:
        with transaction.atomic():
            ClinicPatient.objects.get_or_create(
                clinic=clinic,
                patient=appointment.patient,
                defaults={
                    "registered_by": request.user,
                    "file_number": _generate_file_number(clinic),
                },
            )
            transition_appointment_status(
                appointment,
                Appointment.Status.CANCELLED,
                cancellation_reason=reason,
                actor=request.user,
                ip=client_ip(request),
            )
        messages.success(request, _("تم تسجيل المريض وإلغاء طلب الموعد."))
    except BookingError as e:
        messages.error(request, e.message)
    return redirect(redirect_to)


def _filter_confirmed_by_query(qs, q: str):
    """
    Apply a free-text filter to the Column A (CONFIRMED) queryset.
    Matches patient name (partial), patient phone, and appointment time.
    Doctor is filtered separately via the doctor_id dropdown.
    """
    q = (q or "").strip()
    if not q:
        return qs

    filt = (
        Q(patient__name__icontains=q)
        | Q(patient__phone__icontains=q)
    )
    digits = q.replace(":", "")
    if digits.isdigit():
        try:
            if len(digits) <= 2:
                filt |= Q(appointment_time__hour=int(digits))
            else:
                hh = int(digits[:2])
                mm = int(digits[2:4].ljust(2, "0"))
                filt |= Q(appointment_time__hour=hh, appointment_time__minute=mm)
        except ValueError:
            pass

    return qs.filter(filt)


# ── Stub views for unimplemented modules ─────────────────────────────────────

@secretary_required
def waiting_room(request, staff):
    """Secretary waiting room board — two-column live queue management."""

    from clinics.models import ClinicStaff as CS
    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()

    doctor_filter = request.GET.get("doctor_id", "")
    confirmed_q = request.GET.get("q", "")

    # Column A: CONFIRMED today (checked-in queue candidates)
    confirmed_qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.CONFIRMED,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_time")
    )

    # Column B: CHECKED_IN today (actual waiting queue)
    checkedin_qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.CHECKED_IN,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by(F("queue_priority").asc(nulls_last=True), "checked_in_at")
    )

    # Column C: IN_PROGRESS today (with the doctor — out of the queue, still billable)
    inprogress_qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.IN_PROGRESS,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("checked_in_at")
    )

    if doctor_filter:
        confirmed_qs = confirmed_qs.filter(doctor_id=doctor_filter)
        checkedin_qs = checkedin_qs.filter(doctor_id=doctor_filter)
        inprogress_qs = inprogress_qs.filter(doctor_id=doctor_filter)

    confirmed_qs = _filter_confirmed_by_query(confirmed_qs, confirmed_q)

    now = timezone.now()
    # Annotate wait time in minutes onto each checked-in appointment
    checkedin_list = []
    for i, appt in enumerate(checkedin_qs, start=1):
        wait_minutes = int((now - appt.checked_in_at).total_seconds() / 60) if appt.checked_in_at else 0
        checkedin_list.append({
            "appt": appt,
            "queue_pos": i,
            "wait_minutes": wait_minutes,
            "wait_class": (
                "text-red-600 dark:text-red-400" if wait_minutes >= 30
                else "text-amber-500 dark:text-amber-400" if wait_minutes >= 15
                else "text-emerald-600 dark:text-emerald-400"
            ),
            "row_bg": (
                "bg-red-50 dark:bg-red-900/10" if wait_minutes >= 30
                else "bg-amber-50 dark:bg-amber-900/10" if wait_minutes >= 15
                else ""
            ),
        })

    # Today's doctors for filter dropdown
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # Stats
    total_waiting = checkedin_qs.count()
    avg_wait = (
        sum(e["wait_minutes"] for e in checkedin_list) // max(len(checkedin_list), 1)
        if checkedin_list else 0
    )

    # Patients with the doctor (IN_PROGRESS) — out of the queue but still on the
    # board so billing can continue and the visit can be closed out.
    inprogress_list = [{"appt": appt} for appt in inprogress_qs]

    # Billing: per-patient debt badge + the open invoice (if any) for each
    # checked-in / in-progress row, so the board can show "بدء الفوترة" vs "عرض الفاتورة".
    from secretary import billing
    confirmed_list = list(confirmed_qs)
    debt_map = billing.debt_map(
        clinic,
        [a.patient_id for a in confirmed_list]
        + [e["appt"].patient_id for e in checkedin_list]
        + [e["appt"].patient_id for e in inprogress_list],
    )
    inv_map = billing.open_invoice_map(
        clinic,
        [e["appt"].id for e in checkedin_list] + [e["appt"].id for e in inprogress_list],
    )
    for e in checkedin_list:
        e["open_invoice"] = inv_map.get(e["appt"].id)
        e["debt"] = debt_map.get(e["appt"].patient_id)
    for e in inprogress_list:
        inv = inv_map.get(e["appt"].id)
        e["open_invoice"] = inv
        e["balance"] = inv.balance_due if inv else None
        e["debt"] = debt_map.get(e["appt"].patient_id)

    # Notes: set ``appt.notes_count`` on every card so the board can show a
    # "has notes" reminder badge (profile + appointment + legacy notes).
    from secretary import notes_utils
    notes_utils.annotate_notes_count(
        confirmed_list
        + [e["appt"] for e in checkedin_list]
        + [e["appt"] for e in inprogress_list],
        clinic,
    )

    return render(request, "secretary/waiting_room/board.html", {
        "clinic": clinic,
        "today": today,
        "confirmed_list": confirmed_list,
        "checkedin_list": checkedin_list,
        "inprogress_list": inprogress_list,
        "doctors": doctors,
        "doctor_filter": doctor_filter,
        "confirmed_q": confirmed_q,
        "total_waiting": total_waiting,
        "total_in_progress": len(inprogress_list),
        "avg_wait": avg_wait,
        "debt_map": debt_map,
    })


def waiting_room_display(request):
    """
    TV/kiosk display mode — no auth required so it can run on a lobby screen.
    Shows CHECKED_IN and IN_PROGRESS appointments for today.
    Auto-refreshes via <meta http-equiv='refresh' content='20'>.
    """
    from clinics.models import Clinic as ClinicModel
    from django.core.exceptions import ValidationError

    # Display-screen language is independent of the secretary's system language.
    # Controlled by ?lang= param so the TV/kiosk can show Arabic/English without
    # affecting whoever is logged in on the secretary workstation. Resolved up
    # front so the error responses below honour it too.
    display_lang = request.GET.get("lang", "ar")
    if display_lang not in ("ar", "en"):
        display_lang = "ar"

    # Public, unauthenticated kiosk screen. The clinic is addressed by an
    # unguessable per-clinic display_token (NOT the sequential PK), so the live
    # queue of arbitrary clinics can't be harvested by walking integer ids.
    token = request.GET.get("token", "").strip()
    if not token:
        return HttpResponse(
            "رابط الشاشة غير صالح. افتح شاشة العرض من لوحة غرفة الانتظار."
            if display_lang == "ar"
            else "Invalid screen link. Open the display screen from the waiting-room board.",
            status=400,
        )

    try:
        clinic = ClinicModel.objects.get(display_token=token, is_active=True)
    except (ClinicModel.DoesNotExist, ValidationError, ValueError):
        # Generic response — never reveal whether a token or clinic exists.
        return HttpResponse(
            "الشاشة غير متاحة." if display_lang == "ar" else "Screen unavailable.",
            status=404,
        )

    today = date.today()
    now = timezone.now()

    queue = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status__in=[Appointment.Status.CHECKED_IN, Appointment.Status.IN_PROGRESS],
        )
        .select_related("patient", "doctor")
        .order_by(F("queue_priority").asc(nulls_last=True), "checked_in_at")
    )

    queue_entries = []
    for i, appt in enumerate(queue, start=1):
        wait_minutes = (
            int((now - appt.checked_in_at).total_seconds() / 60)
            if appt.checked_in_at else 0
        )
        # Privacy: first name + initial of second word
        name_parts = appt.patient.name.strip().split()
        if len(name_parts) >= 2:
            display_name = f"{name_parts[0]} {name_parts[1][0]}"
        else:
            display_name = name_parts[0] if name_parts else "—"

        queue_entries.append({
            "queue_pos": i,
            "display_name": display_name,
            "doctor_name": appt.doctor.name if appt.doctor else "—",
            "status": appt.status,
            "is_in_progress": appt.status == Appointment.Status.IN_PROGRESS,
            "wait_minutes": wait_minutes,
        })

    response = render(request, "secretary/waiting_room/display.html", {
        "clinic": clinic,
        "queue_entries": queue_entries,
        "today": today,
        "now": now,
        "display_lang": display_lang,
        "display_is_rtl": display_lang == "ar",
        "display_dir": "rtl" if display_lang == "ar" else "ltr",
    })
    # Public PII surface (patient first-names, token-addressed): keep it out of
    # search indexes and shared caches, and never leak the URL token via Referer.
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


@secretary_required
def waiting_room_confirmed_htmx(request, staff):
    """HTMX polling endpoint — refreshes the CONFIRMED column every 30s."""

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()
    doctor_filter = request.GET.get("doctor_id", "")
    confirmed_q = request.GET.get("q", "")

    qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.CONFIRMED,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_time")
    )
    if doctor_filter:
        qs = qs.filter(doctor_id=doctor_filter)
    qs = _filter_confirmed_by_query(qs, confirmed_q)

    from secretary import notes_utils
    confirmed_list = notes_utils.annotate_notes_count(list(qs), clinic)

    return render(request, "secretary/htmx/waiting_room_confirmed_rows.html", {
        "confirmed_list": confirmed_list,
        "clinic": clinic,
    })


@secretary_required
def waiting_room_checkedin_htmx(request, staff):
    """HTMX polling endpoint — refreshes the CHECKED_IN column every 30s."""

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()
    doctor_filter = request.GET.get("doctor_id", "")
    now = timezone.now()

    qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.CHECKED_IN,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by(F("queue_priority").asc(nulls_last=True), "checked_in_at")
    )
    if doctor_filter:
        qs = qs.filter(doctor_id=doctor_filter)

    checkedin_list = []
    for i, appt in enumerate(qs, start=1):
        wait_minutes = int((now - appt.checked_in_at).total_seconds() / 60) if appt.checked_in_at else 0
        checkedin_list.append({
            "appt": appt,
            "queue_pos": i,
            "wait_minutes": wait_minutes,
            "wait_class": (
                "text-red-600 dark:text-red-400" if wait_minutes >= 30
                else "text-amber-500 dark:text-amber-400" if wait_minutes >= 15
                else "text-emerald-600 dark:text-emerald-400"
            ),
            "row_bg": (
                "bg-red-50 dark:bg-red-900/10" if wait_minutes >= 30
                else "bg-amber-50 dark:bg-amber-900/10" if wait_minutes >= 15
                else ""
            ),
        })

    # Billing: open invoice + outstanding-debt badge per checked-in patient.
    from secretary import billing
    inv_map = billing.open_invoice_map(clinic, [e["appt"].id for e in checkedin_list])
    debts = billing.debt_map(clinic, [e["appt"].patient_id for e in checkedin_list])
    for e in checkedin_list:
        e["open_invoice"] = inv_map.get(e["appt"].id)
        e["debt"] = debts.get(e["appt"].patient_id)

    from secretary import notes_utils
    notes_utils.annotate_notes_count([e["appt"] for e in checkedin_list], clinic)

    return render(request, "secretary/htmx/waiting_room_checkedin_rows.html", {
        "checkedin_list": checkedin_list,
        "clinic": clinic,
    })


@secretary_required
def waiting_room_inprogress_htmx(request, staff):
    """HTMX polling endpoint — refreshes the IN_PROGRESS ("with the doctor") column every 30s."""

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()
    doctor_filter = request.GET.get("doctor_id", "")

    qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.IN_PROGRESS,
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("checked_in_at")
    )
    if doctor_filter:
        qs = qs.filter(doctor_id=doctor_filter)

    inprogress_list = [{"appt": appt} for appt in qs]

    # Billing: open invoice (with balance) + outstanding-debt badge per patient.
    from secretary import billing
    inv_map = billing.open_invoice_map(clinic, [e["appt"].id for e in inprogress_list])
    debts = billing.debt_map(clinic, [e["appt"].patient_id for e in inprogress_list])
    for e in inprogress_list:
        inv = inv_map.get(e["appt"].id)
        e["open_invoice"] = inv
        e["balance"] = inv.balance_due if inv else None
        e["debt"] = debts.get(e["appt"].patient_id)

    from secretary import notes_utils
    notes_utils.annotate_notes_count([e["appt"] for e in inprogress_list], clinic)

    return render(request, "secretary/htmx/waiting_room_inprogress_rows.html", {
        "inprogress_list": inprogress_list,
        "clinic": clinic,
    })


@secretary_required
def waiting_room_notes_htmx(request, staff):
    """Read-only notes panel for a waiting-room card, loaded into the notes modal.

    Returns the secretary-visible notes for one appointment: patient-profile
    StaffNotes, appointment StaffNotes, and the legacy secretary_note/doctor_note
    text. No add/delete — managing notes happens on the appointment overview page.
    """

    clinic = staff.clinic
    appt = get_object_or_404(
        Appointment.objects.select_related("patient"),
        id=request.GET.get("appt"),
        clinic=clinic,
    )

    visible = [StaffNote.Audience.DOCTOR, StaffNote.Audience.SECRETARY]
    appointment_notes = list(
        StaffNote.objects.filter(appointment=appt, audience__in=visible)
        .select_related("author")
        .order_by("-created_at")
    )
    patient_notes = list(
        StaffNote.objects.filter(
            clinic=clinic,
            patient=appt.patient,
            appointment__isnull=True,
            audience__in=visible,
        )
        .select_related("author")
        .order_by("-created_at")
    )

    return render(request, "secretary/htmx/waiting_room_notes_panel.html", {
        "appt": appt,
        "patient_notes": patient_notes,
        "appointment_notes": appointment_notes,
    })


@secretary_required
def reorder_queue(request, staff):
    """POST — secretary drags to reorder the CHECKED_IN queue; persists new priorities."""
    if request.method != "POST":
        return HttpResponse(status=405)

    import json
    try:
        data = json.loads(request.body)
        order = [int(x) for x in data.get("order", [])]
    except (ValueError, TypeError, json.JSONDecodeError):
        return HttpResponse(status=400)

    if not order:
        return HttpResponse(status=200)

    today = date.today()
    clinic = staff.clinic

    valid_ids = set(
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status=Appointment.Status.CHECKED_IN,
            id__in=order,
        ).values_list("id", flat=True)
    )

    for priority, appt_id in enumerate(order, start=1):
        if appt_id in valid_ids:
            # Re-assert the clinic/date/status scope on the write itself, so the
            # update can never touch another clinic's appointment even if the
            # valid_ids guard above is ever weakened.
            Appointment.objects.filter(
                id=appt_id,
                clinic=clinic,
                appointment_date=today,
                status=Appointment.Status.CHECKED_IN,
            ).update(queue_priority=priority)

    return HttpResponse(status=200)


@secretary_required
def checkin_search(request, staff):
    """
    Dedicated check-in search page: secretary searches for a patient,
    sees today's appointments, and checks them in with one click.
    """

    clinic = staff.clinic
    today = date.today()

    search = request.GET.get("q", "").strip()
    found_patient = None
    today_appointments = []
    clinic_patient = None

    if search:
        from accounts.backends import PhoneNumberAuthBackend
        normalized = PhoneNumberAuthBackend.normalize_phone_number(search)
        found_patient = (
            User.objects.filter(
                Q(name__icontains=search)
                | Q(phone__icontains=normalized)
                | Q(clinic_registrations__file_number__iexact=search, clinic_registrations__clinic=clinic)
            )
            .filter(clinic_registrations__clinic=clinic)
            .distinct()
            .first()
        )

        if found_patient:
            clinic_patient = ClinicPatient.objects.filter(
                clinic=clinic, patient=found_patient
            ).first()
            today_appointments = (
                Appointment.objects.filter(
                    clinic=clinic,
                    patient=found_patient,
                    appointment_date=today,
                )
                .select_related("doctor", "appointment_type")
                .order_by("appointment_time")
            )

    return render(request, "secretary/waiting_room/checkin_search.html", {
        "clinic": clinic,
        "today": today,
        "search": search,
        "found_patient": found_patient,
        "clinic_patient": clinic_patient,
        "today_appointments": list(today_appointments),
    })


@secretary_required
def calendar_view(request, staff):
    """Calendar view — FullCalendar v6 with HTMX data feed."""

    from clinics.models import ClinicStaff as CS
    clinic = staff.clinic
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctor_users = [s.user for s in doctor_staff]

    # Calendar slot window — shared with appointments_json so shading aligns.
    start_h, end_h = _clinic_slot_bounds(clinic)
    slot_min_time = f"{start_h:02d}:00:00"
    slot_max_time = f"{end_h:02d}:00:00"

    return render(request, "secretary/appointments/calendar.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "slot_min_time": slot_min_time,
        "slot_max_time": slot_max_time,
        "status_legend": [
            ("قيد الانتظار", "PENDING", "#d97706"),
            ("مؤكد", "CONFIRMED", "#10b981"),
            ("وصل", "CHECKED_IN", "#3b82f6"),
            ("جارٍ", "IN_PROGRESS", "#8b5cf6"),
            ("مكتمل", "COMPLETED", "#6b7280"),
            ("ملغى", "CANCELLED", "#ef4444"),
        ],
    })


@secretary_required
def billing_invoices(request, staff):
    """Billing dashboard: clinic invoices with a status filter + patient search."""

    from secretary.models import Invoice
    from secretary import billing

    clinic = staff.clinic
    void_statuses = [Invoice.Status.CANCELLED, Invoice.Status.REFUNDED]
    flt = request.GET.get("filter", "all")
    q = request.GET.get("q", "").strip()

    invoices = (
        Invoice.objects.filter(clinic=clinic)
        .select_related("patient", "appointment")
        .order_by("-created_at")
    )
    if flt == "open":
        invoices = invoices.filter(status__in=[Invoice.Status.DRAFT, Invoice.Status.PARTIAL])
    elif flt == "unpaid":
        invoices = invoices.filter(balance_due__gt=0).exclude(status__in=void_statuses)
    elif flt == "paid":
        invoices = invoices.filter(status=Invoice.Status.PAID)
    if q:
        invoices = invoices.filter(
            Q(patient__name__icontains=q)
            | Q(patient__phone__icontains=q)
            | Q(invoice_number__icontains=q)
        )

    invoices = list(invoices[:200])

    # Finalized debt only (matches the debtors list below — open sessions excluded).
    totals = {"outstanding": billing.clinic_total_debt(clinic)}
    debtors_count = billing.patient_debtors(clinic).count()

    return render(request, "secretary/billing/dashboard.html", {
        "clinic": clinic,
        "invoices": invoices,
        "filter": flt,
        "q": q,
        "totals": totals,
        "debtors_count": debtors_count,
    })


@secretary_required
@require_POST
def start_billing(request, staff, appointment_id):
    """Open a billing session for a checked-in patient and go to the invoice."""

    from secretary import billing

    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "appointment_type", "clinic"),
        id=appointment_id, clinic=staff.clinic,
    )
    try:
        invoice = billing.open_billing_session(appointment, by_user=request.user, ip=client_ip(request))
    except billing.BillingError as e:
        messages.error(request, e.message)
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if _is_safe_next(request, next_url):
            return redirect(next_url)
        return redirect("secretary:waiting_room")
    return redirect("secretary:invoice_detail", invoice_id=invoice.id)


@secretary_required
def invoice_detail(request, staff, invoice_id):
    """The billing-session screen: line items, add-charge + payment forms, history."""

    from secretary.models import Invoice
    from secretary import billing
    from secretary.forms import ChargeForm, PaymentForm

    clinic = staff.clinic
    invoice = get_object_or_404(
        Invoice.objects.select_related("patient", "appointment", "created_by"),
        id=invoice_id, clinic=clinic,
    )
    max_payable = billing.patient_outstanding(clinic, invoice.patient)
    other_debt = billing.patient_debt(clinic, invoice.patient, exclude_invoice=invoice)

    return render(request, "secretary/billing/invoice_detail.html", {
        "clinic": clinic,
        "invoice": invoice,
        "items": list(invoice.items.all()),
        "payments": list(invoice.payments.select_related("received_by").all()),
        "editable": billing.is_editable(invoice),
        "max_payable": max_payable,
        "other_debt": other_debt,
        "charge_form": ChargeForm(),
        "payment_form": PaymentForm(max_payable=max_payable),
    })


@secretary_required
@require_POST
def invoice_add_charge(request, staff, invoice_id):
    """Add a charge (line item) to an open invoice."""

    from secretary.models import Invoice
    from secretary import billing
    from secretary.forms import ChargeForm

    invoice = get_object_or_404(Invoice, id=invoice_id, clinic=staff.clinic)
    form = ChargeForm(request.POST)
    if form.is_valid():
        try:
            billing.add_charge(
                invoice,
                description=form.cleaned_data["description"],
                quantity=form.cleaned_data["quantity"],
                unit_price=form.cleaned_data["unit_price"],
                actor=request.user,
                ip=client_ip(request),
            )
            messages.success(request, _("تمت إضافة الرسوم."))
        except billing.BillingError as e:
            messages.error(request, e.message)
    else:
        messages.error(request, _("بيانات الرسوم غير صالحة."))
    return redirect("secretary:invoice_detail", invoice_id=invoice.id)


@secretary_required
@require_POST
def invoice_remove_charge(request, staff, invoice_id, item_id):
    """Remove a charge from an open invoice."""

    from secretary.models import Invoice, InvoiceItem
    from secretary import billing

    invoice = get_object_or_404(Invoice, id=invoice_id, clinic=staff.clinic)
    item = get_object_or_404(InvoiceItem, id=item_id, invoice=invoice)
    try:
        billing.remove_charge(item, actor=request.user, ip=client_ip(request))
        messages.success(request, _("تم حذف الرسوم."))
    except billing.BillingError as e:
        messages.error(request, e.message)
    return redirect("secretary:invoice_detail", invoice_id=invoice.id)


@secretary_required
@require_POST
def invoice_delete(request, staff, invoice_id):
    """Permanently delete a draft invoice (no payments)."""

    from secretary.models import Invoice
    from secretary import billing

    invoice = get_object_or_404(Invoice, id=invoice_id, clinic=staff.clinic)
    number = invoice.invoice_number  # capture before delete for the message
    try:
        billing.delete_invoice(invoice, actor=request.user, ip=client_ip(request))
        messages.success(request, _("تم حذف الفاتورة %(n)s.") % {"n": number})
    except billing.BillingError as e:
        messages.error(request, e.message)
        return redirect("secretary:invoice_detail", invoice_id=invoice_id)

    # Return to where the delete was triggered (the filtered list), else the list.
    next_url = request.POST.get("next", "")
    if _is_safe_next(request, next_url) and f"/invoice/{invoice_id}/" not in next_url:
        return redirect(next_url)
    return redirect("secretary:billing_invoices")


@secretary_required
@require_POST
def invoice_record_payment(request, staff, invoice_id):
    """Record a payment against an invoice (overpayment-guarded, FIFO debt settle)."""

    from secretary.models import Invoice
    from secretary import billing
    from secretary.forms import PaymentForm

    invoice = get_object_or_404(Invoice, id=invoice_id, clinic=staff.clinic)
    max_payable = billing.patient_outstanding(staff.clinic, invoice.patient)
    form = PaymentForm(request.POST, max_payable=max_payable)
    if form.is_valid():
        try:
            billing.record_payment(
                primary_invoice=invoice,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data.get("reference", ""),
                breakdown=form.cleaned_data.get("breakdown", ""),
                by_user=request.user,
                ip=client_ip(request),
            )
            messages.success(request, _("تم تسجيل الدفعة بنجاح."))
        except billing.BillingError as e:
            messages.error(request, e.message)
    else:
        err = next(iter(form.errors.values()))[0] if form.errors else _("بيانات الدفعة غير صالحة.")
        messages.error(request, err)
    return redirect("secretary:invoice_detail", invoice_id=invoice.id)


@secretary_required
def patient_debts(request, staff):
    """Page listing every patient with an outstanding balance and the amount owed."""

    from decimal import Decimal
    from secretary import billing

    clinic = staff.clinic
    debtors = list(billing.patient_debtors(clinic))
    grand_total = sum((d["total_due"] for d in debtors), Decimal("0.00"))
    return render(request, "secretary/billing/debts.html", {
        "clinic": clinic,
        "debtors": debtors,
        "grand_total": grand_total,
    })


@secretary_required
def patient_debt_badge_htmx(request, staff):
    """HTMX: outstanding-debt warning banner for a selected patient (booking form)."""

    from secretary import billing

    amount = None
    patient_id = request.GET.get("patient_id")
    if patient_id:
        try:
            patient = User.objects.get(id=patient_id)
            amount = billing.patient_debt(staff.clinic, patient)
        except (User.DoesNotExist, ValueError):
            amount = None
    return render(request, "secretary/billing/_debt_banner.html", {"amount": amount})


# ──────────────────────────────────────────────────────────────────────────────
# Procurement (Purchase Requests)
# ──────────────────────────────────────────────────────────────────────────────


@secretary_required
def purchase_requests(request, staff):
    """List this clinic's purchase requests, filtered by status + time period and
    sorted by date or cost."""

    from secretary.models import PurchaseRequest
    from django.db.models import Count, Q

    clinic = staff.clinic
    status = request.GET.get("filter", "all")
    period = request.GET.get("period", "all")
    sort = request.GET.get("sort", "newest")

    # ── Time period (rolling windows) ─────────────────────────────────
    base_qs = PurchaseRequest.objects.filter(clinic=clinic)
    period_days = {"week": 7, "month": 30, "year": 365}
    if period in period_days:
        since = timezone.now() - timedelta(days=period_days[period])
        base_qs = base_qs.filter(created_at__gte=since)
    else:
        period = "all"

    # ── Status counts within the selected period (for the pill badges) ─
    agg = base_qs.aggregate(
        all=Count("id"),
        pending=Count("id", filter=Q(status=PurchaseRequest.Status.PENDING)),
        approved=Count("id", filter=Q(status=PurchaseRequest.Status.APPROVED)),
        rejected=Count("id", filter=Q(status=PurchaseRequest.Status.REJECTED)),
    )
    counts = {
        "all": agg["all"],
        "PENDING": agg["pending"],
        "APPROVED": agg["approved"],
        "REJECTED": agg["rejected"],
    }

    # ── Status filter ─────────────────────────────────────────────────
    requests_qs = base_qs.select_related("requested_by", "reviewed_by").prefetch_related("items")
    if status in PurchaseRequest.Status.values:
        requests_qs = requests_qs.filter(status=status)
    else:
        status = "all"

    # ── Sort (tie-break on newest for stable ordering) ────────────────
    sort_map = {"newest": "-created_at", "cost_high": "-total", "cost_low": "total"}
    if sort not in sort_map:
        sort = "newest"
    requests_qs = requests_qs.order_by(sort_map[sort], "-created_at")

    requests_list = list(requests_qs[:200])

    return render(request, "secretary/procurement/list.html", {
        "clinic": clinic,
        "requests": requests_list,
        "filter": status,
        "period": period,
        "sort": sort,
        "counts": counts,
    })


@secretary_required
def purchase_request_create(request, staff):
    """Create a new itemized purchase request (PENDING) and notify the owner."""

    from secretary.forms import PurchaseRequestForm
    from secretary import procurement

    clinic = staff.clinic

    if request.method == "POST":
        form = PurchaseRequestForm(request.POST)
        descriptions = request.POST.getlist("item_description")
        quantities = request.POST.getlist("item_quantity")
        unit_prices = request.POST.getlist("item_unit_price")
        items = [
            {"description": d, "quantity": q, "unit_price": p}
            for d, q, p in zip(descriptions, quantities, unit_prices)
        ]

        if form.is_valid():
            try:
                pr = procurement.create_purchase_request(
                    clinic=clinic,
                    user=request.user,
                    title=form.cleaned_data["title"],
                    category=form.cleaned_data["category"],
                    note=form.cleaned_data["note"],
                    items=items,
                )
            except procurement.ProcurementError as e:
                messages.error(request, e.message)
            else:
                from appointments.services.appointment_notification_service import (
                    notify_owner_purchase_request_submitted,
                )
                transaction.on_commit(
                    lambda: notify_owner_purchase_request_submitted(pr)
                )
                messages.success(
                    request,
                    _("تم إرسال طلب الشراء %(num)s إلى مالك العيادة للمراجعة.")
                    % {"num": pr.request_number},
                )
                return redirect("secretary:purchase_requests")
    else:
        form = PurchaseRequestForm()

    return render(request, "secretary/procurement/create.html", {
        "clinic": clinic,
        "form": form,
    })


@secretary_required
@require_POST
def purchase_request_delete(request, staff, request_id):
    """Delete a still-pending purchase request created within this clinic."""

    from secretary.models import PurchaseRequest

    pr = get_object_or_404(PurchaseRequest, id=request_id, clinic=staff.clinic)
    if not pr.is_editable:
        messages.error(request, _("لا يمكن حذف طلب تمت مراجعته."))
    else:
        pr.delete()
        messages.success(request, _("تم حذف طلب الشراء."))
    return redirect("secretary:purchase_requests")


@secretary_required
def reports_index(request, staff):
    """Reports hub — quick stats + links to each sub-report."""
    from django.db.models import Count


    clinic = staff.clinic
    today = date.today()
    month_start = today.replace(day=1)

    # Quick stats
    today_qs = Appointment.objects.filter(clinic=clinic, appointment_date=today)
    month_qs = Appointment.objects.filter(clinic=clinic, appointment_date__range=(month_start, today))

    stats = {
        "today_total": today_qs.count(),
        "today_completed": today_qs.filter(status=Appointment.Status.COMPLETED).count(),
        "today_noshows": today_qs.filter(status=Appointment.Status.NO_SHOW).count(),
        "month_total": month_qs.count(),
        "month_completed": month_qs.filter(status=Appointment.Status.COMPLETED).count(),
        "month_cancelled": month_qs.filter(status=Appointment.Status.CANCELLED).count(),
        "month_noshows": month_qs.filter(status=Appointment.Status.NO_SHOW).count(),
    }

    return render(request, "secretary/reports/index.html", {
        "clinic": clinic,
        "today": today,
        "stats": stats,
    })


@secretary_required
def report_daily(request, staff):
    """Daily appointments report. Supports ?export=csv."""
    from django.db.models import Count, Sum
    import csv as csv_module


    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()

    date_str = request.GET.get("date", today.isoformat())
    try:
        report_date = date.fromisoformat(date_str)
    except ValueError:
        report_date = today

    qs = (
        Appointment.objects.filter(clinic=clinic, appointment_date=report_date)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("appointment_time")
    )

    appointments = list(qs)
    total = len(appointments)

    # Status breakdown
    status_counts = {}
    for appt in appointments:
        status_counts[appt.status] = status_counts.get(appt.status, 0) + 1

    status_breakdown = []
    for status_val, label in Appointment.Status.choices:
        count = status_counts.get(status_val, 0)
        pct = round(count / total * 100) if total > 0 else 0
        status_breakdown.append({"status": status_val, "label": label, "count": count, "pct": pct})

    # Doctor breakdown
    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = {s.user.id: s.user for s in doctor_staff}

    doctor_stats = {}
    for appt in appointments:
        did = appt.doctor_id
        if did not in doctor_stats:
            doctor_stats[did] = {"doctor": appt.doctor, "total": 0, "completed": 0, "noshows": 0, "cancelled": 0}
        doctor_stats[did]["total"] += 1
        if appt.status == Appointment.Status.COMPLETED:
            doctor_stats[did]["completed"] += 1
        elif appt.status == Appointment.Status.NO_SHOW:
            doctor_stats[did]["noshows"] += 1
        elif appt.status == Appointment.Status.CANCELLED:
            doctor_stats[did]["cancelled"] += 1

    # CSV export
    if request.GET.get("export") == "csv":
        blocked = _export_blocked_response(request)
        if blocked:
            return blocked
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="daily_report_{report_date}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["الوقت", "المريض", "الطبيب", "الخدمة", "الحالة", "السعر"])
        for appt in appointments:
            writer.writerow([
                _clock(request, appt.appointment_time),
                appt.patient.name,
                appt.doctor.name if appt.doctor else "",
                appt.appointment_type.display_name if appt.appointment_type else "",
                appt.get_status_display(),
                str(appt.appointment_type.price) if appt.appointment_type else "",
            ])
        log_activity(
            actor=request.user, clinic=clinic,
            action=ActivityLog.Action.REPORT_EXPORTED,
            target=clinic, request=request,
            metadata={"report": "daily", "date": str(report_date),
                      "row_count": total},
        )
        return response

    return render(request, "secretary/reports/daily.html", {
        "clinic": clinic,
        "report_date": report_date,
        "today": today,
        "prev_date": report_date - timedelta(days=1),
        "next_date": report_date + timedelta(days=1),
        "appointments": appointments,
        "total": total,
        "status_breakdown": status_breakdown,
        "doctor_stats": list(doctor_stats.values()),
    })


@secretary_required
def report_visits(request, staff):
    """Patient visits report with date range + doctor filter. Supports ?export=csv."""
    from django.db.models import Count
    import csv as csv_module


    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()

    date_from, date_to, range_clamped = _parse_report_range(
        request, today - timedelta(days=29), today
    )
    doctor_filter = request.GET.get("doctor_id", "")
    doctor_id = _int_or_none(doctor_filter)

    qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date__range=(date_from, date_to),
            status__in=[
                Appointment.Status.COMPLETED,
                Appointment.Status.NO_SHOW,
                Appointment.Status.CANCELLED,
                Appointment.Status.CHECKED_IN,
                Appointment.Status.IN_PROGRESS,
            ],
        )
        .select_related("patient", "doctor", "appointment_type")
        .order_by("-appointment_date", "appointment_time")
    )
    if doctor_id is not None:
        qs = qs.filter(doctor_id=doctor_id)

    appointments = list(qs)

    # New vs returning: a patient is "new" if their first appointment in this clinic
    # falls within the date range.
    all_patient_ids = {a.patient_id for a in appointments}
    first_visits = {}
    if all_patient_ids:
        from django.db.models import Min
        first_appt_qs = (
            Appointment.objects.filter(clinic=clinic, patient_id__in=all_patient_ids)
            .values("patient_id")
            .annotate(first=Min("appointment_date"))
        )
        first_visits = {row["patient_id"]: row["first"] for row in first_appt_qs}

    new_patients = sum(
        1 for pid in all_patient_ids
        if first_visits.get(pid) and date_from <= first_visits[pid] <= date_to
    )
    returning_patients = len(all_patient_ids) - new_patients

    # Daily-volume series + average visits/day (operational trend, built from the
    # already-loaded list so it adds no extra queries).
    num_days = (date_to - date_from).days + 1
    avg_per_day = round(len(appointments) / num_days, 1) if num_days > 0 else 0
    day_counts = {date_from + timedelta(days=i): 0 for i in range(num_days)}
    for a in appointments:
        if a.appointment_date in day_counts:
            day_counts[a.appointment_date] += 1
    daily_series = [{"date": d, "count": c} for d, c in sorted(day_counts.items())]
    max_daily = max((row["count"] for row in daily_series), default=0) or 1
    # Keep the bar strip legible — hide it for very wide ranges.
    show_daily_chart = 1 < num_days <= 92

    # Doctors for filter dropdown
    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # CSV export
    if request.GET.get("export") == "csv":
        blocked = _export_blocked_response(request)
        if blocked:
            return blocked
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="visits_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["المريض", "تاريخ الزيارة", "الوقت", "الطبيب", "الخدمة", "الحالة"])
        for appt in appointments:
            writer.writerow([
                appt.patient.name,
                appt.appointment_date.strftime("%Y/%m/%d"),
                _clock(request, appt.appointment_time),
                appt.doctor.name if appt.doctor else "",
                appt.appointment_type.display_name if appt.appointment_type else "",
                appt.get_status_display(),
            ])
        log_activity(
            actor=request.user, clinic=clinic,
            action=ActivityLog.Action.REPORT_EXPORTED,
            target=clinic, request=request,
            metadata={"report": "visits", "date_from": str(date_from),
                      "date_to": str(date_to), "doctor_id": doctor_id,
                      "row_count": len(appointments)},
        )
        return response

    return render(request, "secretary/reports/visits.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "range_clamped": range_clamped,
        "doctor_filter": doctor_filter,
        "appointments": appointments,
        "total": len(appointments),
        "unique_patients": len(all_patient_ids),
        "new_patients": new_patients,
        "returning_patients": returning_patients,
        "doctors": doctors,
        "avg_per_day": avg_per_day,
        "daily_series": daily_series,
        "max_daily": max_daily,
        "show_daily_chart": show_daily_chart,
    })


@secretary_required
def report_noshows(request, staff):
    """No-show & cancellation report. Supports ?export=csv."""
    from django.db.models import Count
    import csv as csv_module


    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()

    date_from, date_to, range_clamped = _parse_report_range(
        request, today - timedelta(days=29), today
    )
    doctor_filter = request.GET.get("doctor_id", "")
    doctor_id = _int_or_none(doctor_filter)

    base_qs = Appointment.objects.filter(
        clinic=clinic,
        appointment_date__range=(date_from, date_to),
    )
    if doctor_id is not None:
        base_qs = base_qs.filter(doctor_id=doctor_id)

    total = base_qs.count()
    # Evaluate each queryset once and reuse the lists for every breakdown below.
    noshows = list(
        base_qs.filter(status=Appointment.Status.NO_SHOW)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("-appointment_date")
    )
    cancellations = list(
        base_qs.filter(status=Appointment.Status.CANCELLED)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("-appointment_date")
    )

    noshow_count = len(noshows)
    cancelled_count = len(cancellations)
    noshow_rate = round(noshow_count / total * 100, 1) if total > 0 else 0
    cancel_rate = round(cancelled_count / total * 100, 1) if total > 0 else 0

    # Top no-show patients
    top_noshows = (
        base_qs.filter(status=Appointment.Status.NO_SHOW)
        .values("patient__id", "patient__name", "patient__phone")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    # Day-of-week breakdown (0=Mon … 6=Sun). WEEKDAYS holds Django's own
    # translated weekday names, so they render correctly in Arabic and English.
    from django.utils.dates import WEEKDAYS
    dow_counts = {i: {"name": WEEKDAYS[i], "noshows": 0, "cancelled": 0} for i in range(7)}
    for appt in noshows:
        dow_counts[appt.appointment_date.weekday()]["noshows"] += 1
    for appt in cancellations:
        dow_counts[appt.appointment_date.weekday()]["cancelled"] += 1
    dow_breakdown = list(dow_counts.values())
    max_dow = max((d["noshows"] + d["cancelled"]) for d in dow_breakdown) or 1

    # No-shows & cancellations grouped by doctor.
    by_doctor = {}
    for appt in noshows:
        row = by_doctor.setdefault(appt.doctor_id, {"doctor": appt.doctor, "noshows": 0, "cancelled": 0})
        row["noshows"] += 1
    for appt in cancellations:
        row = by_doctor.setdefault(appt.doctor_id, {"doctor": appt.doctor, "noshows": 0, "cancelled": 0})
        row["cancelled"] += 1
    doctor_breakdown = sorted(
        by_doctor.values(), key=lambda r: r["noshows"] + r["cancelled"], reverse=True
    )
    max_doc_total = max((r["noshows"] + r["cancelled"]) for r in doctor_breakdown) if doctor_breakdown else 1

    # Doctors for filter
    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # CSV export
    if request.GET.get("export") == "csv":
        blocked = _export_blocked_response(request)
        if blocked:
            return blocked
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="noshows_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["النوع", "المريض", "الهاتف", "التاريخ", "الطبيب", "الخدمة", "السبب"])
        for appt in noshows:
            writer.writerow(["لم يحضر", appt.patient.name, appt.patient.phone,
                              appt.appointment_date.strftime("%Y/%m/%d"),
                              appt.doctor.name if appt.doctor else "",
                              appt.appointment_type.display_name if appt.appointment_type else "", ""])
        for appt in cancellations:
            writer.writerow(["ملغى", appt.patient.name, appt.patient.phone,
                              appt.appointment_date.strftime("%Y/%m/%d"),
                              appt.doctor.name if appt.doctor else "",
                              appt.appointment_type.display_name if appt.appointment_type else "",
                              appt.cancellation_reason])
        log_activity(
            actor=request.user, clinic=clinic,
            action=ActivityLog.Action.REPORT_EXPORTED,
            target=clinic, request=request,
            metadata={"report": "noshows", "date_from": str(date_from),
                      "date_to": str(date_to), "doctor_id": doctor_id,
                      "row_count": len(noshows) + len(cancellations)},
        )
        return response

    return render(request, "secretary/reports/noshows.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "range_clamped": range_clamped,
        "doctor_filter": doctor_filter,
        "doctors": doctors,
        "total": total,
        "noshow_count": noshow_count,
        "cancelled_count": cancelled_count,
        "noshow_rate": noshow_rate,
        "cancel_rate": cancel_rate,
        "noshows": noshows,
        "cancellations": cancellations,
        "top_noshows": top_noshows,
        "dow_breakdown": dow_breakdown,
        "max_dow": max_dow,
        "doctor_breakdown": doctor_breakdown,
        "max_doc_total": max_doc_total,
    })


@secretary_required
def report_doctors(request, staff):
    """Doctor utilization report. Supports ?export=csv."""
    from django.db.models import Count
    from doctors.models import DoctorAvailability
    import csv as csv_module


    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    today = date.today()
    month_start = today.replace(day=1)

    date_from, date_to, range_clamped = _parse_report_range(
        request, month_start, today
    )

    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # Working days in range per weekday
    num_days = (date_to - date_from).days + 1
    weekday_counts = {i: 0 for i in range(7)}
    for i in range(num_days):
        weekday_counts[(date_from + timedelta(days=i)).weekday()] += 1

    # Per-doctor stats
    doctor_rows = []
    for doctor in doctors:
        # Scheduled slots: sum of DoctorAvailability slots × working days
        avail_slots = DoctorAvailability.objects.filter(
            doctor=doctor, clinic=clinic, is_active=True
        )
        # Each availability slot represents one time block per weekday occurrence
        scheduled_sessions = sum(
            weekday_counts.get(slot.day_of_week, 0) for slot in avail_slots
        )

        # Appointment counts by status
        appt_qs = Appointment.objects.filter(
            clinic=clinic, doctor=doctor,
            appointment_date__range=(date_from, date_to),
        )
        counts_by_status = {
            row["status"]: row["count"]
            for row in appt_qs.values("status").annotate(count=Count("id"))
        }
        total_booked = sum(counts_by_status.values())
        completed = counts_by_status.get(Appointment.Status.COMPLETED, 0)
        noshows = counts_by_status.get(Appointment.Status.NO_SHOW, 0)
        cancelled = counts_by_status.get(Appointment.Status.CANCELLED, 0)

        utilization = round(total_booked / scheduled_sessions * 100) if scheduled_sessions > 0 else 0
        avg_daily = round(total_booked / max(num_days, 1), 1)
        completion_rate = round(completed / total_booked * 100) if total_booked > 0 else 0

        # Most common appointment type
        top_type = (
            appt_qs.filter(appointment_type__isnull=False)
            .values("appointment_type__name", "appointment_type__name_ar")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
            .first()
        )
        # Pick the service name for the active language (mirrors AppointmentType.display_name).
        if top_type:
            _name = top_type["appointment_type__name"]
            _name_ar = top_type["appointment_type__name_ar"]
            if (get_language() or "ar").startswith("ar"):
                top_type_name = _name_ar or _name
            else:
                top_type_name = _name or _name_ar
        else:
            top_type_name = "—"

        doctor_rows.append({
            "doctor": doctor,
            "scheduled_sessions": scheduled_sessions,
            "total_booked": total_booked,
            "completed": completed,
            "noshows": noshows,
            "cancelled": cancelled,
            "utilization": utilization,
            "completion_rate": completion_rate,
            "avg_daily": avg_daily,
            "top_type": top_type_name,
        })

    # Sort by total_booked desc for chart rendering
    doctor_rows.sort(key=lambda r: r["total_booked"], reverse=True)
    max_booked = max((r["total_booked"] for r in doctor_rows), default=1) or 1

    # CSV export
    if request.GET.get("export") == "csv":
        blocked = _export_blocked_response(request)
        if blocked:
            return blocked
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="doctors_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["الطبيب", "الجلسات المجدولة", "المواعيد المحجوزة",
                          "مكتملة", "لم يحضر", "ملغاة",
                          "نسبة الاستخدام %", "نسبة الإنجاز %", "متوسط يومي"])
        for row in doctor_rows:
            writer.writerow([
                row["doctor"].name, row["scheduled_sessions"], row["total_booked"],
                row["completed"], row["noshows"], row["cancelled"],
                row["utilization"], row["completion_rate"], row["avg_daily"],
            ])
        log_activity(
            actor=request.user, clinic=clinic,
            action=ActivityLog.Action.REPORT_EXPORTED,
            target=clinic, request=request,
            metadata={"report": "doctors", "date_from": str(date_from),
                      "date_to": str(date_to), "row_count": len(doctor_rows)},
        )
        return response

    return render(request, "secretary/reports/doctors.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "range_clamped": range_clamped,
        "doctor_rows": doctor_rows,
        "max_booked": max_booked,
    })


@secretary_required
def doctor_schedule(request, staff):
    """
    Weekly schedule view for all clinic doctors.
    Shows DoctorAvailability (recurring) and DoctorAvailabilityException (blocks) per doctor.
    Supports week navigation (prev/next) and doctor filter via GET params.
    Secretary can read the full schedule and add/remove blocks.
    """
    from clinics.models import ClinicStaff as CS, DoctorAvailabilityException
    from doctors.models import DoctorAvailability
    from django.db.models import Count


    clinic = staff.clinic
    today = date.today()

    # ── Week navigation ────────────────────────────────────────────────────
    week_start_str = request.GET.get("week")
    if week_start_str:
        try:
            week_start = date.fromisoformat(week_start_str)
            week_start = week_start - timedelta(days=week_start.weekday())
        except ValueError:
            week_start = today - timedelta(days=today.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=6)
    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()
    week_days = [week_start + timedelta(days=i) for i in range(7)]

    # ── Doctor selection ───────────────────────────────────────────────────
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    selected_doctor_id = request.GET.get("doctor_id")
    try:
        selected_doctor_id = int(selected_doctor_id) if selected_doctor_id else None
    except ValueError:
        selected_doctor_id = None
    selected_doctor = next((d for d in doctors if d.id == selected_doctor_id), None) if selected_doctor_id else None

    # ── DoctorAvailability (recurring weekly) ─────────────────────────────
    avail_qs = DoctorAvailability.objects.filter(clinic=clinic, is_active=True).select_related("doctor")
    if selected_doctor:
        avail_qs = avail_qs.filter(doctor=selected_doctor)
    avail_map = {}
    for slot in avail_qs:
        avail_map.setdefault((slot.doctor_id, slot.day_of_week), []).append(slot)

    # ── DoctorAvailabilityException (date-range blocks) ───────────────────
    exc_qs = DoctorAvailabilityException.objects.filter(
        clinic=clinic, is_active=True,
        start_date__lte=week_end, end_date__gte=week_start,
    ).select_related("doctor", "created_by")
    if selected_doctor:
        exc_qs = exc_qs.filter(doctor=selected_doctor)
    exc_map = {}
    for exc in exc_qs:
        for day in week_days:
            if exc.start_date <= day <= exc.end_date:
                exc_map.setdefault((exc.doctor_id, day), []).append(exc)

    # ── Appointment counts per (doctor_id, date) ──────────────────────────
    appt_counts_qs = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date__range=(week_start, week_end),
            status__in=[
                Appointment.Status.PENDING, Appointment.Status.CONFIRMED,
                Appointment.Status.CHECKED_IN, Appointment.Status.IN_PROGRESS,
            ],
        )
        .values("doctor_id", "appointment_date")
        .annotate(count=Count("id"))
    )
    appt_count_map = {
        (row["doctor_id"], row["appointment_date"]): row["count"]
        for row in appt_counts_qs
    }

    # ── Build grid ─────────────────────────────────────────────────────────
    target_doctors = [selected_doctor] if selected_doctor else doctors
    grid = []
    for doctor in target_doctors:
        days_data = []
        for day in week_days:
            day_int = day.weekday()
            slots = avail_map.get((doctor.id, day_int), [])
            exceptions = exc_map.get((doctor.id, day), [])
            appt_count = appt_count_map.get((doctor.id, day), 0)
            if exceptions:
                cell_status = "blocked"
            elif slots:
                cell_status = "busy" if appt_count > 0 else "available"
            else:
                cell_status = "off"
            days_data.append({
                "date": day,
                "day_int": day_int,
                "slots": slots,
                "exceptions": exceptions,
                "appt_count": appt_count,
                "status": cell_status,
                "is_today": day == today,
            })
        grid.append({"doctor": doctor, "days": days_data})

    # ── Upcoming active blocks ─────────────────────────────────────────────
    active_blocks = DoctorAvailabilityException.objects.filter(
        clinic=clinic, is_active=True, end_date__gte=today,
    ).select_related("doctor", "created_by").order_by("start_date")
    if selected_doctor:
        active_blocks = active_blocks.filter(doctor=selected_doctor)

    day_names_ar = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

    return render(request, "secretary/schedule/index.html", {
        "clinic": clinic,
        "today": today,
        "week_start": week_start,
        "week_end": week_end,
        "week_days": week_days,
        "prev_week": prev_week,
        "next_week": next_week,
        "doctors": doctors,
        "selected_doctor": selected_doctor,
        "selected_doctor_id": selected_doctor_id or "",
        "grid": grid,
        "active_blocks": active_blocks,
        "day_names_ar": day_names_ar,
    })


@secretary_required
def block_doctor_time(request, staff):
    """
    Create a DoctorAvailabilityException: block a doctor for a date range.
    Secretary can add blocks. Warns if active appointments exist in the range.
    """
    from clinics.models import ClinicStaff as CS, DoctorAvailabilityException
    from django.core.exceptions import ValidationError as DjangoValidationError


    clinic = staff.clinic
    today = date.today()

    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    _REASON_KEY_TO_AR = {
        "annual_leave": "إجازة سنوية",
        "sick_leave": "إجازة مرضية",
        "conference": "مؤتمر / تدريب",
        "meeting": "اجتماع",
        "emergency": "غياب طارئ",
        "other": "أخرى",
    }
    REASON_CHOICES = [
        ("annual_leave", _("إجازة سنوية")),
        ("sick_leave", _("إجازة مرضية")),
        ("conference", _("مؤتمر / تدريب")),
        ("meeting", _("اجتماع")),
        ("emergency", _("غياب طارئ")),
        ("other", _("أخرى")),
    ]

    error = None
    warning = None
    conflicting_appointments = []

    if request.method == "POST":
        doctor_id_raw = request.POST.get("doctor_id", "").strip()
        start_date_str = request.POST.get("start_date", "").strip()
        end_date_str = request.POST.get("end_date", "").strip()
        reason = request.POST.get("reason", "").strip()
        custom_reason = request.POST.get("custom_reason", "").strip()
        force = request.POST.get("force_create") == "1"
        final_reason = custom_reason if reason == "other" and custom_reason else _REASON_KEY_TO_AR.get(reason, reason)

        try:
            doctor_id = int(doctor_id_raw)
            doctor = next((d for d in doctors if d.id == doctor_id), None)
            if not doctor:
                error = "الطبيب المحدد غير موجود في هذه العيادة."
            elif not start_date_str or not end_date_str:
                error = "يرجى تحديد تاريخ البداية والنهاية."
            else:
                start_date_val = date.fromisoformat(start_date_str)
                end_date_val = date.fromisoformat(end_date_str)
                if end_date_val < start_date_val:
                    error = "تاريخ الانتهاء يجب أن يكون بعد تاريخ البداية."
                else:
                    conflicting_appointments = list(
                        Appointment.objects.filter(
                            clinic=clinic,
                            doctor=doctor,
                            appointment_date__range=(start_date_val, end_date_val),
                            status__in=[
                                Appointment.Status.PENDING,
                                Appointment.Status.CONFIRMED,
                                Appointment.Status.CHECKED_IN,
                            ],
                        ).select_related("patient").order_by("appointment_date", "appointment_time")
                    )
                    if conflicting_appointments and not force:
                        warning = (
                            f"يوجد {len(conflicting_appointments)} موعد في هذه الفترة. "
                            "هل تريد المتابعة وحجب الوقت رغم ذلك؟"
                        )
                    else:
                        try:
                            exc = DoctorAvailabilityException(
                                doctor=doctor,
                                clinic=clinic,
                                start_date=start_date_val,
                                end_date=end_date_val,
                                reason=final_reason,
                                is_active=True,
                                created_by=request.user,
                            )
                            exc.full_clean()
                            exc.save()
                            messages.success(
                                request,
                                _("تم حجب وقت الدكتور %(name)s من %(start)s إلى %(end)s.") % {
                                    "name": doctor.name,
                                    "start": start_date_val.strftime("%Y/%m/%d"),
                                    "end": end_date_val.strftime("%Y/%m/%d"),
                                }
                            )
                            return redirect("secretary:doctor_schedule")
                        except DjangoValidationError as e:
                            msgs = e.messages if hasattr(e, 'messages') else [str(e)]
                            error = " — ".join(msgs)
        except (ValueError, TypeError):
            error = _("بيانات غير صالحة. يرجى التحقق من المدخلات.")

    return render(request, "secretary/schedule/block.html", {
        "clinic": clinic,
        "today": today,
        "doctors": doctors,
        "reason_choices": REASON_CHOICES,
        "error": error,
        "warning": warning,
        "conflicting_appointments": conflicting_appointments,
        "post": request.POST if request.method == "POST" else {},
    })


@secretary_required
@require_POST
def delete_doctor_block(request, staff, exception_id):
    """Deactivate (soft-delete) a DoctorAvailabilityException."""
    from clinics.models import DoctorAvailabilityException


    exc = get_object_or_404(
        DoctorAvailabilityException,
        id=exception_id,
        clinic=staff.clinic,
        is_active=True,
    )
    doctor_name = exc.doctor.name
    exc.is_active = False
    exc.save(update_fields=["is_active", "updated_at"])
    messages.success(request, _("تم إلغاء حجب الوقت للدكتور %(name)s.") % {"name": doctor_name})
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if _is_safe_next(request, next_url):
        return redirect(next_url)
    return redirect("secretary:doctor_schedule")


@secretary_required
def settings_profile(request, staff):
    """Secretary settings & profile page.
    Handles two POST actions:
      - action=profile  → update name, email, city
      - action=password → change password (requires current_password)
    Preferences (calendar default, appointment duration) are stored in localStorage
    and never hit the server.
    """
    from accounts.models import City


    clinic = staff.clinic
    user = request.user
    cities = City.objects.all().order_by("name")

    profile_errors = {}
    password_errors = {}
    profile_data = None  # re-populate form on error

    if request.method == "POST":
        action = request.POST.get("action", "profile")

        # ── Profile edit ────────────────────────────────────────────
        if action == "profile":
            name = request.POST.get("name", "").strip()
            email = request.POST.get("email", "").strip().lower()
            city_id = request.POST.get("city", "").strip()
            profile_data = request.POST

            if name_has_disallowed_chars(name):
                profile_errors["name"] = NAME_DISALLOWED_MESSAGE
            if not name:
                profile_errors["name"] = "الاسم مطلوب."

            # Email validation / change-flow detection
            current_email = (user.email or "").lower()
            email_changed = email and email != current_email

            if email and not profile_errors:
                from django.core.validators import validate_email as _ve
                from django.core.exceptions import ValidationError as _VE
                try:
                    _ve(email)
                except _VE:
                    profile_errors["email"] = "البريد الإلكتروني غير صحيح."

            if not profile_errors:
                user.name = name
                if city_id:
                    try:
                        user.city = City.objects.get(id=city_id)
                    except City.DoesNotExist:
                        user.city = None
                else:
                    user.city = None
                user.save(update_fields=["name", "city"])

                if email_changed and email:
                    # Route through accounts email-change OTP flow
                    request.session["pending_email_change"] = email
                    messages.success(request, _("تم حفظ الاسم والمدينة. أكمل تغيير البريد الإلكتروني بالتحقق."))
                    return redirect("accounts:change_email_otp_request")

                messages.success(request, _("تم حفظ الملف الشخصي بنجاح."))
                return redirect("secretary:settings_profile")

        # ── Display preferences (time format) ────────────────────────
        elif action == "preferences":
            fmt = request.POST.get("time_format", "24")
            if fmt in {"24", "12"}:
                user.time_format = fmt
                user.save(update_fields=["time_format"])
                messages.success(request, _("تم حفظ تفضيل عرض الوقت."))
            return redirect("secretary:settings_profile")

        # ── Password change ──────────────────────────────────────────
        elif action == "password":
            from accounts import ratelimit
            current_pw = request.POST.get("current_password", "")
            new_pw = request.POST.get("new_password", "").strip()
            confirm_pw = request.POST.get("confirm_password", "").strip()

            # Throttle repeated wrong-current-password guesses (a hijacked session
            # shouldn't get unlimited attempts at the existing password).
            if ratelimit.is_blocked("pw_change", user.pk, ratelimit.PW_CHANGE_MAX_ATTEMPTS):
                password_errors["current_password"] = "محاولات كثيرة. حاول مرة أخرى بعد قليل."
            elif not current_pw:
                password_errors["current_password"] = "أدخل كلمة المرور الحالية."
            elif not user.check_password(current_pw):
                ratelimit.register_failure("pw_change", user.pk, ratelimit.PW_CHANGE_WINDOW_SECONDS)
                password_errors["current_password"] = "كلمة المرور الحالية غير صحيحة."

            if not new_pw:
                password_errors["new_password"] = "أدخل كلمة المرور الجديدة."
            elif new_pw != confirm_pw:
                password_errors["confirm_password"] = "كلمتا المرور غير متطابقتين."
            else:
                # Enforce the project's configured AUTH_PASSWORD_VALIDATORS
                # (length, common-password, all-numeric, user-attribute similarity).
                from django.contrib.auth import password_validation
                from django.core.exceptions import ValidationError as _PWValidationError
                try:
                    password_validation.validate_password(new_pw, user)
                except _PWValidationError as exc:
                    password_errors["new_password"] = " ".join(exc.messages)

            if not password_errors:
                ratelimit.clear_failures("pw_change", user.pk)
                user.set_password(new_pw)
                user.save(update_fields=["password"])
                # Re-authenticate so the session stays valid
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)
                messages.success(request, _("تم تغيير كلمة المرور بنجاح."))
                return redirect("secretary:settings_profile")

    return render(request, "secretary/settings/profile.html", {
        "clinic": clinic,
        "staff": staff,
        "cities": cities,
        "profile_errors": profile_errors,
        "password_errors": password_errors,
        "profile_data": profile_data,
    })


@secretary_required
def settings_clinic(request, staff):
    """Clinic-wide booking policy settings, editable by any active secretary."""

    from clinics.services import (
        get_clinic_compliance_settings,
        update_clinic_compliance_settings,
    )

    clinic = staff.clinic
    booking_settings = clinic.get_or_create_booking_settings()

    if request.method == "POST":
        section = request.POST.get("form_section")

        if section == "compliance":
            from django.core.exceptions import ValidationError
            try:
                max_no_show_count = int(request.POST.get("max_no_show_count", 3))
                forgiveness_enabled = request.POST.get("forgiveness_enabled") == "on"
                days_raw = request.POST.get("forgiveness_days")
                forgiveness_days = (
                    int(days_raw) if (days_raw and forgiveness_enabled) else None
                )
                update_clinic_compliance_settings(
                    clinic,
                    max_no_show_count,
                    forgiveness_enabled,
                    forgiveness_days,
                )
                messages.success(request, _("تم حفظ إعدادات الامتثال."))
            except (ValidationError, ValueError):
                messages.error(
                    request,
                    _("تعذّر حفظ إعدادات الامتثال. تأكد من إدخال قيم صحيحة."),
                )
            return redirect("secretary:settings_clinic")

        if section == "kiosk":
            # Rotate the public lobby-screen token — the old kiosk link stops working
            # immediately. Used to recover from a leaked link or retire a screen.
            import uuid as _uuid
            clinic.display_token = _uuid.uuid4()
            clinic.save(update_fields=["display_token"])
            messages.success(
                request,
                _("تم إنشاء رابط جديد لشاشة غرفة الانتظار. لن يعمل الرابط القديم بعد الآن."),
            )
            return redirect("secretary:settings_clinic")

        # Default: booking-policy section
        auto_confirm = bool(request.POST.get("auto_confirm_patient_bookings"))
        allow_multi = bool(request.POST.get("allow_multiple_bookings_same_day"))
        # Same-day rule only makes sense when auto-confirm is on. If a patient
        # has to wait for approval anyway, the same-day toggle has no effect.
        if not auto_confirm:
            allow_multi = False
        booking_settings.auto_confirm_patient_bookings = auto_confirm
        booking_settings.allow_multiple_bookings_same_day = allow_multi
        no_show_after = request.POST.get("no_show_after")
        if no_show_after in dict(booking_settings.NoShowAfter.choices):
            booking_settings.no_show_after = no_show_after
        booking_settings.updated_by = request.user
        booking_settings.save()
        messages.success(request, _("تم حفظ إعدادات الحجز."))
        return redirect("secretary:settings_clinic")

    kiosk_url = request.build_absolute_uri(
        reverse("secretary:waiting_room_display") + f"?token={clinic.display_token}"
    )

    return render(request, "secretary/settings/clinic.html", {
        "clinic": clinic,
        "staff": staff,
        "booking_settings": booking_settings,
        "compliance_settings": get_clinic_compliance_settings(clinic),
        "kiosk_url": kiosk_url,
    })


# ── New appointment module views ──────────────────────────────────────────────

@secretary_required
@require_POST
def accept_new_patient_request(request, staff, appointment_id):
    """Accept an unregistered patient's pending booking: register them in the
    clinic and confirm the appointment (atomic). Reuses _generate_file_number
    and transition_appointment_status."""

    from django.db import transaction
    from secretary.services import transition_appointment_status
    from appointments.services.booking_service import BookingError

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=clinic)

    already_registered = ClinicPatient.objects.filter(
        clinic=clinic, patient=appointment.patient
    ).exists()
    next_url = request.POST.get("next") or ""
    redirect_to = next_url if _is_safe_next(request, next_url) else "secretary:appointments"

    if appointment.status != Appointment.Status.PENDING or already_registered:
        messages.error(request, _("لم يعد هذا الطلب بحاجة إلى مراجعة."))
        return redirect(redirect_to)

    try:
        with transaction.atomic():
            ClinicPatient.objects.get_or_create(
                clinic=clinic,
                patient=appointment.patient,
                defaults={
                    "registered_by": request.user,
                    "file_number": _generate_file_number(clinic),
                },
            )
            transition_appointment_status(
                appointment, Appointment.Status.CONFIRMED,
                actor=request.user, ip=client_ip(request),
            )
        messages.success(request, _("تم قبول المريض الجديد وتأكيد الموعد."))
    except BookingError as e:
        messages.error(request, e.message)
    return redirect(redirect_to)


@secretary_required
@require_POST
def reject_new_patient_request(request, staff, appointment_id):
    """Reject an unregistered patient's pending booking: cancel it without
    registering the patient."""

    from secretary.services import transition_appointment_status
    from appointments.services.booking_service import BookingError

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=clinic)

    next_url = request.POST.get("next") or ""
    redirect_to = next_url if _is_safe_next(request, next_url) else "secretary:appointments"

    if appointment.status != Appointment.Status.PENDING:
        messages.error(request, _("لم يعد هذا الطلب بحاجة إلى مراجعة."))
        return redirect(redirect_to)

    reason = request.POST.get("cancellation_reason", "").strip() or _(
        "تم رفض طلب الحجز من قِبل العيادة"
    )
    try:
        transition_appointment_status(
            appointment,
            Appointment.Status.CANCELLED,
            cancellation_reason=reason,
            actor=request.user,
            ip=client_ip(request),
        )
        messages.success(request, _("تم رفض طلب المريض الجديد."))
    except BookingError as e:
        messages.error(request, e.message)
    return redirect(redirect_to)


@secretary_required
@require_POST
def update_appointment_status(request, staff, appointment_id):
    """
    HTMX endpoint: update appointment status with validation.
    Returns the updated status badge HTML partial.
    """

    from secretary.services import transition_appointment_status, get_valid_transitions
    from appointments.services.booking_service import BookingError

    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)
    new_status = (request.POST.get("new_status") or request.POST.get("status") or "").strip()
    cancellation_reason = request.POST.get("cancellation_reason", "").strip()

    error = None
    if not new_status:
        error = _("لم يتم تحديد الحالة الجديدة.")
    else:
        try:
            appointment = transition_appointment_status(
                appointment, new_status, cancellation_reason=cancellation_reason,
                actor=request.user, ip=client_ip(request),
            )
        except BookingError as e:
            error = e.message
        except Exception as e:
            error = str(e)

    # Non-HTMX submissions (e.g. the "remove from queue" modal form) get a
    # redirect back to where the secretary was, with a flash message.
    if request.headers.get("HX-Request") != "true":
        if error:
            messages.error(request, error)
        else:
            messages.success(request, _("تم تحديث حالة الموعد بنجاح."))
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if _is_safe_next(request, next_url):
            return redirect(next_url)
        return redirect("secretary:waiting_room")

    valid_transitions = get_valid_transitions(appointment.status)
    terminal_statuses = ["CANCELLED", "NO_SHOW", "COMPLETED"]
    status_steps = ["PENDING", "CONFIRMED", "CHECKED_IN", "IN_PROGRESS", "COMPLETED"]
    try:
        current_step_index = status_steps.index(appointment.status)
    except ValueError:
        current_step_index = 0
    return render(request, "secretary/htmx/appointment_status_chip.html", {
        "appointment": appointment,
        "valid_transitions": valid_transitions,
        "error": error,
        "terminal_statuses": terminal_statuses,
        "status_steps": status_steps,
        "current_step_index": current_step_index,
    })


@secretary_required
@require_POST
def remove_from_queue(request, staff, appointment_id):
    """
    Secretary "X" button on a CHECKED_IN row in the waiting room.

      - Walk-in  → delete the appointment (no prior state to return to).
      - Booked   → revert status to CONFIRMED so the original booking is preserved.
    """

    from secretary.services import transition_appointment_status
    from appointments.services.booking_service import BookingError

    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")

    def _redirect_back():
        if _is_safe_next(request, next_url):
            return redirect(next_url)
        return redirect("secretary:waiting_room")

    if appointment.status != Appointment.Status.CHECKED_IN:
        messages.info(request, _("هذا الموعد لم يعد في طابور الانتظار."))
        return _redirect_back()

    if appointment.is_walk_in:
        appt_id = appointment.pk
        appointment.delete()
        log_activity(
            actor=request.user,
            clinic=staff.clinic,
            action=ActivityLog.Action.APPOINTMENT_DELETED,
            target_type="Appointment",
            target_id=appt_id,
            request=request,
            metadata={"is_walk_in": True},
        )
        messages.success(request, _("تم حذف الحضور المباشر من السجلات."))
        return _redirect_back()

    try:
        transition_appointment_status(
            appointment,
            Appointment.Status.CONFIRMED,
            actor=request.user,
            ip=client_ip(request),
        )
    except BookingError as e:
        messages.error(request, e.message)
        return _redirect_back()

    messages.success(request, _("تم إرجاع المريض إلى قائمة المواعيد المؤكدة."))
    return _redirect_back()


@secretary_required
def get_time_slots_htmx(request, staff):
    """
    HTMX endpoint: return available time slots for a doctor on a date.
    GET params: doctor_id, date (YYYY-MM-DD), appointment_type_id
    """

    from datetime import datetime as _dt
    from doctors.services import generate_slots_for_date
    from appointments.services.appointment_type_service import (
        get_slot_step_minutes_for_doctor,
    )

    doctor_id = request.GET.get("doctor_id", "")
    date_str = request.GET.get("appointment_date", "")
    type_id = request.GET.get("appointment_type_id", "")
    exclude_appointment_id_raw = request.GET.get("exclude_appointment_id", "")
    exclude_appointment_id = None
    if exclude_appointment_id_raw:
        try:
            exclude_appointment_id = int(exclude_appointment_id_raw)
        except (TypeError, ValueError):
            exclude_appointment_id = None

    slots = []
    error = None
    duration = 30  # default

    if doctor_id and date_str and type_id:
        try:
            target_date = _dt.strptime(date_str, "%Y-%m-%d").date()
            appt_type = AppointmentType.objects.filter(
                id=type_id, clinic=staff.clinic, is_active=True
            ).first()
            if appt_type:
                duration = appt_type.duration_minutes
                step = get_slot_step_minutes_for_doctor(
                    int(doctor_id), staff.clinic.id
                )
                slots = generate_slots_for_date(
                    doctor_id=int(doctor_id),
                    clinic_id=staff.clinic.id,
                    target_date=target_date,
                    duration_minutes=duration,
                    slot_step_minutes=step,
                    exclude_appointment_id=exclude_appointment_id,
                )
            else:
                error = _("نوع الموعد غير موجود.")
        except ValueError:
            error = _("تاريخ غير صالح.")
        except Exception as e:
            error = str(e)

    return render(request, "secretary/htmx/time_slots.html", {
        "slots": slots,
        "error": error,
        "duration": duration,
    })


@secretary_required
def get_doctor_types_htmx(request, staff):
    """HTMX endpoint: return appointment types for a specific doctor in this clinic."""

    from appointments.services.appointment_type_service import get_appointment_types_for_doctor_in_clinic

    doctor_id = request.GET.get("doctor_id", "")
    types = []
    if doctor_id:
        try:
            types = get_appointment_types_for_doctor_in_clinic(int(doctor_id), staff.clinic.id)
        except Exception:
            types = AppointmentType.objects.filter(clinic=staff.clinic, is_active=True)

    return render(request, "secretary/htmx/doctor_types.html", {
        "appointment_types": types,
    })


@secretary_required(as_json=True)
def doctor_working_days_json(request, staff):
    """
    JSON endpoint: returns the weekdays (Python weekday: 0=Mon..6=Sun) on which
    the selected doctor has at least one active availability block in this clinic.
    """

    from doctors.models import DoctorAvailability

    doctor_id = request.GET.get("doctor_id", "")
    working_days: list[int] = []
    if doctor_id:
        try:
            working_days = sorted(
                set(
                    DoctorAvailability.objects.filter(
                        doctor_id=int(doctor_id),
                        clinic=staff.clinic,
                        is_active=True,
                    ).values_list("day_of_week", flat=True)
                )
            )
        except (ValueError, TypeError):
            working_days = []

    return JsonResponse({"working_days": working_days})


@secretary_required(as_json=True)
def appointments_json(request, staff):
    """
    JSON feed for FullCalendar.
    GET params: start (ISO date), end (ISO date), doctor_id (optional)

    Groups appointments into fixed-width buckets — bucket size = the shortest
    active appointment-type duration in the clinic (or for the selected doctor
    when filtered), falling back to ``DEFAULT_SLOT_STEP_MINUTES``. Each bucket
    renders as a single event (kind="single") if it holds one appointment, or
    a group summary card (kind="group") with a per-status count breakdown when
    multiple appointments fall in the same window.
    """

    import math
    from datetime import datetime as _dt, timedelta
    from collections import defaultdict
    from appointments.services.appointment_type_service import (
        get_slot_step_minutes_for_clinic,
        get_slot_step_minutes_for_doctor,
    )

    clinic = staff.clinic
    start_str = request.GET.get("start", "")
    end_str = request.GET.get("end", "")
    doctor_id = request.GET.get("doctor_id", "")

    qs = Appointment.objects.filter(clinic=clinic).select_related(
        "patient", "doctor", "appointment_type"
    )
    start_date = None
    end_date = None
    if start_str:
        try:
            start_date = _dt.fromisoformat(start_str[:10]).date()
            qs = qs.filter(appointment_date__gte=start_date)
        except ValueError:
            pass
    if end_str:
        try:
            end_date = _dt.fromisoformat(end_str[:10]).date()
            qs = qs.filter(appointment_date__lte=end_date)
        except ValueError:
            pass
    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)

    # Bucket width = shortest active appointment-type duration. Per-doctor when
    # the doctor filter is on (matches the booking-grid step the patient sees);
    # otherwise clinic-wide minimum.
    if doctor_id:
        try:
            bucket_minutes = get_slot_step_minutes_for_doctor(int(doctor_id), clinic.id)
        except (TypeError, ValueError):
            bucket_minutes = get_slot_step_minutes_for_clinic(clinic.id)
    else:
        bucket_minutes = get_slot_step_minutes_for_clinic(clinic.id)
    bucket_minutes = max(bucket_minutes, 1)

    # Bucket each appointment by the day + the bucket index inside that day.
    # Both single and group events snap their start to the bucket boundary so
    # the calendar reads as a fixed slot grid: a 21:13 booking lands on the
    # 21:00 row, not between rows. The card label still shows the true booking
    # time (e.g. "21:13"); only the row position is snapped. Long appointments
    # span multiple slots — slot_count = ceil(duration / bucket_minutes).
    buckets = defaultdict(list)
    for appt in qs:
        start_dt = _dt.combine(appt.appointment_date, appt.appointment_time)
        minutes_since_midnight = start_dt.hour * 60 + start_dt.minute
        bucket_idx = minutes_since_midnight // bucket_minutes
        bucket_start_minutes = bucket_idx * bucket_minutes
        bucket_start = _dt.combine(appt.appointment_date, _dt.min.time()) + timedelta(
            minutes=bucket_start_minutes
        )
        bucket_end = bucket_start + timedelta(minutes=bucket_minutes)

        duration = 30
        if appt.appointment_type and appt.appointment_type.duration_minutes:
            duration = appt.appointment_type.duration_minutes
        slot_count = max(1, math.ceil(duration / bucket_minutes))
        slot_end = bucket_start + timedelta(minutes=slot_count * bucket_minutes)
        payload = {
            "id": appt.id,
            "patient": appt.patient.name,
            "doctor": appt.doctor.name if appt.doctor else "",
            "type": appt.appointment_type.display_name if appt.appointment_type else "",
            "status": appt.status,
            "status_label": appt.get_status_display(),
            "url": reverse("secretary:appointment_overview", kwargs={"appointment_id": appt.id}) + "?return_to=calendar",
            "time": appt.appointment_time.strftime("%H:%M"),
            "time_label": _clock(request, appt.appointment_time),
            "duration_minutes": duration,
        }
        buckets[(appt.appointment_date, bucket_idx)].append(
            {
                "bucket_start": bucket_start,
                "bucket_end": bucket_end,
                "slot_end": slot_end,
                "payload": payload,
            }
        )

    events = []
    for items in buckets.values():
        if len(items) == 1:
            it = items[0]
            events.append(_single_event(it["payload"], it["bucket_start"], it["slot_end"]))
        else:
            bucket_start = items[0]["bucket_start"]
            bucket_end = items[0]["bucket_end"]
            events.append(
                _group_event(
                    [it["payload"] for it in items], bucket_start, bucket_end
                )
            )

    if start_date and end_date:
        events.extend(
            _unavailable_events_for_range(clinic, doctor_id, start_date, end_date)
        )

    return JsonResponse(events, safe=False)


def _clinic_slot_bounds(clinic):
    """Return (start_h, end_h_exclusive) — the calendar's visible envelope.
    Earliest open and latest close across the clinic's weekly working hours,
    snapped to the hour. Falls back to 07–21 when no working hours configured."""
    from django.db.models import Min, Max
    bounds = (
        clinic.working_hours
        .filter(is_closed=False)
        .exclude(start_time__isnull=True)
        .exclude(end_time__isnull=True)
        .aggregate(min_start=Min("start_time"), max_end=Max("end_time"))
    )
    min_start = bounds.get("min_start")
    max_end = bounds.get("max_end")
    if min_start and max_end:
        start_h = min_start.hour
        close_h = max_end.hour + (1 if (max_end.minute or max_end.second) else 0)
    else:
        start_h, close_h = 7, 20
    return start_h, min(close_h + 1, 24)


def _bg_unavailable(d, start_h, bucket_min, i_start, i_end):
    """Build a FullCalendar background event for an unavailable bucket range."""
    from datetime import datetime as _dt, timedelta
    base = _dt.combine(d, _dt.min.time()) + timedelta(hours=start_h)
    return {
        "start": (base + timedelta(minutes=i_start * bucket_min)).isoformat(),
        "end":   (base + timedelta(minutes=i_end * bucket_min)).isoformat(),
        "display": "background",
        "classNames": ["fc-bg-unavailable"],
        "groupId": "unavailable",
        "extendedProps": {"kind": "unavailable"},
    }


def _unavailable_events_for_range(clinic, doctor_id, start_date, end_date):
    """Compute background events for slots where the (filtered) doctor(s) don't work.

    - doctor_id given → that doctor's non-working ranges.
    - doctor_id empty → ranges where ALL active clinic doctors are unavailable
      (intersection of unavailability == complement of union of working time).
    """
    from datetime import timedelta
    from doctors.services import generate_slots_for_date
    from clinics.models import ClinicStaff

    BUCKET_MIN = 15
    start_h, end_h = _clinic_slot_bounds(clinic)
    envelope_buckets_per_day = ((end_h - start_h) * 60) // BUCKET_MIN
    if envelope_buckets_per_day <= 0:
        return []

    if doctor_id:
        try:
            doc_ids = [int(doctor_id)]
        except (TypeError, ValueError):
            return []
    else:
        doc_ids = list(
            ClinicStaff.objects
            .filter(clinic=clinic, role="DOCTOR", is_active=True)
            .values_list("user_id", flat=True)
        )

    events = []
    d = start_date
    while d < end_date:
        # Bucket index → True if AT LEAST ONE doctor works it.
        # For specific-doctor case the union over [doc_id] reduces to that doctor's set.
        working_idxs = set()
        if doc_ids:
            for did in doc_ids:
                slots = generate_slots_for_date(did, clinic.id, d, BUCKET_MIN, BUCKET_MIN)
                for s in slots:
                    minutes = s["time"].hour * 60 + s["time"].minute
                    if minutes < start_h * 60 or minutes >= end_h * 60:
                        continue
                    idx = (minutes - start_h * 60) // BUCKET_MIN
                    working_idxs.add(idx)
        # Buckets NOT in working_idxs are unavailable. Coalesce contiguous runs.
        run_start = None
        for i in range(envelope_buckets_per_day):
            blocked = i not in working_idxs
            if blocked and run_start is None:
                run_start = i
            elif not blocked and run_start is not None:
                events.append(_bg_unavailable(d, start_h, BUCKET_MIN, run_start, i))
                run_start = None
        if run_start is not None:
            events.append(_bg_unavailable(d, start_h, BUCKET_MIN, run_start, envelope_buckets_per_day))
        # Fully-closed day marker — used by Month view to shade the whole cell.
        # Hidden in time-grid views; only consumed via getEvents() in JS.
        if not working_idxs:
            events.append({
                "start": d.isoformat(),
                "end": (d + timedelta(days=1)).isoformat(),
                "allDay": True,
                "display": "none",
                "groupId": "unavailable",
                "extendedProps": {"kind": "day_closed"},
            })
        d += timedelta(days=1)
    return events


# Status colors / labels used by both the calendar legend and the JSON feed.
CALENDAR_STATUS_COLORS = {
    "PENDING":     "#d97706",  # amber
    "CONFIRMED":   "#10b981",  # emerald
    "CHECKED_IN":  "#3b82f6",  # blue
    "IN_PROGRESS": "#8b5cf6",  # purple
    "COMPLETED":   "#6b7280",  # gray
    "CANCELLED":   "#ef4444",  # red
    "NO_SHOW":     "#f59e0b",  # orange
}


def _single_event(payload, start_dt, end_dt):
    return {
        "id": payload["id"],
        "title": payload["patient"] + (f" — {payload['doctor']}" if payload["doctor"] else ""),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "color": CALENDAR_STATUS_COLORS.get(payload["status"], "#6b7280"),
        "url": payload["url"],
        "extendedProps": {
            "kind": "single",
            "status": payload["status"],
            "status_label": payload["status_label"],
            "patient": payload["patient"],
            "doctor": payload["doctor"],
            "type": payload["type"],
            "time_label": payload["time_label"],
        },
    }


def _group_event(active_payloads, t_start, t_end):
    from collections import defaultdict
    by_status_map = defaultdict(list)
    for p in active_payloads:
        by_status_map[p["status"]].append(p)
    by_status = []
    for status, items in by_status_map.items():
        by_status.append({
            "status": status,
            "status_label": items[0]["status_label"],
            "color": CALENDAR_STATUS_COLORS.get(status, "#6b7280"),
            "count": len(items),
        })
    return {
        "id": f"group-{t_start.isoformat()}-{len(active_payloads)}",
        "start": t_start.isoformat(),
        "end": t_end.isoformat(),
        "color": "#475569",  # neutral fallback; stripes paint real colors
        "display": "block",
        "extendedProps": {
            "kind": "group",
            "count": len(active_payloads),
            "by_status": by_status,
            "appointments": active_payloads,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PATIENT MANAGEMENT MODULE
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_file_number(clinic) -> str:
    """Auto-generate per-clinic file number: YYYY-NNNN (e.g. 2026-0001)."""
    year = date.today().year
    count = ClinicPatient.objects.filter(
        clinic=clinic, registered_at__year=year
    ).count() + 1
    return f"{year}-{count:04d}"


def _compute_age(dob) -> int | None:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# Allowed patient-list sort fields (whitelist), shared by the full page and
# the HTMX live-search endpoint.
_PATIENT_LIST_ALLOWED_SORTS = {
    "name": "patient__name",
    "-name": "-patient__name",
    "file_number": "file_number",
    "-file_number": "-file_number",
    "registered_at": "registered_at",
    "-registered_at": "-registered_at",
}


def _patient_list_queryset(clinic, search="", sort="-registered_at", blocked_only=False):
    """Shared roster queryset for the patient list (full page + HTMX search).

    Annotates each ClinicPatient with last_visit, visit_count and is_blocked
    (compliance BLOCKED in this clinic, derived via an Exists subquery so
    profile-less users simply yield is_blocked=False). Applies whitelisted
    sort, optional search, and an optional blocked-only filter.
    """
    from django.db.models import Max, Count, Exists, OuterRef
    from compliance.models import PatientClinicCompliance

    order_by = _PATIENT_LIST_ALLOWED_SORTS.get(sort, "-registered_at")

    qs = (
        ClinicPatient.objects.filter(clinic=clinic)
        .select_related("patient", "patient__patient_profile")
        .annotate(
            last_visit=Max(
                "patient__appointments_as_patient__appointment_date",
                filter=Q(
                    patient__appointments_as_patient__clinic=clinic,
                    patient__appointments_as_patient__status="COMPLETED",
                ),
            ),
            visit_count=Count(
                "patient__appointments_as_patient",
                filter=Q(
                    patient__appointments_as_patient__clinic=clinic,
                    patient__appointments_as_patient__status="COMPLETED",
                ),
            ),
            is_blocked=Exists(
                PatientClinicCompliance.objects.filter(
                    clinic=clinic,
                    status="BLOCKED",
                    patient=OuterRef("patient__patient_profile"),
                )
            ),
        )
        .order_by(order_by)
    )

    if search:
        normalized = PhoneNumberAuthBackend.normalize_phone_number(search)
        qs = qs.filter(
            Q(patient__name__icontains=search)
            | Q(patient__phone__icontains=normalized)
            | Q(patient__national_id__icontains=search)
            | Q(file_number__icontains=search)
        )

    if blocked_only:
        qs = qs.filter(is_blocked=True)

    return qs


def _patient_list_filter(request):
    """Read + whitelist the patient-list status filter from the request."""
    value = request.GET.get("filter", "")
    return value if value in ("all", "blocked") else ""


@secretary_required
def patient_list(request, staff):
    """Full patient roster for this clinic with search, sort, pagination."""

    from django.core.paginator import Paginator
    from compliance.services.compliance_service import count_blocked_patients

    clinic = staff.clinic
    search = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "-registered_at")
    current_filter = _patient_list_filter(request)

    qs = _patient_list_queryset(
        clinic,
        search=search,
        sort=sort,
        blocked_only=(current_filter == "blocked"),
    )

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "secretary/patients/list.html", {
        "clinic": clinic,
        "page_obj": page,
        "clinic_patients": page.object_list,
        "search": search,
        "sort": sort,
        "current_filter": current_filter,
        "blocked_filter_active": current_filter == "blocked",
        "blocked_count": count_blocked_patients(clinic),
        "total_count": paginator.count,
    })


@secretary_required
def patient_detail(request, staff, patient_id):
    """Full patient profile: personal info, appointments, medical records, billing."""

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)

    # Must be registered in this clinic
    clinic_patient = get_object_or_404(ClinicPatient, clinic=clinic, patient=patient)
    profile = getattr(patient, "patient_profile", None)
    age = _compute_age(profile.date_of_birth if profile else None)

    # Appointments at this clinic (newest first)
    appointments = (
        Appointment.objects.filter(clinic=clinic, patient=patient)
        .select_related("doctor", "appointment_type")
        .order_by("-appointment_date", "-appointment_time")
    )

    # Medical records (read-only)
    from patients.models import MedicalRecord
    medical_records = MedicalRecord.objects.filter(
        clinic=clinic, patient=patient
    ).select_related("uploaded_by").order_by("-uploaded_at")

    # Billing — optional; Invoice model may not exist in all deployments
    invoices = []
    invoices_count = 0
    balance_due_total = 0
    total_paid = 0
    inv_filter = request.GET.get("inv_status", "all")
    payment_form = None
    try:
        from secretary.models import Invoice
        from secretary.forms import PaymentForm

        void_statuses = [Invoice.Status.CANCELLED, Invoice.Status.REFUNDED]
        base_qs = Invoice.objects.filter(clinic=clinic, patient=patient)

        # Summary totals always reflect the whole patient (independent of the filter).
        invoices_count = base_qs.count()
        agg = base_qs.exclude(status__in=void_statuses).aggregate(
            balance=Sum("balance_due"), paid=Sum("amount_paid")
        )
        balance_due_total = agg["balance"] or 0
        total_paid = agg["paid"] or 0

        # The listed invoices follow the status filter.
        list_qs = base_qs.order_by("-created_at")
        if inv_filter == "open":
            list_qs = list_qs.filter(status__in=[Invoice.Status.DRAFT, Invoice.Status.PARTIAL])
        elif inv_filter == "unpaid":
            list_qs = list_qs.filter(balance_due__gt=0).exclude(status__in=void_statuses)
        elif inv_filter == "paid":
            list_qs = list_qs.filter(status=Invoice.Status.PAID)
        invoices = list(list_qs)

        # Clear-debts form: prefilled to the full outstanding amount and capped to it.
        payment_form = PaymentForm(
            initial={"amount": balance_due_total}, max_payable=balance_due_total
        )
    except ImportError:
        pass  # Billing module not installed

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]

    active_tab = request.GET.get("tab", "info")

    # Authored staff notes on the patient profile — secretaries see doctor + secretary-only
    # audiences, but never a doctor's private notes.
    patient_notes = list(
        StaffNote.objects.filter(
            clinic=clinic, patient=patient, appointment__isnull=True
        ).exclude(audience=StaffNote.Audience.DOCTOR_PRIVATE)
        .select_related("author").order_by("-created_at")
    )

    from patients.models import PatientProfile as _PP
    blood_type_choices = _PP.BLOOD_TYPE_CHOICES

    # Latest clinical note, only if the doctor allowed secretary access to it.
    latest_clinical_note = _secretary_visible_note(clinic, patient_id)
    if latest_clinical_note is not None:
        log_activity(
            actor=request.user,
            clinic=clinic,
            action=ActivityLog.Action.CLINICAL_NOTE_VIEWED,
            target=latest_clinical_note,
            request=request,
            metadata={
                "note_id": latest_clinical_note.id,
                "doctor_id": latest_clinical_note.doctor_id,
                "via": "detail",
            },
        )

    tab_list = [
        ("info",         _("المعلومات الشخصية"), "fa-solid fa-user"),
        ("appointments", _("المواعيد"),           "fa-solid fa-calendar-days"),
        ("records",      _("السجلات الطبية"),     "fa-solid fa-folder-open"),
        ("billing",      _("الفواتير"),            "fa-solid fa-receipt"),
    ]

    return render(request, "secretary/patients/detail.html", {
        "clinic": clinic,
        "patient": patient,
        "profile": profile,
        "clinic_patient": clinic_patient,
        "age": age,
        "appointments": appointments,
        "medical_records": medical_records,
        "invoices": invoices,
        "invoices_count": invoices_count,
        "inv_filter": inv_filter,
        "payment_form": payment_form,
        "balance_due_total": balance_due_total,
        "total_paid": total_paid,
        "terminal_statuses": terminal_statuses,
        "active_tab": active_tab,
        "tab_list": tab_list,
        "blood_type_choices": blood_type_choices,
        "patient_notes": patient_notes,
        "latest_clinical_note": latest_clinical_note,
    })


@secretary_required
def clinical_note_print(request, staff, patient_id):
    """Printable (browser 'Save as PDF') view of the patient's latest clinical note,
    for a secretary. Access is granted only when that latest note was flagged
    secretary-visible by its doctor (see ``_secretary_visible_note``)."""

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)
    get_object_or_404(ClinicPatient, clinic=clinic, patient=patient)

    note = _secretary_visible_note(clinic, patient_id)
    if note is None:
        return HttpResponseForbidden(
            "لا يُسمح لك بعرض هذه الملاحظة السريرية."
        )

    from doctors.views import _annotate_notes_with_labeled_extras
    _annotate_notes_with_labeled_extras([note])
    log_activity(
        actor=request.user,
        clinic=clinic,
        action=ActivityLog.Action.CLINICAL_NOTE_VIEWED,
        target=note,
        request=request,
        metadata={"note_id": note.id, "doctor_id": note.doctor_id, "via": "print"},
    )
    return render(
        request,
        "doctors/clinical_note_print.html",
        {"note": note, "patient": patient, "clinic": clinic},
    )


@secretary_required
@require_POST
def patient_pay_debt(request, staff, patient_id):
    """Settle a patient's outstanding debts in one go (FIFO, oldest debt first)."""

    from secretary.models import Invoice
    from secretary import billing
    from secretary.forms import PaymentForm

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)
    # Must be registered in this clinic
    get_object_or_404(ClinicPatient, clinic=clinic, patient=patient)

    redirect_url = reverse("secretary:patient_detail", args=[patient_id]) + "?tab=billing"

    outstanding = billing.patient_outstanding(clinic, patient)
    if outstanding <= 0:
        messages.info(request, _("لا توجد ديون مستحقة على هذا المريض."))
        return redirect(redirect_url)

    form = PaymentForm(request.POST, max_payable=outstanding)
    if not form.is_valid():
        messages.error(request, _("تعذّر تسجيل الدفعة. تحقق من المبلغ."))
        return redirect(redirect_url)

    # Oldest non-void invoice that still carries a balance leads the FIFO settlement.
    primary = (
        Invoice.objects.filter(clinic=clinic, patient=patient, balance_due__gt=0)
        .exclude(status__in=[Invoice.Status.CANCELLED, Invoice.Status.REFUNDED])
        .order_by("created_at")
        .first()
    )
    if primary is None:
        messages.info(request, _("لا توجد ديون مستحقة على هذا المريض."))
        return redirect(redirect_url)

    try:
        billing.record_payment(
            primary_invoice=primary,
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            by_user=request.user,
            ip=client_ip(request),
        )
        messages.success(request, _("تم تسجيل الدفعة وتسديد الديون."))
    except billing.BillingError as e:
        messages.error(request, e.message)
    return redirect(redirect_url)


@secretary_required
def edit_patient(request, staff, patient_id):
    """Edit patient demographics (secretary-permitted fields only)."""

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)
    clinic_patient = get_object_or_404(ClinicPatient, clinic=clinic, patient=patient)
    profile, _ = PatientProfile.objects.get_or_create(user=patient)

    from accounts.models import City
    cities = City.objects.all()
    blood_type_choices = PatientProfile.BLOOD_TYPE_CHOICES

    if request.method == "POST":
        errors = {}

        # Name
        name = request.POST.get("name", "").strip()
        if name_has_disallowed_chars(name):
            errors["name"] = NAME_DISALLOWED_MESSAGE
        if not name:
            errors["name"] = "الاسم الكامل مطلوب."

        # Date of birth
        dob = None
        dob_str = request.POST.get("date_of_birth", "").strip()
        if dob_str:
            try:
                from datetime import datetime as _dt
                dob = _dt.strptime(dob_str, "%Y-%m-%d").date()
            except ValueError:
                errors["date_of_birth"] = "تاريخ الميلاد غير صالح."

        gender = request.POST.get("gender", "").strip()
        blood_type = request.POST.get("blood_type", "").strip()
        city_id = request.POST.get("city", "").strip()
        emergency_name = request.POST.get("emergency_contact_name", "").strip()
        emergency_phone = request.POST.get("emergency_contact_phone", "").strip()
        allergies = request.POST.get("allergies", "").strip()
        notes = request.POST.get("notes", "").strip()

        if errors:
            return render(request, "secretary/patients/edit.html", {
                "clinic": clinic,
                "patient": patient,
                "profile": profile,
                "clinic_patient": clinic_patient,
                "cities": cities,
                "blood_type_choices": blood_type_choices,
                "errors": errors,
                "post": request.POST,
            })

        # Save user fields
        user_dirty = []
        if patient.name != name:
            patient.name = name
            user_dirty.append("name")
        if city_id:
            try:
                city_obj = City.objects.get(id=city_id)
                if patient.city_id != city_obj.id:
                    patient.city = city_obj
                    user_dirty.append("city")
            except City.DoesNotExist:
                pass
        elif patient.city_id:
            patient.city = None
            user_dirty.append("city")
        if user_dirty:
            patient.save(update_fields=user_dirty)

        # Save profile fields
        profile_dirty = []
        if dob is not None and profile.date_of_birth != dob:
            profile.date_of_birth = dob
            profile_dirty.append("date_of_birth")
        elif not dob_str and profile.date_of_birth:
            profile.date_of_birth = None
            profile_dirty.append("date_of_birth")

        if gender and profile.gender != gender:
            profile.gender = gender
            profile_dirty.append("gender")

        if blood_type and profile.blood_type != blood_type:
            profile.blood_type = blood_type
            profile_dirty.append("blood_type")

        if profile.emergency_contact_name != emergency_name:
            profile.emergency_contact_name = emergency_name
            profile_dirty.append("emergency_contact_name")

        if profile.emergency_contact_phone != emergency_phone:
            profile.emergency_contact_phone = emergency_phone
            profile_dirty.append("emergency_contact_phone")

        if profile.allergies != allergies:
            profile.allergies = allergies
            profile_dirty.append("allergies")

        if profile_dirty:
            profile.save(update_fields=profile_dirty)

        # Update clinic patient notes
        notes_changed = clinic_patient.notes != notes
        if notes_changed:
            clinic_patient.notes = notes
            clinic_patient.save(update_fields=["notes"])

        changed_fields = list(user_dirty) + list(profile_dirty) + (["notes"] if notes_changed else [])
        log_activity(
            actor=request.user,
            clinic=clinic,
            action=ActivityLog.Action.PATIENT_UPDATED,
            target=patient,
            request=request,
            metadata={"changed_fields": changed_fields},
        )

        messages.success(request, _("تم تحديث بيانات المريض %(name)s بنجاح.") % {"name": patient.name})
        return redirect("secretary:patient_detail", patient_id=patient.id)

    return render(request, "secretary/patients/edit.html", {
        "clinic": clinic,
        "patient": patient,
        "profile": profile,
        "clinic_patient": clinic_patient,
        "cities": cities,
        "blood_type_choices": blood_type_choices,
        "errors": {},
        "post": {},
    })


@secretary_required
def create_new_patient(request, staff):
    """
    Create a brand-new patient (not yet in the system) and register them in this clinic.
    If a user with the same phone already exists, redirect to register_patient with warning.
    """

    clinic = staff.clinic
    from accounts.models import City

    cities = City.objects.all()
    blood_type_choices = PatientProfile.BLOOD_TYPE_CHOICES

    if request.method == "POST":
        errors = {}

        # Required
        name = request.POST.get("name", "").strip()
        phone_raw = request.POST.get("phone", "").strip()

        if not name:
            errors["name"] = "الاسم الكامل مطلوب."
        if not phone_raw:
            errors["phone"] = "رقم الهاتف مطلوب."
        else:
            normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(phone_raw)
            if not PhoneNumberAuthBackend.is_valid_phone_number(normalized_phone):
                errors["phone"] = "رقم الهاتف غير صالح. يجب أن يبدأ بـ 05 ويكون 10 أرقام."

        if errors:
            return render(request, "secretary/patients/register.html", {
                "clinic": clinic,
                "cities": cities,
                "blood_type_choices": blood_type_choices,
                "errors": errors,
                "post": request.POST,
                "mode": "new",
            })

        normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(phone_raw)

        # Duplicate detection
        existing_user = User.objects.filter(phone=normalized_phone).first()
        if existing_user:
            # Already exists — check if already in clinic
            already_in_clinic = ClinicPatient.objects.filter(
                clinic=clinic, patient=existing_user
            ).exists()
            if already_in_clinic:
                messages.warning(
                    request,
                    f"المريض {existing_user.name} مسجل بهذا الرقم وهو بالفعل في قائمة مرضى العيادة."
                )
                return redirect("secretary:patient_detail", patient_id=existing_user.id)
            else:
                # Existing user (may already hold DOCTOR/MAIN_DOCTOR/SECRETARY roles
                # — preserve them and just append PATIENT). Do NOT strip any role.
                existing_roles = list(existing_user.roles or [])
                if "PATIENT" not in existing_roles:
                    existing_roles.append("PATIENT")
                    existing_user.roles = existing_roles
                    existing_user.save(update_fields=["roles"])

                from patients.services import ensure_patient_profile
                ensure_patient_profile(existing_user)

                messages.info(
                    request,
                    _("يوجد حساب بهذا الرقم (%(name)s). تم تسجيله في عيادتك.")
                    % {"name": existing_user.name}
                )
                # Register existing patient into clinic
                file_number = _generate_file_number(clinic)
                ClinicPatient.objects.create(
                    clinic=clinic,
                    patient=existing_user,
                    registered_by=request.user,
                    file_number=file_number,
                    notes=request.POST.get("notes", "").strip(),
                )
                log_activity(
                    actor=request.user,
                    clinic=clinic,
                    action=ActivityLog.Action.PATIENT_REGISTERED,
                    target=existing_user,
                    request=request,
                    metadata={"file_number": file_number, "new_user": False},
                )
                return redirect("secretary:patient_detail", patient_id=existing_user.id)

        # Also check by national ID if provided
        national_id = request.POST.get("national_id", "").strip()
        if national_id:
            nid_user = User.objects.filter(national_id=national_id).first()
            if nid_user:
                messages.warning(
                    request,
                    f"يوجد حساب برقم الهوية هذا ({nid_user.name} — {nid_user.phone}). "
                    "يرجى التحقق قبل الإنشاء."
                )
                return render(request, "secretary/patients/register.html", {
                    "clinic": clinic,
                    "cities": cities,
                    "blood_type_choices": blood_type_choices,
                    "errors": {"national_id": f"رقم الهوية موجود مسبقاً للمريض: {nid_user.name} ({nid_user.phone})"},
                    "post": request.POST,
                    "mode": "new",
                })

        # Create new user
        from django.db import transaction as _txn
        with _txn.atomic():
            new_user = User.objects.create_user(
                phone=normalized_phone,
                name=name,
                role="PATIENT",
                roles=["PATIENT"],
                national_id=national_id or None,
                is_verified=False,
            )
            # Set random unusable password (they'll use OTP to log in)
            new_user.set_unusable_password()

            # City
            city_id = request.POST.get("city", "").strip()
            if city_id:
                try:
                    new_user.city = City.objects.get(id=city_id)
                except City.DoesNotExist:
                    pass
            new_user.save()

            # PatientProfile
            dob = None
            dob_str = request.POST.get("date_of_birth", "").strip()
            if dob_str:
                try:
                    from datetime import datetime as _dt
                    dob = _dt.strptime(dob_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

            PatientProfile.objects.create(
                user=new_user,
                date_of_birth=dob,
                gender=request.POST.get("gender", "").strip(),
                blood_type=request.POST.get("blood_type", "").strip(),
                emergency_contact_name=request.POST.get("emergency_contact_name", "").strip(),
                emergency_contact_phone=request.POST.get("emergency_contact_phone", "").strip(),
                allergies=request.POST.get("allergies", "").strip(),
                medical_history=request.POST.get("medical_history", "").strip(),
            )

            # ClinicPatient with auto file_number
            file_number = _generate_file_number(clinic)
            ClinicPatient.objects.create(
                clinic=clinic,
                patient=new_user,
                registered_by=request.user,
                file_number=file_number,
                notes=request.POST.get("notes", "").strip(),
            )
            log_activity(
                actor=request.user,
                clinic=clinic,
                action=ActivityLog.Action.PATIENT_REGISTERED,
                target=new_user,
                request=request,
                metadata={"file_number": file_number, "new_user": True},
            )

        messages.success(
            request,
            f"تم تسجيل المريض {new_user.name} بنجاح — رقم الملف: {file_number}"
        )
        return redirect("secretary:patient_detail", patient_id=new_user.id)

    return render(request, "secretary/patients/register.html", {
        "clinic": clinic,
        "cities": cities,
        "blood_type_choices": blood_type_choices,
        "errors": {},
        "post": {},
        "mode": "new",
    })


@secretary_required
def patient_list_htmx(request, staff):
    """HTMX live search endpoint for the patient list table."""

    clinic = staff.clinic
    search = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "-registered_at")
    current_filter = _patient_list_filter(request)

    # Live-search only kicks in at 2+ chars; below that show the full list
    # (still honoring the active sort + blocked filter).
    effective_search = search if len(search) >= 2 else ""

    qs = _patient_list_queryset(
        clinic,
        search=effective_search,
        sort=sort,
        blocked_only=(current_filter == "blocked"),
    )[:20]

    return render(request, "secretary/htmx/patient_list_rows.html", {
        "clinic_patients": qs,
        "search": search,
    })


@secretary_required
@require_POST
def remove_patient_block(request, staff, patient_id):
    """Lift a patient's no-show block (manual waiver) — e.g. when the patient
    comes to the clinic in person. Reuses compliance.apply_manual_waiver."""

    clinic = staff.clinic
    cp = get_object_or_404(ClinicPatient, clinic=clinic, patient_id=patient_id)
    profile = getattr(cp.patient, "patient_profile", None)
    if profile is None:
        messages.error(request, _("تعذّر رفع الحظر: لا يوجد ملف مريض."))
    else:
        from compliance.services.compliance_service import apply_manual_waiver
        apply_manual_waiver(clinic, profile, staff_user=request.user)
        messages.success(request, _("تم رفع الحظر عن المريض."))

    # Preserve the list state (search / sort / filter / page) on return.
    params = {}
    for key in ("q", "sort", "filter", "page"):
        val = request.POST.get(key) or request.GET.get(key)
        if val:
            params[key] = val
    url = reverse("secretary:patient_list")
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    return redirect(url)


@secretary_required
def appointments_list(request, staff):
    """Full appointment list with filters, search, and pagination."""

    from django.core.paginator import Paginator
    from clinics.models import ClinicStaff as CS

    clinic = staff.clinic
    _sweep_clinic_no_shows(clinic)
    status_filter = request.GET.get("status", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    doctor_filter = request.GET.get("doctor_id", "")
    search = request.GET.get("q", "").strip()

    from django.db.models import Exists, OuterRef

    qs = (
        Appointment.objects.filter(clinic=clinic)
        .select_related("patient", "doctor", "appointment_type")
        .annotate(
            patient_registered=Exists(
                ClinicPatient.objects.filter(
                    clinic=clinic, patient=OuterRef("patient")
                )
            )
        )
        .order_by("-appointment_date", "appointment_time")
    )
    if status_filter == "new_patient":
        # Pseudo-filter: pending bookings from patients not yet registered
        # in this clinic — the "new patient" requests.
        qs = qs.filter(
            status=Appointment.Status.PENDING, patient_registered=False
        )
    elif status_filter:
        qs = qs.filter(status=status_filter)
    if doctor_filter:
        qs = qs.filter(doctor_id=doctor_filter)
    if date_from:
        try:
            from datetime import datetime as _dt
            qs = qs.filter(appointment_date__gte=_dt.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import datetime as _dt
            qs = qs.filter(appointment_date__lte=_dt.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            pass
    if search:
        from accounts.backends import PhoneNumberAuthBackend as _PH
        norm = _PH.normalize_phone_number(search)
        qs = qs.filter(
            Q(patient__name__icontains=search) | Q(patient__phone__icontains=norm)
        )

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))

    # Doctors for filter dropdown
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctor_users = [s.user for s in doctor_staff]

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]

    # Outstanding-debt badge per patient (one aggregate query for the whole page).
    from secretary import billing
    debt_map = billing.debt_map(clinic, [a.patient_id for a in page.object_list])
    for a in page.object_list:
        a.debt = debt_map.get(a.patient_id)

    return render(request, "secretary/appointments/list.html", {
        "clinic": clinic,
        "page_obj": page,
        "appointments": page.object_list,
        "status_choices": Appointment.Status.choices,
        "current_status": status_filter,
        "current_date_from": date_from,
        "current_date_to": date_to,
        "current_doctor": doctor_filter,
        "search": search,
        "doctor_users": doctor_users,
        "terminal_statuses": terminal_statuses,
        "total_count": paginator.count,
        "debt_map": debt_map,
    })


@secretary_required
def create_appointment(request, staff):
    """Secretary books an appointment on behalf of a patient."""

    clinic = staff.clinic
    from clinics.models import ClinicStaff as CS
    doctors_qs = CS.objects.filter(
        clinic=clinic, role="DOCTOR", is_active=True
    ).select_related("user").order_by("user__name")
    doctor_users = [s.user for s in doctors_qs]

    valid_doctor_ids = {u.id for u in doctor_users}
    # Pre-fill date/time/doctor from query params (when clicking from schedule page)
    prefill_date = request.GET.get("date", "")
    prefill_time = request.GET.get("time", "")
    prefill_patient_id = request.GET.get("patient_id", "")
    return_to = request.GET.get("return_to", "")
    try:
        prefill_doctor_id = int(request.GET.get("doctor_id", "") or 0)
        if prefill_doctor_id not in valid_doctor_ids:
            prefill_doctor_id = 0
    except (ValueError, TypeError):
        prefill_doctor_id = 0

    if request.method == "POST":
        from secretary.services import secretary_book_appointment
        from appointments.services.booking_service import BookingError, SlotUnavailableError
        from appointments.services.appointment_type_service import get_appointment_types_for_doctor_in_clinic
        try:
            patient_id = request.POST.get("patient_id", "").strip()
            patient_phone = request.POST.get("patient_phone", "").strip()
            doctor_id = int(request.POST.get("doctor_id") or 0)
            type_id = int(request.POST.get("appointment_type_id") or 0)
            date_str = request.POST.get("appointment_date", "").strip()
            time_str = request.POST.get("appointment_time", "").strip()
            reason = request.POST.get("reason", "").strip()
            secretary_note_text = request.POST.get("secretary_note", "").strip()
            doctor_note_text = request.POST.get("doctor_note", "").strip()
            post_return_to = request.POST.get("return_to", "").strip()

            if not all([doctor_id, type_id, date_str, time_str]):
                messages.error(request, _("يرجى ملء جميع الحقول المطلوبة."))
                return redirect("secretary:create_appointment")

            if doctor_id not in valid_doctor_ids:
                messages.error(request, _("الطبيب المحدد لا ينتمي إلى هذه العيادة."))
                return redirect("secretary:create_appointment")

            from datetime import datetime as dt_cls
            appt_date = dt_cls.strptime(date_str, "%Y-%m-%d").date()
            appt_time = dt_cls.strptime(time_str, "%H:%M").time()

            # Resolve patient: prefer ID, fall back to phone search
            patient = None
            if patient_id:
                try:
                    patient = User.objects.get(id=patient_id)
                except User.DoesNotExist:
                    pass
            if patient is None and patient_phone:
                normalized = PhoneNumberAuthBackend.normalize_phone_number(patient_phone)
                try:
                    patient = User.objects.get(phone=normalized)
                except User.DoesNotExist:
                    pass
            if patient is None:
                messages.error(request, _("يرجى اختيار مريض أو إدخال رقم هاتف صحيح."))
                return redirect("secretary:create_appointment")

            # Cross-tenant guard: only book for a patient the secretary can
            # legitimately reach — already registered in THIS clinic, or matched by
            # the exact phone strong-identifier they just typed. Mirrors the patient
            # card / search guard so this POST can't be used to enumerate or inject
            # appointments against the global patient directory by raw id.
            if not _patient_reachable_for_registration(clinic, patient, q=patient_phone):
                messages.error(request, _("هذا المريض غير مسجّل في هذه العيادة."))
                return redirect("secretary:create_appointment")

            # ── Optional doctor intake form (secretary may fill it on the patient's
            # behalf). All fields are optional here; only file type/size limits are
            # validated. Validate BEFORE booking so a file error doesn't create an
            # orphaned appointment.
            fill_intake = request.POST.get("fill_intake") == "1"
            intake_questions, intake_answers, intake_files = [], {}, {}
            if fill_intake:
                from appointments.services.intake_service import (
                    get_active_intake_template,
                    collect_and_validate_intake,
                    save_intake_answers,
                )
                _intake_template, intake_questions = get_active_intake_template(
                    doctor_id, type_id
                )
                if intake_questions:
                    intake_answers, intake_files, intake_errors = collect_and_validate_intake(
                        request.POST, request.FILES, intake_questions, [],
                        enforce_required=False,
                    )
                    if intake_errors:
                        for err in intake_errors:
                            messages.error(request, err)
                        url = reverse("secretary:create_appointment")
                        url += (
                            f"?doctor_id={doctor_id}&date={date_str}"
                            f"&time={time_str}&patient_id={patient.id}"
                        )
                        if post_return_to:
                            url += f"&return_to={post_return_to}"
                        return redirect(url)

            appointment = secretary_book_appointment(
                patient=patient,
                doctor_id=doctor_id,
                clinic_id=clinic.id,
                appointment_type_id=type_id,
                appointment_date=appt_date,
                appointment_time=appt_time,
                reason=reason,
                secretary_note=secretary_note_text,
                doctor_note=doctor_note_text,
                status=Appointment.Status.CONFIRMED,
                created_by=request.user,
                ip=client_ip(request),
            )

            if fill_intake and intake_questions:
                save_intake_answers(
                    appointment, intake_questions, intake_answers, intake_files, request.user
                )

            messages.success(request, _("تم حجز موعد %(name)s بنجاح.") % {"name": patient.name})
            detail_url = reverse("secretary:appointment_overview", kwargs={"appointment_id": appointment.id})
            if post_return_to:
                detail_url += f"?return_to={post_return_to}"
            return redirect(detail_url)

        except (BookingError, SlotUnavailableError) as e:
            messages.error(request, e.message)
        except Exception as e:
            messages.error(request, _("حدث خطأ: %(error)s") % {"error": e})

    if prefill_doctor_id:
        from appointments.services.appointment_type_service import get_appointment_types_for_doctor_in_clinic
        appointment_types = get_appointment_types_for_doctor_in_clinic(prefill_doctor_id, clinic.id)
    else:
        appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)
    today_str = date.today().isoformat()

    # If a patient is pre-selected, surface any outstanding debt up front.
    prefill_patient_debt = None
    if prefill_patient_id:
        from secretary import billing
        try:
            prefill_patient_debt = billing.patient_debt(
                clinic, User.objects.get(id=prefill_patient_id)
            )
        except (User.DoesNotExist, ValueError):
            prefill_patient_debt = None

    return render(request, "secretary/appointments/create.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "appointment_types": appointment_types,
        "today_str": today_str,
        "prefill_date": prefill_date,
        "prefill_time": prefill_time,
        "prefill_patient_id": prefill_patient_id,
        "prefill_doctor_id": prefill_doctor_id,
        "return_to": return_to,
        "steps": [(_("المريض"), 1), (_("الموعد"), 2), (_("التأكيد"), 3)],
        "prefill_patient_debt": prefill_patient_debt,
    })


@secretary_required
def register_walk_in(request, staff):
    """
    Secretary registers a walk-in patient: today/now, status CHECKED_IN,
    is_walk_in=True. Patient is added to the waiting-room queue immediately.
    """

    clinic = staff.clinic
    from clinics.models import ClinicStaff as CS
    doctors_qs = (
        CS.objects.filter(clinic=clinic, role="DOCTOR", is_active=True)
        .select_related("user")
        .order_by("user__name")
    )
    doctor_users = [s.user for s in doctors_qs]
    valid_doctor_ids = {u.id for u in doctor_users}

    if request.method == "POST":
        from secretary.services import register_walk_in as svc_register_walk_in
        from appointments.services.booking_service import BookingError, SlotUnavailableError

        try:
            patient_id = request.POST.get("patient_id", "").strip()
            doctor_id = int(request.POST.get("doctor_id") or 0)
            type_id = int(request.POST.get("appointment_type_id") or 0)
            reason = request.POST.get("reason", "").strip()
            secretary_note_text = request.POST.get("secretary_note", "").strip()
            doctor_note_text = request.POST.get("doctor_note", "").strip()
            override_same_day = request.POST.get("override_same_day_conflict") == "1"

            if not patient_id:
                messages.error(request, _("يرجى اختيار المريض."))
                return redirect("secretary:register_walk_in")
            if not doctor_id or doctor_id not in valid_doctor_ids:
                messages.error(request, _("يرجى اختيار طبيب من العيادة."))
                return redirect("secretary:register_walk_in")
            if not type_id:
                messages.error(request, _("يرجى اختيار نوع الموعد."))
                return redirect("secretary:register_walk_in")

            try:
                patient = User.objects.get(id=patient_id)
            except User.DoesNotExist:
                messages.error(request, _("المريض المحدد غير موجود."))
                return redirect("secretary:register_walk_in")

            # Cross-tenant guard (see create_appointment): a walk-in can only be
            # registered for a patient already on THIS clinic's roster. The walk-in
            # form carries only patient_id (chosen from the clinic-scoped search),
            # so there is no strong-identifier to fall back on (q="").
            if not _patient_reachable_for_registration(clinic, patient, q=""):
                messages.error(request, _("هذا المريض غير مسجّل في هذه العيادة."))
                return redirect("secretary:register_walk_in")

            from patients.services import ensure_patient_profile
            ensure_patient_profile(patient)

            svc_register_walk_in(
                patient=patient,
                doctor_id=doctor_id,
                clinic_id=clinic.id,
                appointment_type_id=type_id,
                created_by=request.user,
                reason=reason,
                secretary_note=secretary_note_text,
                doctor_note=doctor_note_text,
                override_same_day_conflict=override_same_day,
                ip=client_ip(request),
            )

            messages.success(
                request,
                _("تم تسجيل وصول %(name)s (حضور مباشر) — أُضيف إلى طابور الانتظار.")
                % {"name": patient.name},
            )
            return redirect("secretary:waiting_room")

        except (BookingError, SlotUnavailableError) as e:
            messages.error(request, e.message)
        except Exception as e:
            messages.error(request, _("حدث خطأ: %(error)s") % {"error": e})

    appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    return render(request, "secretary/appointments/walk_in.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "appointment_types": appointment_types,
    })


@secretary_required
def edit_appointment(request, staff, appointment_id):
    """Secretary reschedules or updates an appointment."""

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=clinic)

    # Block editing of terminal or in-progress appointments (S-07/S-08)
    _NON_EDITABLE = {
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
        Appointment.Status.CHECKED_IN,
        Appointment.Status.IN_PROGRESS,
    }
    if appointment.status in _NON_EDITABLE:
        messages.error(request, _("لا يمكن تعديل هذا الموعد في حالته الحالية."))
        return redirect("secretary:appointments")

    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )
    from clinics.models import ClinicStaff as CS

    doctors_qs = (
        CS.objects.filter(clinic=clinic, role="DOCTOR", is_active=True)
        .select_related("user")
        .order_by("user__name")
    )
    doctor_users = [s.user for s in doctors_qs]
    valid_doctor_ids = {u.id for u in doctor_users}

    # Filter appointment types by what the current doctor offers in this clinic
    if appointment.doctor_id:
        appointment_types = get_appointment_types_for_doctor_in_clinic(
            appointment.doctor_id, clinic.id
        )
    else:
        appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    if request.method == "POST":
        try:
            new_doctor_id = int(request.POST.get("doctor_id") or 0)
            new_type_id = int(request.POST.get("appointment_type_id") or 0)
            new_date_str = request.POST.get("appointment_date", "").strip()
            new_time_str = request.POST.get("appointment_time", "").strip()
            new_reason = request.POST.get("reason", "").strip()

            if not all([new_doctor_id, new_type_id, new_date_str, new_time_str]):
                messages.error(request, _("يرجى ملء جميع الحقول المطلوبة."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            if new_doctor_id not in valid_doctor_ids:
                messages.error(request, _("الطبيب المحدد لا ينتمي إلى هذه العيادة."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            if appointment.patient_id and new_doctor_id == appointment.patient_id:
                messages.error(request, _("لا يمكن حجز الموعد للطبيب مع نفسه كمريض."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            from datetime import datetime as dt_cls
            new_date = dt_cls.strptime(new_date_str, "%Y-%m-%d").date()
            new_time = dt_cls.strptime(new_time_str, "%H:%M").time()

            # S-06: Prevent rescheduling to a past date
            if new_date < date.today():
                messages.error(request, _("لا يمكن جدولة موعد في تاريخ ماضٍ."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            # Validate type is enabled for the NEW doctor (S-03 equivalent for edit)
            allowed_types = get_appointment_types_for_doctor_in_clinic(new_doctor_id, clinic.id)
            enabled_type_ids = {t.id for t in allowed_types}
            if enabled_type_ids and new_type_id not in enabled_type_ids:
                messages.error(request, _("نوع الموعد المحدد غير متاح لهذا الطبيب."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            new_type = get_object_or_404(AppointmentType, id=new_type_id, clinic=clinic, is_active=True)

            doctor_changed = new_doctor_id != appointment.doctor_id
            date_or_time_changed = (
                new_date != appointment.appointment_date
                or new_time != appointment.appointment_time
            )
            key_fields_changed = doctor_changed or date_or_time_changed

            # S-05: Check for slot conflicts with other confirmed appointments for the
            # (possibly new) doctor whenever doctor, date, or time changed.
            if key_fields_changed:
                conflict = Appointment.objects.filter(
                    doctor_id=new_doctor_id,
                    appointment_date=new_date,
                    appointment_time=new_time,
                    status__in=[
                        Appointment.Status.CONFIRMED,
                        Appointment.Status.CHECKED_IN,
                        Appointment.Status.IN_PROGRESS,
                    ],
                ).exclude(pk=appointment.pk).exists()
                if conflict:
                    messages.error(request, _("هذا الوقت محجوز بالفعل لدى هذا الطبيب. يرجى اختيار وقت آخر."))
                    return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            old_date = appointment.appointment_date
            old_time = appointment.appointment_time
            old_doctor_id = appointment.doctor_id

            appointment.doctor_id = new_doctor_id
            appointment.appointment_type = new_type
            appointment.appointment_date = new_date
            appointment.appointment_time = new_time
            if new_reason:
                appointment.reason = new_reason
            appointment.save(update_fields=[
                "doctor", "appointment_type", "appointment_date",
                "appointment_time", "reason", "updated_at",
            ])

            log_activity(
                actor=request.user,
                clinic=clinic,
                action=ActivityLog.Action.APPOINTMENT_RESCHEDULED,
                target=appointment,
                request=request,
                metadata={
                    "old_doctor": old_doctor_id,
                    "new_doctor": new_doctor_id,
                    "old_date": old_date.isoformat() if old_date else None,
                    "new_date": new_date.isoformat(),
                    "old_time": old_time.strftime("%H:%M") if old_time else None,
                    "new_time": new_time.strftime("%H:%M"),
                },
            )

            # Notify patient if doctor, date, or time changed
            if key_fields_changed:
                from django.db import transaction as _txn
                from appointments.services.appointment_notification_service import (
                    notify_appointment_rescheduled_by_staff,
                )
                _txn.on_commit(
                    lambda: notify_appointment_rescheduled_by_staff(
                        appointment, old_date, old_time, clinic_staff=staff
                    )
                )

            messages.success(request, _("تم تحديث الموعد بنجاح."))
            return redirect("secretary:appointments")
        except Exception as e:
            messages.error(request, _("حدث خطأ: %(error)s") % {"error": e})

    today_str = date.today().isoformat()
    return render(request, "secretary/edit_appointment.html", {
        "clinic": clinic,
        "appointment": appointment,
        "appointment_types": appointment_types,
        "doctor_users": doctor_users,
        "today_str": today_str,
    })


@secretary_required
def cancel_appointment(request, staff, appointment_id):
    """Secretary cancels an appointment (with optional reason)."""

    is_htmx = request.headers.get("HX-Request") == "true"
    htmx_target_patient_id = request.POST.get("walkin_patient_id", "").strip()

    if request.method == "POST":
        from secretary.services import transition_appointment_status
        from appointments.services.booking_service import BookingError
        # Fetch outside the try so a cross-clinic id raises a clean Http404 instead
        # of being swallowed by the broad ``except Exception`` below (which would
        # otherwise return 302 / a 500 for HTMX on a non-existent-in-clinic id).
        appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)
        try:
            reason = request.POST.get("cancellation_reason", "").strip() or "إلغاء من قِبل السكرتارية"
            transition_appointment_status(
                appointment,
                Appointment.Status.CANCELLED,
                cancellation_reason=reason,
                actor=request.user,
                ip=client_ip(request),
            )
            if not is_htmx:
                messages.success(request, _("تم إلغاء الموعد بنجاح."))
        except BookingError as e:
            if is_htmx:
                return HttpResponse(e.message, status=400)
            messages.error(request, e.message)
        except Exception as e:
            if is_htmx:
                return HttpResponse(str(e), status=500)
            messages.error(request, _("حدث خطأ أثناء الإلغاء: %(error)s") % {"error": e})

    if is_htmx and htmx_target_patient_id:
        # Re-render the walk-in patient appointments partial so the list refreshes
        return _render_walkin_patient_appointments(request, staff, htmx_target_patient_id)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if _is_safe_next(request, next_url):
        return redirect(next_url)
    return redirect("secretary:appointments")


# ── Staff notes (secretary-authored notes on appointments / patient profiles) ──


def _bilingual(ar, en):
    """Pick AR/EN by the active language (avoids relying on compiled .po catalogs)."""
    from django.utils.translation import get_language
    lang = (get_language() or "ar").split("-")[0]
    return en if lang.startswith("en") else ar


def _safe_redirect(request, fallback):
    """Redirect to POSTed ``next`` only when it is a same-host target; else ``fallback``."""
    next_url = request.POST.get("next") or ""
    if _is_safe_next(request, next_url):
        return redirect(next_url)
    return redirect(fallback)


def _add_staff_note(request, staff, patient, appointment):
    """Validate + create a StaffNote, then queue its notification. Returns note or None.

    ``appointment`` is None for a patient-profile note. Audience comes from POST
    (DOCTOR or SECRETARY); body must be non-empty.
    """
    from appointments.services.appointment_notification_service import notify_staff_note

    audience = (request.POST.get("audience") or "").strip().upper()
    body = (request.POST.get("body") or "").strip()
    if audience not in (StaffNote.Audience.DOCTOR, StaffNote.Audience.SECRETARY):
        messages.error(request, _bilingual("نوع الملاحظة غير صالح.", "Invalid note type."))
        return None
    if not body:
        messages.error(request, _bilingual("لا يمكن إضافة ملاحظة فارغة.", "Cannot add an empty note."))
        return None

    note = StaffNote.objects.create(
        clinic=staff.clinic,
        patient=patient,
        appointment=appointment,
        audience=audience,
        body=body,
        author=request.user,
        author_name=request.user.name,
        author_role="SECRETARY",
    )
    actor = request.user
    transaction.on_commit(lambda: notify_staff_note(note, actor))
    messages.success(request, _bilingual("تمت إضافة الملاحظة.", "Note added."))
    return note


@secretary_required
@require_POST
def appointment_note_add(request, staff, appointment_id):
    """Secretary adds a note (for the doctor or secretaries-only) to an appointment."""
    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "doctor"),
        id=appointment_id, clinic=staff.clinic,
    )
    _add_staff_note(request, staff, appointment.patient, appointment)
    return _safe_redirect(
        request,
        reverse("secretary:appointment_overview", args=[appointment.id]) + "#staff-notes",
    )


@secretary_required
@require_POST
def appointment_note_delete(request, staff, appointment_id, note_id):
    """Secretary deletes her OWN appointment note.

    Scoped to secretary-visible notes (never a doctor's private note, so no existence
    oracle); deletion is allowed only for notes authored from the secretary portal."""
    note = get_object_or_404(
        StaffNote.objects.exclude(audience=StaffNote.Audience.DOCTOR_PRIVATE),
        id=note_id, appointment_id=appointment_id, clinic=staff.clinic,
    )
    if not note.can_delete(request.user, "SECRETARY"):
        return HttpResponseForbidden("لا يمكنك حذف ملاحظة كتبها شخص آخر.")
    note.delete()
    messages.success(request, _bilingual("تم حذف الملاحظة.", "Note deleted."))
    return _safe_redirect(
        request,
        reverse("secretary:appointment_overview", args=[appointment_id]) + "#staff-notes",
    )


@secretary_required
@require_POST
def patient_note_add(request, staff, patient_id):
    """Secretary adds a note (for the doctor or secretaries-only) to a patient profile."""
    patient = get_object_or_404(User, id=patient_id)
    get_object_or_404(ClinicPatient, clinic=staff.clinic, patient=patient)
    _add_staff_note(request, staff, patient, None)
    return _safe_redirect(request, reverse("secretary:patient_detail", args=[patient_id]))


@secretary_required
@require_POST
def patient_note_delete(request, staff, patient_id, note_id):
    """Secretary deletes her OWN patient-profile note.

    Scoped to secretary-visible notes (never a doctor's private note); deletion is
    allowed only for notes authored from the secretary portal."""
    note = get_object_or_404(
        StaffNote.objects.exclude(audience=StaffNote.Audience.DOCTOR_PRIVATE),
        id=note_id, patient_id=patient_id, clinic=staff.clinic,
        appointment__isnull=True,
    )
    if not note.can_delete(request.user, "SECRETARY"):
        return HttpResponseForbidden("لا يمكنك حذف ملاحظة كتبها شخص آخر.")
    note.delete()
    messages.success(request, _bilingual("تم حذف الملاحظة.", "Note deleted."))
    return _safe_redirect(request, reverse("secretary:patient_detail", args=[patient_id]))


def _render_walkin_patient_appointments(request, staff, patient_id):
    """Helper: render the walk-in future-appointments partial for a given patient."""
    from secretary.services import get_patient_future_appointments

    try:
        patient = User.objects.get(id=patient_id)
    except (User.DoesNotExist, ValueError):
        return HttpResponse("")

    # Cross-tenant guard: only surface a patient registered in this clinic. A
    # non-clinic patient has no appointments here anyway, so returning empty
    # avoids echoing their name (PII) for an arbitrary global user id.
    if not ClinicPatient.objects.filter(clinic=staff.clinic, patient=patient).exists():
        return HttpResponse("")

    future_appts = list(get_patient_future_appointments(patient=patient, clinic=staff.clinic))
    today = date.today()
    today_appts = [a for a in future_appts if a.appointment_date == today]
    later_appts = [a for a in future_appts if a.appointment_date > today]

    return render(request, "secretary/htmx/walkin_patient_appointments.html", {
        "patient": patient,
        "today_appts": today_appts,
        "later_appts": later_appts,
        "has_today_conflict": bool(today_appts),
    })


@secretary_required
def patient_walkin_appointments_htmx(request, staff):
    """HTMX endpoint: list a patient's future appointments for the walk-in flow."""

    patient_id = request.GET.get("patient_id", "").strip()
    if not patient_id:
        return HttpResponse("")
    return _render_walkin_patient_appointments(request, staff, patient_id)


# ============================================
# SECRETARY INVITATIONS FLOW
# ============================================

from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from clinics.models import ClinicInvitation
from clinics.services import accept_invitation, reject_invitation
from accounts.backends import PhoneNumberAuthBackend


@login_required
def secretary_invitations_inbox(request):
    """View pending invitations for the logged-in secretary."""
    user = request.user
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    
    invitations = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, 
        role="SECRETARY",
        status="PENDING"
    ).select_related('clinic', 'invited_by').order_by('-created_at')
    
    return render(request, "secretary/invitations_inbox.html", {
        "invitations": invitations,
    })


@login_required
def accept_invitation_view(request, invitation_id):
    """Action to accept a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")

    # Verify this invitation belongs to the logged-in user's phone (prevents IDOR)
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    if normalized_phone != invitation.doctor_phone:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة."
        })

    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, _("تم الانضمام بنجاح إلى عيادة %(clinic)s بصفة سكرتير/ة.") % {"clinic": staff.clinic.name})
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, _("خطأ: %(error)s") % {"error": err_msg})

    return redirect(reverse("secretary:secretary_invitations_inbox"))


@login_required
def reject_invitation_view(request, invitation_id):
    """Action to reject a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")

    # Verify this invitation belongs to the logged-in user's phone (prevents IDOR)
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    if normalized_phone != invitation.doctor_phone:
        return render(request, "secretary/invalid_invitation.html", {
            "error": _("لا تملك الصلاحية للوصول إلى هذه الدعوة.")
        })

    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, _("تم رفض الدعوة."))
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, _("خطأ: %(error)s") % {"error": err_msg})

    return redirect(reverse("secretary:secretary_invitations_inbox"))


def guest_accept_invitation_view(request, token):
    """
    Public endpoint for SMS link. 
    Shows generic error if token invalid/expired.
    If valid, redirects to login/reg storing token in session.
    """
    try:
        invitation = ClinicInvitation.objects.get(token=token, role="SECRETARY")
    except ClinicInvitation.DoesNotExist:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "رابط الدعوة غير صالح أو قد تم استخدامه مسبقاً."
        })
        
    if invitation.status != "PENDING" or invitation.is_expired:
         return render(request, "secretary/invalid_invitation.html", {
            "error": "انتهت صلاحية هذه الدعوة أو لم تعد متاحة."
        })
        
    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
             # Already logged in as the right user, redirect to inbox to accept
             return redirect(reverse("secretary:secretary_invitations_inbox"))
        else:
             # Logged in as someone else (wrong phone)
             return render(request, "secretary/invalid_invitation.html", {
                "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة. يرجى تسجيل الدخول بالحساب الصحيح."
            })
            
    # Unauthenticated but token is valid: store generic next url and redirect to login
    request.session["pending_invitation_token"] = str(token)  # UUID must be str for JSON session
    request.session["pending_invitation_app"] = "secretary"
    
    messages.info(request, _("يرجى تسجيل الدخول أو إنشاء حساب جديد لقبول دعوة الانضمام للعيادة كـ سكرتير/ة."))
    return redirect(reverse("accounts:login"))


# ============================================
# PATIENT REGISTRATION FLOW
# ============================================

def _is_patient_user(user):
    return user.role == "PATIENT" or "PATIENT" in (user.roles or [])


def _compute_age(date_of_birth):
    if not date_of_birth:
        return None
    today = date.today()
    return (
        today.year
        - date_of_birth.year
        - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    )


@secretary_required
def register_patient(request, staff):
    """Secretary patient registration landing page."""

    clinic = staff.clinic
    recently_registered = (
        ClinicPatient.objects.filter(clinic=clinic)
        .select_related("patient", "registered_by")
        .order_by("-registered_at")[:5]
    )
    return render(request, "secretary/register_patient.html", {
        "clinic": clinic,
        "recently_registered": recently_registered,
    })


def _strong_identifier_q(q):
    """Q matching a patient by an *exact* strong identifier (phone or national id).

    These are the only identifiers that grant a secretary cross-clinic reach when
    registering an existing patient. Single source of truth shared by
    ``patient_search_htmx`` (queryset filter) and
    ``_patient_reachable_for_registration`` (per-patient re-check).
    """
    normalized_q = PhoneNumberAuthBackend.normalize_phone_number(q)
    return Q(phone=normalized_q) | Q(national_id__iexact=q)


def _patient_reachable_for_registration(clinic, patient, q=""):
    """Whether a secretary may legitimately load this patient's PII card.

    True when the patient is already registered in ``clinic`` (an existing patient
    the secretary owns), OR when ``q`` is an exact strong-identifier match for the
    patient — the only way a not-yet-registered global patient can surface via
    search. This closes id-based enumeration of the global patient directory
    through the card / walk-in lookup endpoints (see ``patient_search_htmx`` for
    the matching search-side guard).
    """
    if ClinicPatient.objects.filter(clinic=clinic, patient=patient).exists():
        return True
    q = (q or "").strip()
    if len(q) < 2:
        return False
    return User.objects.filter(_strong_identifier_q(q), pk=patient.pk).exists()


@secretary_required
def patient_search_htmx(request, staff):
    """HTMX endpoint: find a patient to register.

    Cross-tenant fishing guard: a *global* match (any patient in the system) is
    returned ONLY on an exact phone or national-id — the strong identifiers a
    secretary legitimately has when registering an existing patient. Partial
    matches (name, or partial phone/id) are restricted to THIS clinic's own
    roster, so a secretary can't enumerate the global patient directory by name.
    Every lookup is audit-logged (who/when/how-many — never the searched value).
    """

    clinic = staff.clinic
    q = request.GET.get("q", "").strip()
    patients = []

    if len(q) >= 2:
        normalized_q = PhoneNumberAuthBackend.normalize_phone_number(q)
        patient_role = Q(role="PATIENT") | Q(roles__contains=["PATIENT"])
        # Global reach: exact phone OR exact national id only.
        strong_match = _strong_identifier_q(q)
        # Broad reach (name / partial): only within this clinic's registered patients.
        clinic_match = Q(clinic_registrations__clinic=clinic) & (
            Q(name__icontains=q)
            | Q(phone__icontains=normalized_q)
            | Q(national_id__icontains=q)
        )
        patients = list(
            User.objects.filter(patient_role)
            .filter(strong_match | clinic_match)
            .distinct()
            .select_related("patient_profile")
            .order_by("name")[:10]
        )
        logger.info(
            "secretary_patient_search user=%s clinic=%s q_len=%s results=%s",
            request.user.id, clinic.id, len(q), len(patients),
        )

    return render(request, "secretary/htmx/patient_search_results.html", {
        "patients": patients,
        "query": q,
        "clinic_id": staff.clinic_id,
    })


@secretary_required
def patient_detail_htmx(request, staff, patient_id):
    """HTMX endpoint: load patient summary card + registration form."""

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)

    if not _is_patient_user(patient):
        return HttpResponse(
            '<p class="text-red-500 text-sm p-4">المستخدم المحدد ليس مريضاً.</p>',
            status=400,
        )

    # Cross-tenant guard: only surface PII for a patient the secretary could
    # legitimately reach — already registered here, or matched by an exact strong
    # identifier (the forwarded search query). Without this, /patients/<id>/card/
    # would let a secretary enumerate user ids to harvest names/DOBs system-wide,
    # bypassing the guard in patient_search_htmx.
    if not _patient_reachable_for_registration(clinic, patient, request.GET.get("q", "")):
        raise Http404("Patient not reachable from this clinic")

    profile = getattr(patient, "patient_profile", None)
    already_registered = ClinicPatient.objects.filter(
        clinic=clinic, patient=patient
    ).exists()
    age = _compute_age(profile.date_of_birth if profile else None)
    # Surface only WHETHER the patient is registered at any other clinic — never the
    # other clinics' names. Returning clinic names here would let a secretary enumerate
    # user ids and learn which clinics a patient attends elsewhere (cross-tenant leak).
    registered_elsewhere = (
        ClinicPatient.objects.filter(patient=patient)
        .exclude(clinic=clinic)
        .exists()
    )

    return render(request, "secretary/htmx/patient_card.html", {
        "patient": patient,
        "profile": profile,
        "age": age,
        "already_registered": already_registered,
        "clinic": clinic,
        "registered_elsewhere": registered_elsewhere,
    })


@secretary_required
def register_patient_submit(request, staff):
    """POST: register a patient in the secretary's clinic, optionally filling profile gaps."""

    if request.method != "POST":
        return redirect("secretary:register_patient")

    clinic = staff.clinic
    patient_id = request.POST.get("patient_id", "").strip()
    if not patient_id:
        messages.error(request, _("لم يتم تحديد مريض."))
        return redirect("secretary:register_patient")

    patient = get_object_or_404(User, id=patient_id)

    if not _is_patient_user(patient):
        messages.error(request, _("المستخدم المحدد ليس مريضاً."))
        return redirect("secretary:register_patient")

    if ClinicPatient.objects.filter(clinic=clinic, patient=patient).exists():
        messages.warning(request, _("المريض %(name)s مسجل بالفعل في هذه العيادة.") % {"name": patient.name})
        return redirect("secretary:register_patient")

    # --- Fill gaps in PatientProfile (never overwrite non-blank existing values) ---
    profile, _ = PatientProfile.objects.get_or_create(user=patient)
    profile_dirty = []
    user_dirty = []

    dob_str = request.POST.get("date_of_birth", "").strip()
    if dob_str and not profile.date_of_birth:
        from datetime import datetime as _dt
        try:
            profile.date_of_birth = _dt.strptime(dob_str, "%Y-%m-%d").date()
            profile_dirty.append("date_of_birth")
        except ValueError:
            pass

    gender = request.POST.get("gender", "").strip()
    if gender and not profile.gender:
        profile.gender = gender
        profile_dirty.append("gender")

    national_id = request.POST.get("national_id", "").strip()
    if national_id and not patient.national_id:
        patient.national_id = national_id
        user_dirty.append("national_id")

    if user_dirty:
        patient.save(update_fields=user_dirty)
    if profile_dirty:
        profile.save(update_fields=profile_dirty)

    # --- Register ---
    ClinicPatient.objects.create(
        clinic=clinic,
        patient=patient,
        registered_by=request.user,
        notes=request.POST.get("notes", "").strip(),
    )
    log_activity(
        actor=request.user,
        clinic=clinic,
        action=ActivityLog.Action.PATIENT_REGISTERED,
        target=patient,
        request=request,
        metadata={"new_user": False},
    )

    messages.success(request, _("تم تسجيل المريض %(name)s في عيادة %(clinic)s بنجاح.") % {"name": patient.name, "clinic": clinic.name})
    return redirect("secretary:register_patient")