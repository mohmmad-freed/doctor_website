from datetime import date, datetime, timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from appointments.models import Appointment, AppointmentType
from patients.models import ClinicPatient, PatientProfile

User = get_user_model()


def _require_secretary(request):
    """Return the secretary's ClinicStaff record, or None if not a secretary."""
    from clinics.models import ClinicStaff
    return ClinicStaff.objects.filter(
        user=request.user, role="SECRETARY", is_active=True
    ).select_related("clinic").first()


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


@login_required
def dashboard(request):
    """Secretary daily overview: today's appointments, stats, and live status panels."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
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

    # Revenue today from payments — optional billing module
    revenue_today = None
    try:
        from secretary.models import Payment
        result = Payment.objects.filter(
            clinic=clinic, received_at__date=today
        ).aggregate(total=Sum("amount"))
        revenue_today = result["total"] or 0
    except ImportError:
        pass  # Billing module not installed

    # Recent activity (appointment notifications for this clinic)
    recent_activity = []
    try:
        from appointments.models import AppointmentNotification
        recent_activity = list(
            AppointmentNotification.objects.filter(
                appointment__clinic=clinic
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

    return render(request, "secretary/dashboard.html", {
        "clinic": clinic,
        "todays_appointments": todays_appointments,
        "today": today,
        "stat_total": stat_total,
        "stat_pending": stat_pending,
        "stat_checked_in": stat_checked_in,
        "stat_in_progress": stat_in_progress,
        "stat_completed": stat_completed,
        "stat_cancelled": stat_cancelled,
        "waiting_count": waiting_count,
        "upcoming_2h": upcoming_2h,
        "revenue_today": revenue_today,
        "recent_activity": recent_activity,
        "terminal_statuses": terminal_statuses,
        "unread_secretary_notification_count": unread_secretary_notification_count,
    })


@login_required
def doctor_status_htmx(request):
    """HTMX endpoint: returns the doctor status cards partial (auto-refreshes every 60s)."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    doctor_statuses = _get_doctor_statuses(staff.clinic)
    return render(request, "secretary/htmx/doctor_status_cards.html", {
        "doctor_statuses": doctor_statuses,
    })


@login_required
@require_POST
def checkin_appointment(request, appointment_id):
    """Mark a CONFIRMED appointment as CHECKED_IN and set checked_in_at timestamp."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)

    if appointment.status == Appointment.Status.CONFIRMED:
        appointment.status = Appointment.Status.CHECKED_IN
        appointment.checked_in_at = timezone.now()
        appointment.save(update_fields=["status", "checked_in_at", "updated_at"])
        messages.success(request, _("تم تسجيل وصول %(name)s بنجاح.") % {"name": appointment.patient.name})
    else:
        messages.warning(request, _("لا يمكن تسجيل الوصول إلا للمواعيد المؤكدة."))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "secretary:dashboard"
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("secretary:dashboard")


@login_required
def appointment_detail(request, appointment_id):
    """View appointment details."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "doctor", "appointment_type", "patient__patient_profile"),
        id=appointment_id, clinic=clinic
    )
    profile = getattr(appointment.patient, "patient_profile", None)
    clinic_patient = ClinicPatient.objects.filter(clinic=clinic, patient=appointment.patient).first()

    from secretary.services import get_valid_transitions

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]
    status_steps = ["PENDING", "CONFIRMED", "CHECKED_IN", "IN_PROGRESS", "COMPLETED"]
    try:
        current_step_index = status_steps.index(appointment.status)
    except ValueError:
        current_step_index = 0
    return render(request, "secretary/appointment_detail.html", {
        "clinic": clinic,
        "appointment": appointment,
        "profile": profile,
        "clinic_patient": clinic_patient,
        "terminal_statuses": terminal_statuses,
        "valid_transitions": get_valid_transitions(appointment.status),
        "status_steps": status_steps,
        "current_step_index": current_step_index,
    })


# ── Stub views for unimplemented modules ─────────────────────────────────────

@login_required
def waiting_room(request):
    """Secretary waiting room board — two-column live queue management."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    from clinics.models import ClinicStaff as CS
    clinic = staff.clinic
    today = date.today()

    doctor_filter = request.GET.get("doctor_id", "")

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
        .order_by("checked_in_at")
    )

    if doctor_filter:
        confirmed_qs = confirmed_qs.filter(doctor_id=doctor_filter)
        checkedin_qs = checkedin_qs.filter(doctor_id=doctor_filter)

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

    return render(request, "secretary/waiting_room/board.html", {
        "clinic": clinic,
        "today": today,
        "confirmed_list": list(confirmed_qs),
        "checkedin_list": checkedin_list,
        "doctors": doctors,
        "doctor_filter": doctor_filter,
        "total_waiting": total_waiting,
        "avg_wait": avg_wait,
    })


def waiting_room_display(request):
    """
    TV/kiosk display mode — no auth required so it can run on a lobby screen.
    Shows CHECKED_IN and IN_PROGRESS appointments for today.
    Auto-refreshes via <meta http-equiv='refresh' content='20'>.
    """
    from clinics.models import Clinic as ClinicModel
    clinic_id = request.GET.get("clinic_id")
    if not clinic_id:
        return HttpResponse("يرجى تحديد معرّف العيادة في الرابط: ?clinic_id=X", status=400)

    try:
        clinic = ClinicModel.objects.get(id=clinic_id, is_active=True)
    except ClinicModel.DoesNotExist:
        return HttpResponse("العيادة غير موجودة أو غير نشطة.", status=404)

    today = date.today()
    now = timezone.now()

    queue = (
        Appointment.objects.filter(
            clinic=clinic,
            appointment_date=today,
            status__in=[Appointment.Status.CHECKED_IN, Appointment.Status.IN_PROGRESS],
        )
        .select_related("patient", "doctor")
        .order_by("checked_in_at")
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
            display_name = f"{name_parts[0]} {name_parts[1][0]}."
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

    return render(request, "secretary/waiting_room/display.html", {
        "clinic": clinic,
        "queue_entries": queue_entries,
        "today": today,
        "now": now,
    })


@login_required
def waiting_room_confirmed_htmx(request):
    """HTMX polling endpoint — refreshes the CONFIRMED column every 30s."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    clinic = staff.clinic
    today = date.today()
    doctor_filter = request.GET.get("doctor_id", "")

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

    return render(request, "secretary/htmx/waiting_room_confirmed_rows.html", {
        "confirmed_list": list(qs),
        "clinic": clinic,
    })


@login_required
def waiting_room_checkedin_htmx(request):
    """HTMX polling endpoint — refreshes the CHECKED_IN column every 30s."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    clinic = staff.clinic
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
        .order_by("checked_in_at")
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

    return render(request, "secretary/htmx/waiting_room_checkedin_rows.html", {
        "checkedin_list": checkedin_list,
        "clinic": clinic,
    })


@login_required
def checkin_search(request):
    """
    Dedicated check-in search page: secretary searches for a patient,
    sees today's appointments, and checks them in with one click.
    """
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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


@login_required
def calendar_view(request):
    """Calendar view — FullCalendar v6 with HTMX data feed."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    from clinics.models import ClinicStaff as CS
    clinic = staff.clinic
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctor_users = [s.user for s in doctor_staff]

    return render(request, "secretary/appointments/calendar.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "status_legend": [
            ("قيد الانتظار", "PENDING", "#d97706"),
            ("مؤكد", "CONFIRMED", "#10b981"),
            ("وصل", "CHECKED_IN", "#3b82f6"),
            ("جارٍ", "IN_PROGRESS", "#8b5cf6"),
            ("مكتمل", "COMPLETED", "#6b7280"),
            ("ملغى", "CANCELLED", "#ef4444"),
        ],
    })


@login_required
def billing_invoices(request):
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")
    return render(request, "secretary/coming_soon.html", {"title": "الفواتير", "clinic": staff.clinic})


@login_required
def daily_summary(request):
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")
    return render(request, "secretary/coming_soon.html", {"title": "الملخص اليومي", "clinic": staff.clinic})


@login_required
def reports_index(request):
    """Reports hub — quick stats + links to each sub-report."""
    from django.db.models import Count

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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


@login_required
def report_daily(request):
    """Daily appointments report. Supports ?export=csv."""
    from django.db.models import Count, Sum
    import csv as csv_module

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
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
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="daily_report_{report_date}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["الوقت", "المريض", "الطبيب", "الخدمة", "الحالة", "السعر"])
        for appt in appointments:
            writer.writerow([
                appt.appointment_time.strftime("%H:%M"),
                appt.patient.name,
                appt.doctor.name if appt.doctor else "",
                appt.appointment_type.display_name if appt.appointment_type else "",
                appt.get_status_display(),
                str(appt.appointment_type.price) if appt.appointment_type else "",
            ])
        return response

    return render(request, "secretary/reports/daily.html", {
        "clinic": clinic,
        "report_date": report_date,
        "today": today,
        "appointments": appointments,
        "total": total,
        "status_breakdown": status_breakdown,
        "doctor_stats": list(doctor_stats.values()),
    })


@login_required
def report_visits(request):
    """Patient visits report with date range + doctor filter. Supports ?export=csv."""
    from django.db.models import Count
    import csv as csv_module

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    today = date.today()
    default_from = (today - timedelta(days=29)).isoformat()

    date_from_str = request.GET.get("date_from", default_from)
    date_to_str = request.GET.get("date_to", today.isoformat())
    doctor_filter = request.GET.get("doctor_id", "")

    try:
        date_from = date.fromisoformat(date_from_str)
    except ValueError:
        date_from = today - timedelta(days=29)
    try:
        date_to = date.fromisoformat(date_to_str)
    except ValueError:
        date_to = today

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
    if doctor_filter:
        qs = qs.filter(doctor_id=doctor_filter)

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

    # Doctors for filter dropdown
    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # CSV export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="visits_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["المريض", "تاريخ الزيارة", "الوقت", "الطبيب", "الخدمة", "الحالة"])
        for appt in appointments:
            writer.writerow([
                appt.patient.name,
                appt.appointment_date.strftime("%Y/%m/%d"),
                appt.appointment_time.strftime("%H:%M"),
                appt.doctor.name if appt.doctor else "",
                appt.appointment_type.display_name if appt.appointment_type else "",
                appt.get_status_display(),
            ])
        return response

    return render(request, "secretary/reports/visits.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "doctor_filter": doctor_filter,
        "appointments": appointments,
        "total": len(appointments),
        "unique_patients": len(all_patient_ids),
        "new_patients": new_patients,
        "returning_patients": returning_patients,
        "doctors": doctors,
    })


@login_required
def report_noshows(request):
    """No-show & cancellation report. Supports ?export=csv."""
    from django.db.models import Count
    import csv as csv_module

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    today = date.today()
    default_from = (today - timedelta(days=29)).isoformat()

    date_from_str = request.GET.get("date_from", default_from)
    date_to_str = request.GET.get("date_to", today.isoformat())
    doctor_filter = request.GET.get("doctor_id", "")

    try:
        date_from = date.fromisoformat(date_from_str)
    except ValueError:
        date_from = today - timedelta(days=29)
    try:
        date_to = date.fromisoformat(date_to_str)
    except ValueError:
        date_to = today

    base_qs = Appointment.objects.filter(
        clinic=clinic,
        appointment_date__range=(date_from, date_to),
    )
    if doctor_filter:
        base_qs = base_qs.filter(doctor_id=doctor_filter)

    total = base_qs.count()
    noshows_qs = base_qs.filter(status=Appointment.Status.NO_SHOW).select_related("patient", "doctor", "appointment_type").order_by("-appointment_date")
    cancelled_qs = base_qs.filter(status=Appointment.Status.CANCELLED).select_related("patient", "doctor", "appointment_type").order_by("-appointment_date")

    noshow_count = noshows_qs.count()
    cancelled_count = cancelled_qs.count()
    noshow_rate = round(noshow_count / total * 100, 1) if total > 0 else 0
    cancel_rate = round(cancelled_count / total * 100, 1) if total > 0 else 0

    # Top no-show patients
    top_noshows = (
        base_qs.filter(status=Appointment.Status.NO_SHOW)
        .values("patient__id", "patient__name", "patient__phone")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    # Day-of-week breakdown (0=Mon … 6=Sun)
    DOW_NAMES = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    dow_counts = {i: {"name": DOW_NAMES[i], "noshows": 0, "cancelled": 0} for i in range(7)}
    for appt in noshows_qs:
        dow_counts[appt.appointment_date.weekday()]["noshows"] += 1
    for appt in cancelled_qs:
        dow_counts[appt.appointment_date.weekday()]["cancelled"] += 1
    dow_breakdown = list(dow_counts.values())
    max_dow = max((d["noshows"] + d["cancelled"]) for d in dow_breakdown) or 1

    # Doctors for filter
    from clinics.models import ClinicStaff as CS
    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    # CSV export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="noshows_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["النوع", "المريض", "الهاتف", "التاريخ", "الطبيب", "الخدمة", "السبب"])
        for appt in noshows_qs:
            writer.writerow(["لم يحضر", appt.patient.name, appt.patient.phone,
                              appt.appointment_date.strftime("%Y/%m/%d"),
                              appt.doctor.name if appt.doctor else "",
                              appt.appointment_type.display_name if appt.appointment_type else "", ""])
        for appt in cancelled_qs:
            writer.writerow(["ملغى", appt.patient.name, appt.patient.phone,
                              appt.appointment_date.strftime("%Y/%m/%d"),
                              appt.doctor.name if appt.doctor else "",
                              appt.appointment_type.display_name if appt.appointment_type else "",
                              appt.cancellation_reason])
        return response

    return render(request, "secretary/reports/noshows.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "doctor_filter": doctor_filter,
        "doctors": doctors,
        "total": total,
        "noshow_count": noshow_count,
        "cancelled_count": cancelled_count,
        "noshow_rate": noshow_rate,
        "cancel_rate": cancel_rate,
        "noshows": list(noshows_qs),
        "cancellations": list(cancelled_qs),
        "top_noshows": top_noshows,
        "dow_breakdown": dow_breakdown,
        "max_dow": max_dow,
    })


@login_required
def report_doctors(request):
    """Doctor utilization report. Supports ?export=csv."""
    from django.db.models import Count
    from doctors.models import DoctorAvailability
    import csv as csv_module

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    today = date.today()
    month_start = today.replace(day=1)

    date_from_str = request.GET.get("date_from", month_start.isoformat())
    date_to_str = request.GET.get("date_to", today.isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
    except ValueError:
        date_from = month_start
    try:
        date_to = date.fromisoformat(date_to_str)
    except ValueError:
        date_to = today

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

        # Most common appointment type
        top_type = (
            appt_qs.filter(appointment_type__isnull=False)
            .values("appointment_type__name", "appointment_type__name_ar")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
            .first()
        )
        top_type_name = (top_type["appointment_type__name_ar"] or top_type["appointment_type__name"]) if top_type else "—"

        doctor_rows.append({
            "doctor": doctor,
            "scheduled_sessions": scheduled_sessions,
            "total_booked": total_booked,
            "completed": completed,
            "noshows": noshows,
            "cancelled": cancelled,
            "utilization": utilization,
            "avg_daily": avg_daily,
            "top_type": top_type_name,
        })

    # Sort by total_booked desc for chart rendering
    doctor_rows.sort(key=lambda r: r["total_booked"], reverse=True)
    max_booked = max((r["total_booked"] for r in doctor_rows), default=1) or 1

    # CSV export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="doctors_{date_from}_{date_to}.csv"'
        writer = csv_module.writer(response)
        writer.writerow(["الطبيب", "الجلسات المجدولة", "المواعيد المحجوزة",
                          "مكتملة", "لم يحضر", "ملغاة", "نسبة الاستخدام %", "متوسط يومي"])
        for row in doctor_rows:
            writer.writerow([
                row["doctor"].name, row["scheduled_sessions"], row["total_booked"],
                row["completed"], row["noshows"], row["cancelled"],
                row["utilization"], row["avg_daily"],
            ])
        return response

    return render(request, "secretary/reports/doctors.html", {
        "clinic": clinic,
        "today": today,
        "date_from": date_from,
        "date_to": date_to,
        "doctor_rows": doctor_rows,
        "max_booked": max_booked,
    })


@login_required
def doctor_schedule(request):
    """
    Weekly schedule view for all clinic doctors.
    Shows DoctorAvailability (recurring) and DoctorAvailabilityException (blocks) per doctor.
    Supports week navigation (prev/next) and doctor filter via GET params.
    Secretary can read the full schedule and add/remove blocks.
    """
    from clinics.models import ClinicStaff as CS, DoctorAvailabilityException
    from doctors.models import DoctorAvailability
    from django.db.models import Count

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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


@login_required
def block_doctor_time(request):
    """
    Create a DoctorAvailabilityException: block a doctor for a date range.
    Secretary can add blocks. Warns if active appointments exist in the range.
    """
    from clinics.models import ClinicStaff as CS, DoctorAvailabilityException
    from django.core.exceptions import ValidationError as DjangoValidationError

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    today = date.today()

    doctor_staff = CS.objects.filter(
        clinic=clinic, role__in=["DOCTOR"], is_active=True
    ).select_related("user")
    doctors = [s.user for s in doctor_staff]

    REASON_CHOICES = [
        ("إجازة سنوية", "إجازة سنوية"),
        ("إجازة مرضية", "إجازة مرضية"),
        ("مؤتمر / تدريب", "مؤتمر / تدريب"),
        ("اجتماع", "اجتماع"),
        ("غياب طارئ", "غياب طارئ"),
        ("أخرى", "أخرى"),
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
        final_reason = custom_reason if reason == "أخرى" and custom_reason else reason

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


@login_required
@require_POST
def delete_doctor_block(request, exception_id):
    """Deactivate (soft-delete) a DoctorAvailabilityException."""
    from clinics.models import DoctorAvailabilityException

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

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
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("secretary:doctor_schedule")


@login_required
def settings_profile(request):
    """Secretary settings & profile page.
    Handles two POST actions:
      - action=profile  → update name, email, city
      - action=password → change password (requires current_password)
    Preferences (calendar default, appointment duration) are stored in localStorage
    and never hit the server.
    """
    from accounts.models import City

    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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

        # ── Password change ──────────────────────────────────────────
        elif action == "password":
            current_pw = request.POST.get("current_password", "")
            new_pw = request.POST.get("new_password", "").strip()
            confirm_pw = request.POST.get("confirm_password", "").strip()

            if not current_pw:
                password_errors["current_password"] = "أدخل كلمة المرور الحالية."
            elif not user.check_password(current_pw):
                password_errors["current_password"] = "كلمة المرور الحالية غير صحيحة."

            if not new_pw:
                password_errors["new_password"] = "أدخل كلمة المرور الجديدة."
            elif len(new_pw) < 8:
                password_errors["new_password"] = "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
            elif new_pw != confirm_pw:
                password_errors["confirm_password"] = "كلمتا المرور غير متطابقتين."

            if not password_errors:
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


# ── New appointment module views ──────────────────────────────────────────────

@login_required
@require_POST
def update_appointment_status(request, appointment_id):
    """
    HTMX endpoint: update appointment status with validation.
    Returns the updated status badge HTML partial.
    """
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    from secretary.services import transition_appointment_status, get_valid_transitions
    from appointments.services.booking_service import BookingError

    appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)
    new_status = request.POST.get("status", "").strip()
    cancellation_reason = request.POST.get("cancellation_reason", "").strip()

    error = None
    if not new_status:
        error = "لم يتم تحديد الحالة الجديدة."
    else:
        try:
            appointment = transition_appointment_status(
                appointment, new_status, cancellation_reason=cancellation_reason, actor=request.user
            )
        except BookingError as e:
            error = e.message
        except Exception as e:
            error = str(e)

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


@login_required
def get_time_slots_htmx(request):
    """
    HTMX endpoint: return available time slots for a doctor on a date.
    GET params: doctor_id, date (YYYY-MM-DD), appointment_type_id
    """
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    from datetime import datetime as _dt
    from doctors.services import generate_slots_for_date

    doctor_id = request.GET.get("doctor_id", "")
    date_str = request.GET.get("appointment_date", "")
    type_id = request.GET.get("appointment_type_id", "")

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
                slots = generate_slots_for_date(
                    doctor_id=int(doctor_id),
                    clinic_id=staff.clinic.id,
                    target_date=target_date,
                    duration_minutes=duration,
                )
            else:
                error = "نوع الموعد غير موجود."
        except ValueError:
            error = "تاريخ غير صالح."
        except Exception as e:
            error = str(e)

    selected_time = request.GET.get("selected_time", "")
    return render(request, "secretary/htmx/time_slots.html", {
        "slots": slots,
        "error": error,
        "duration": duration,
        "selected_time": selected_time,
    })


@login_required
def get_doctor_types_htmx(request):
    """HTMX endpoint: return appointment types for a specific doctor in this clinic."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

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


@login_required
def doctor_working_days_json(request):
    """
    JSON endpoint: returns the weekdays (Python weekday: 0=Mon..6=Sun) on which
    the selected doctor has at least one active availability block in this clinic.
    """
    staff = _require_secretary(request)
    if not staff:
        return JsonResponse({"error": "Forbidden"}, status=403)

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


@login_required
def appointments_json(request):
    """
    JSON feed for FullCalendar.
    GET params: start (ISO date), end (ISO date), doctor_id (optional)
    Returns list of FullCalendar event objects.
    """
    staff = _require_secretary(request)
    if not staff:
        return JsonResponse({"error": "Forbidden"}, status=403)

    from datetime import datetime as _dt

    clinic = staff.clinic
    start_str = request.GET.get("start", "")
    end_str = request.GET.get("end", "")
    doctor_id = request.GET.get("doctor_id", "")

    qs = Appointment.objects.filter(clinic=clinic).select_related(
        "patient", "doctor", "appointment_type"
    )
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

    # Status → FullCalendar color
    STATUS_COLORS = {
        "PENDING":     "#d97706",  # amber
        "CONFIRMED":   "#10b981",  # emerald
        "CHECKED_IN":  "#3b82f6",  # blue
        "IN_PROGRESS": "#8b5cf6",  # purple
        "COMPLETED":   "#6b7280",  # gray
        "CANCELLED":   "#ef4444",  # red
        "NO_SHOW":     "#f59e0b",  # orange
    }

    events = []
    for appt in qs:
        duration = 30
        if appt.appointment_type and appt.appointment_type.duration_minutes:
            duration = appt.appointment_type.duration_minutes

        from datetime import datetime as _dt2, timedelta
        start_dt = _dt2.combine(appt.appointment_date, appt.appointment_time)
        end_dt = start_dt + timedelta(minutes=duration)

        title = appt.patient.name
        if appt.doctor:
            title += f" — {appt.doctor.name}"

        events.append({
            "id": appt.id,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": STATUS_COLORS.get(appt.status, "#6b7280"),
            "url": f"/secretary/appointments/{appt.id}/",
            "extendedProps": {
                "status": appt.status,
                "status_label": appt.get_status_display(),
                "patient": appt.patient.name,
                "doctor": appt.doctor.name if appt.doctor else "",
                "type": appt.appointment_type.display_name if appt.appointment_type else "",
            },
        })

    return JsonResponse(events, safe=False)


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


@login_required
def patient_list(request):
    """Full patient roster for this clinic with search, sort, pagination."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    from django.core.paginator import Paginator
    from django.db.models import Max, Count

    clinic = staff.clinic
    search = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "-registered_at")

    # Allowed sort fields (whitelist)
    _ALLOWED_SORTS = {
        "name": "patient__name",
        "-name": "-patient__name",
        "file_number": "file_number",
        "-file_number": "-file_number",
        "registered_at": "registered_at",
        "-registered_at": "-registered_at",
    }
    order_by = _ALLOWED_SORTS.get(sort, "-registered_at")

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

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "secretary/patients/list.html", {
        "clinic": clinic,
        "page_obj": page,
        "clinic_patients": page.object_list,
        "search": search,
        "sort": sort,
        "total_count": paginator.count,
    })


@login_required
def patient_detail(request, patient_id):
    """Full patient profile: personal info, appointments, medical records, billing."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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
    balance_due_total = 0
    try:
        from secretary.models import Invoice
        invoices = list(Invoice.objects.filter(
            clinic=clinic, patient=patient
        ).order_by("-created_at"))
        balance_due_total = Invoice.objects.filter(
            clinic=clinic, patient=patient
        ).aggregate(total=Sum("balance_due"))["total"] or 0
    except ImportError:
        pass  # Billing module not installed

    terminal_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]

    active_tab = request.GET.get("tab", "info")

    from patients.models import PatientProfile as _PP
    blood_type_choices = _PP.BLOOD_TYPE_CHOICES

    tab_list = [
        ("info",         "المعلومات الشخصية", "fa-solid fa-user"),
        ("appointments", "المواعيد",           "fa-solid fa-calendar-days"),
        ("records",      "السجلات الطبية",     "fa-solid fa-folder-open"),
        ("billing",      "الفواتير",            "fa-solid fa-receipt"),
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
        "balance_due_total": balance_due_total,
        "terminal_statuses": terminal_statuses,
        "active_tab": active_tab,
        "tab_list": tab_list,
        "blood_type_choices": blood_type_choices,
    })


@login_required
def edit_patient(request, patient_id):
    """Edit patient demographics (secretary-permitted fields only)."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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
        if clinic_patient.notes != notes:
            clinic_patient.notes = notes
            clinic_patient.save(update_fields=["notes"])

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


@login_required
def create_new_patient(request):
    """
    Create a brand-new patient (not yet in the system) and register them in this clinic.
    If a user with the same phone already exists, redirect to register_patient with warning.
    """
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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
                # Offer to register existing user into clinic
                messages.info(
                    request,
                    f"يوجد حساب بهذا الرقم ({existing_user.name}). تم تسجيله في عيادتك."
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


@login_required
def patient_list_htmx(request):
    """HTMX live search endpoint for the patient list table."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden()

    from django.db.models import Max, Count

    clinic = staff.clinic
    search = request.GET.get("q", "").strip()

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
        )
        .order_by("-registered_at")
    )

    if len(search) >= 2:
        normalized = PhoneNumberAuthBackend.normalize_phone_number(search)
        qs = qs.filter(
            Q(patient__name__icontains=search)
            | Q(patient__phone__icontains=normalized)
            | Q(patient__national_id__icontains=search)
            | Q(file_number__icontains=search)
        )

    qs = qs[:20]
    return render(request, "secretary/htmx/patient_list_rows.html", {
        "clinic_patients": qs,
        "search": search,
    })


@login_required
def appointments_list(request):
    """Full appointment list with filters, search, and pagination."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    from django.core.paginator import Paginator
    from clinics.models import ClinicStaff as CS

    clinic = staff.clinic
    status_filter = request.GET.get("status", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    doctor_filter = request.GET.get("doctor_id", "")
    search = request.GET.get("q", "").strip()

    qs = (
        Appointment.objects.filter(clinic=clinic)
        .select_related("patient", "doctor", "appointment_type")
        .order_by("-appointment_date", "appointment_time")
    )
    if status_filter:
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
    })


@login_required
def create_appointment(request):
    """Secretary books an appointment on behalf of a patient."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    from clinics.models import ClinicStaff as CS
    doctors_qs = CS.objects.filter(
        clinic=clinic, role="DOCTOR", is_active=True
    ).select_related("user").order_by("user__name")
    doctor_users = [s.user for s in doctors_qs]

    valid_doctor_ids = {u.id for u in doctor_users}
    # Pre-fill date/time from query params (when clicking a calendar slot)
    prefill_date = request.GET.get("date", "")
    prefill_time = request.GET.get("time", "")
    prefill_patient_id = request.GET.get("patient_id", "")

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
            notes_text = request.POST.get("notes", "").strip()
            is_walkin = request.POST.get("is_walkin") == "1"

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

            initial_status = (
                Appointment.Status.CHECKED_IN if is_walkin else Appointment.Status.CONFIRMED
            )

            appointment = secretary_book_appointment(
                patient=patient,
                doctor_id=doctor_id,
                clinic_id=clinic.id,
                appointment_type_id=type_id,
                appointment_date=appt_date,
                appointment_time=appt_time,
                reason=reason,
                notes=notes_text,
                status=initial_status,
                created_by=request.user,
            )

            if is_walkin:
                messages.success(request, _("تم تسجيل وصول %(name)s (حضور مباشر) بنجاح.") % {"name": patient.name})
            else:
                messages.success(request, _("تم حجز موعد %(name)s بنجاح.") % {"name": patient.name})
            return redirect("secretary:appointment_detail", appointment_id=appointment.id)

        except (BookingError, SlotUnavailableError) as e:
            messages.error(request, e.message)
        except Exception as e:
            messages.error(request, _("حدث خطأ: %(error)s") % {"error": e})

    appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)
    today_str = date.today().isoformat()

    return render(request, "secretary/appointments/create.html", {
        "clinic": clinic,
        "doctor_users": doctor_users,
        "appointment_types": appointment_types,
        "today_str": today_str,
        "prefill_date": prefill_date,
        "prefill_time": prefill_time,
        "prefill_patient_id": prefill_patient_id,
        "steps": [(_("المريض"), 1), (_("الموعد"), 2), (_("التأكيد"), 3)],
    })


@login_required
def edit_appointment(request, appointment_id):
    """Secretary reschedules or updates an appointment."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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
    # Filter appointment types by what this specific doctor offers in this clinic
    if appointment.doctor_id:
        appointment_types = get_appointment_types_for_doctor_in_clinic(
            appointment.doctor_id, clinic.id
        )
    else:
        appointment_types = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    if request.method == "POST":
        try:
            new_type_id = int(request.POST.get("appointment_type_id") or 0)
            new_date_str = request.POST.get("appointment_date", "").strip()
            new_time_str = request.POST.get("appointment_time", "").strip()
            new_reason = request.POST.get("reason", "").strip()

            if not all([new_type_id, new_date_str, new_time_str]):
                messages.error(request, _("يرجى ملء جميع الحقول المطلوبة."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            from datetime import datetime as dt_cls
            new_date = dt_cls.strptime(new_date_str, "%Y-%m-%d").date()
            new_time = dt_cls.strptime(new_time_str, "%H:%M").time()

            # S-06: Prevent rescheduling to a past date
            if new_date < date.today():
                messages.error(request, _("لا يمكن جدولة موعد في تاريخ ماضٍ."))
                return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            # Validate type is enabled for this doctor (S-03 equivalent for edit)
            if appointment.doctor_id:
                enabled_type_ids = {t.id for t in appointment_types}
                if enabled_type_ids and new_type_id not in enabled_type_ids:
                    messages.error(request, _("نوع الموعد المحدد غير متاح لهذا الطبيب."))
                    return redirect("secretary:edit_appointment", appointment_id=appointment_id)

            new_type = get_object_or_404(AppointmentType, id=new_type_id, clinic=clinic, is_active=True)

            # S-05: Check for slot conflicts with other confirmed appointments for the same doctor
            # (only if date, time, or doctor changes)
            date_or_time_changed = (
                new_date != appointment.appointment_date
                or new_time != appointment.appointment_time
            )
            if date_or_time_changed and appointment.doctor_id:
                conflict = Appointment.objects.filter(
                    doctor_id=appointment.doctor_id,
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

            appointment.appointment_type = new_type
            appointment.appointment_date = new_date
            appointment.appointment_time = new_time
            if new_reason:
                appointment.reason = new_reason
            appointment.save(update_fields=["appointment_type", "appointment_date", "appointment_time", "reason", "updated_at"])

            # Notify patient if date/time changed
            if date_or_time_changed:
                from django.db import transaction as _txn
                from appointments.services.appointment_notification_service import (
                    notify_appointment_rescheduled_by_staff,
                )
                _txn.on_commit(
                    lambda: notify_appointment_rescheduled_by_staff(appointment, old_date, old_time)
                )

            messages.success(request, _("تم تحديث الموعد بنجاح."))
            return redirect("secretary:appointments")
        except Exception as e:
            messages.error(request, _("حدث خطأ: %(error)s") % {"error": e})

    return render(request, "secretary/edit_appointment.html", {
        "clinic": clinic,
        "appointment": appointment,
        "appointment_types": appointment_types,
    })


@login_required
def cancel_appointment(request, appointment_id):
    """Secretary cancels an appointment (with optional reason)."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    if request.method == "POST":
        from secretary.services import transition_appointment_status
        from appointments.services.booking_service import BookingError
        try:
            appointment = get_object_or_404(Appointment, id=appointment_id, clinic=staff.clinic)
            reason = request.POST.get("cancellation_reason", "").strip() or "إلغاء من قِبل السكرتارية"
            transition_appointment_status(
                appointment,
                Appointment.Status.CANCELLED,
                cancellation_reason=reason,
                actor=request.user,
            )
            messages.success(request, _("تم إلغاء الموعد بنجاح."))
        except BookingError as e:
            messages.error(request, e.message)
        except Exception as e:
            messages.error(request, _("حدث خطأ أثناء الإلغاء: %(error)s") % {"error": e})

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("secretary:appointments")


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


@login_required
def register_patient(request):
    """Secretary patient registration landing page."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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


@login_required
def patient_search_htmx(request):
    """HTMX endpoint: search patients by name / phone / national ID."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    q = request.GET.get("q", "").strip()
    patients = []

    if len(q) >= 2:
        normalized_q = PhoneNumberAuthBackend.normalize_phone_number(q)
        patients = (
            User.objects.filter(
                Q(name__icontains=q)
                | Q(phone__icontains=normalized_q)
                | Q(national_id__icontains=q)
            )
            .filter(Q(role="PATIENT") | Q(roles__contains=["PATIENT"]))
            .select_related("patient_profile")
            .order_by("name")[:10]
        )

    return render(request, "secretary/htmx/patient_search_results.html", {
        "patients": patients,
        "query": q,
        "clinic_id": staff.clinic_id,
    })


@login_required
def patient_detail_htmx(request, patient_id):
    """HTMX endpoint: load patient summary card + registration form."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

    clinic = staff.clinic
    patient = get_object_or_404(User, id=patient_id)

    if not _is_patient_user(patient):
        return HttpResponse(
            '<p class="text-red-500 text-sm p-4">المستخدم المحدد ليس مريضاً.</p>',
            status=400,
        )

    profile = getattr(patient, "patient_profile", None)
    already_registered = ClinicPatient.objects.filter(
        clinic=clinic, patient=patient
    ).exists()
    age = _compute_age(profile.date_of_birth if profile else None)
    other_clinics = (
        ClinicPatient.objects.filter(patient=patient)
        .exclude(clinic=clinic)
        .select_related("clinic")
    )

    return render(request, "secretary/htmx/patient_card.html", {
        "patient": patient,
        "profile": profile,
        "age": age,
        "already_registered": already_registered,
        "clinic": clinic,
        "other_clinics": other_clinics,
    })


@login_required
def register_patient_submit(request):
    """POST: register a patient in the secretary's clinic, optionally filling profile gaps."""
    staff = _require_secretary(request)
    if not staff:
        return HttpResponseForbidden("هذه الصفحة متاحة للسكرتارية فقط.")

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

    messages.success(request, _("تم تسجيل المريض %(name)s في عيادة %(clinic)s بنجاح.") % {"name": patient.name, "clinic": clinic.name})
    return redirect("secretary:register_patient")