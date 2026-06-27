import json
import logging
from datetime import datetime, date
from functools import wraps

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST

from django.contrib import messages
from django.urls import reverse

from appointments.models import Appointment, AppointmentType
from clinics.models import ClinicStaff
from .models import DoctorAvailability, DoctorProfile, DoctorVerification, ClinicDoctorCredential, DoctorIntakeFormTemplate, DoctorIntakeQuestion, DoctorIntakeRule, ClinicalNoteTemplate, ClinicalNoteTemplateElement, DoctorClinicalNoteSettings
from .services import generate_slots_for_date
from accounts.otp_utils import request_otp, verify_otp, is_in_cooldown, get_remaining_resends, get_cooldown_remaining

User = get_user_model()
logger = logging.getLogger(__name__)


def _is_doctor(user):
    return user.has_role("DOCTOR") or user.has_role("MAIN_DOCTOR")


def doctor_required(view_func):
    """Require an authenticated DOCTOR/MAIN_DOCTOR.

    Consolidates the four role-check patterns that used to be copy-pasted across
    this module (inline ``"DOCTOR" not in ...`` blocks, ``_is_doctor``,
    ``_require_doctor`` and ``_doctor_required``). Stack it *under* ``@login_required``
    so ``request.user`` is always authenticated by the time this runs.

    HTML page requests get a flash message + redirect home (unchanged behaviour);
    HTMX/POST requests get a bare 403 so partial/AJAX callers fail cleanly instead
    of swallowing a redirected HTML page.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _is_doctor(request.user):
            if request.headers.get("HX-Request") or request.method == "POST":
                return HttpResponseForbidden("هذه الصفحة متاحة للأطباء فقط.")
            messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
            return redirect(reverse("accounts:home"))
        return view_func(request, *args, **kwargs)

    return _wrapped


# ============================================
# DOCTOR DASHBOARD
# ============================================


@login_required
@doctor_required
def dashboard(request):
    """Full doctor dashboard with verification status, clinic memberships, and today's appointments."""
    user = request.user

    # Identity verification
    verification = DoctorVerification.objects.filter(user=user).first()

    # Clinic memberships + credential status (deduplicated by clinic)
    # A doctor may have multiple ClinicStaff rows for the same clinic
    # (e.g. DOCTOR + SECRETARY). Show each clinic only once, preferring
    # the highest-privilege role: MAIN_DOCTOR > DOCTOR > SECRETARY.
    _role_priority = {"MAIN_DOCTOR": 0, "DOCTOR": 1, "SECRETARY": 2}
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("added_at")
    )
    _best: dict = {}
    for m in memberships:
        cid = m.clinic_id
        if cid not in _best or _role_priority.get(m.role, 99) < _role_priority.get(_best[cid].role, 99):
            _best[cid] = m

    # Fetch this doctor's credentials once and group by clinic, instead of
    # issuing a query (plus exists()/iteration) per clinic card.
    from collections import defaultdict
    creds_by_clinic = defaultdict(list)
    for cred in ClinicDoctorCredential.objects.filter(doctor=user).select_related("specialty"):
        creds_by_clinic[cred.clinic_id].append(cred)

    clinic_cards = []
    for m in _best.values():
        credentials = creds_by_clinic.get(m.clinic_id, [])
        all_verified = bool(credentials) and all(
            c.credential_status == "CREDENTIALS_VERIFIED" for c in credentials
        )
        clinic_cards.append({
            "membership": m,
            "clinic": m.clinic,
            "role": m.role,
            "credentials": credentials,
            "all_verified": all_verified,
        })

    # Today's appointments across all clinics
    today = date.today()
    todays_appointments = (
        Appointment.objects.filter(
            doctor=user,
            appointment_date=today,
        )
        .select_related("patient", "clinic", "appointment_type")
        .order_by("appointment_time")
    )

    # Upcoming appointments this week (next 7 days, not today)
    from datetime import timedelta
    week_end = today + timedelta(days=7)
    upcoming_count = Appointment.objects.filter(
        doctor=user,
        appointment_date__gt=today,
        appointment_date__lte=week_end,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
    ).count()

    # Total unique patients seen
    from django.db.models import Count as _Count
    total_patients_count = (
        Appointment.objects.filter(doctor=user)
        .exclude(status=Appointment.Status.CANCELLED)
        .values("patient_id")
        .distinct()
        .count()
    )

    # Pending invitations count
    from accounts.backends import PhoneNumberAuthBackend
    from clinics.models import ClinicInvitation
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    pending_invitations_count = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, status="PENDING"
    ).count()

    # Profile completeness
    profile = DoctorProfile.objects.filter(user=user).first()
    profile_complete = bool(
        profile and profile.bio and profile.years_of_experience
    )

    # Visibility check
    identity_verified = bool(
        verification and verification.identity_status == "IDENTITY_VERIFIED"
    )

    return render(request, "doctors/doctor_dashboard.html", {
        "verification": verification,
        "identity_verified": identity_verified,
        "clinic_cards": clinic_cards,
        "todays_appointments": todays_appointments,
        "upcoming_count": upcoming_count,
        "total_patients_count": total_patients_count,
        "pending_invitations_count": pending_invitations_count,
        "profile": profile,
        "profile_complete": profile_complete,
        "today": today,
    })


@login_required
@doctor_required
def appointments_list(request):
    """Doctor's full appointment list — filterable by date and status."""
    user = request.user

    from compliance.services.compliance_service import apply_due_no_shows
    apply_due_no_shows(Appointment.objects.filter(doctor=user))

    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")
    clinic_filter = request.GET.get("clinic_id", "")

    patient_filter = request.GET.get("patient_id", "")

    qs = (
        Appointment.objects.filter(doctor=user)
        .select_related("patient", "clinic", "appointment_type")
        .order_by("-appointment_date", "appointment_time")
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_filter:
        try:
            from datetime import datetime as _dt
            qs = qs.filter(appointment_date=_dt.strptime(date_filter, "%Y-%m-%d").date())
        except ValueError:
            pass
    if clinic_filter:
        qs = qs.filter(clinic_id=clinic_filter)
    if patient_filter:
        try:
            qs = qs.filter(patient_id=int(patient_filter))
        except ValueError:
            pass

    # Clinics this doctor works at (for filter dropdown)
    my_clinics = (
        ClinicStaff.objects.filter(user=user, is_active=True)
        .select_related("clinic")
        .values_list("clinic_id", "clinic__name")
    )

    return render(request, "doctors/appointments_list.html", {
        "appointments": qs,
        "status_choices": Appointment.Status.choices,
        "current_status": status_filter,
        "current_date": date_filter,
        "current_clinic": clinic_filter,
        "current_patient": patient_filter,
        "my_clinics": my_clinics,
    })


# ── Appointment helpers (shared by appointment_detail + appointment_overview) ──

def build_appointment_intake_data(appointment):
    """Merge an appointment's submitted text answers and file attachments into one
    ordered list (per question). Returns a list of dicts:
    ``{"question", "answer_text", "attachments"}`` sorted by question order."""
    from appointments.models import AppointmentAnswer, AppointmentAttachment

    intake_answers = (
        AppointmentAnswer.objects.filter(appointment=appointment)
        .select_related("question")
    )
    intake_attachments = (
        AppointmentAttachment.objects.filter(appointment=appointment)
        .select_related("question")
        .order_by("question__order", "file_group_date", "uploaded_at")
    )

    _combined = {}
    for ans in intake_answers:
        _combined[ans.question_id] = {
            "question": ans.question,
            "answer_text": ans.answer_text,
            "attachments": [],
        }
    for att in intake_attachments:
        q_id = att.question_id
        if q_id not in _combined:
            _combined[q_id] = {
                "question": att.question,
                "answer_text": "",
                "attachments": [],
            }
        _combined[q_id]["attachments"].append(att)

    return sorted(
        _combined.values(),
        key=lambda x: x["question"].order if x["question"] else 0,
    )


# Status transitions the doctor is allowed to trigger, keyed by current status.
_STATUS_TRANSITION_MAP = {
    Appointment.Status.PENDING: [
        Appointment.Status.CONFIRMED,
        Appointment.Status.CANCELLED,
    ],
    Appointment.Status.CONFIRMED: [
        Appointment.Status.CHECKED_IN,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ],
    Appointment.Status.CHECKED_IN: [Appointment.Status.IN_PROGRESS],
    Appointment.Status.IN_PROGRESS: [Appointment.Status.COMPLETED],
}


def allowed_status_transitions(appointment):
    """Return ``(allowed_transitions, valid_transition_values)`` for an appointment.

    ``allowed_transitions`` is a list of (value, label) tuples for the template;
    ``valid_transition_values`` is a set of allowed status values for POST validation.
    """
    raw_transitions = _STATUS_TRANSITION_MAP.get(appointment.status, [])
    allowed = [(s.value, s.label) for s in raw_transitions]
    valid_values = {s.value for s in raw_transitions}
    return allowed, valid_values


def apply_status_transition(request, appointment, user):
    """Handle a doctor's status-change POST for an appointment.

    Returns True when a valid transition was applied (the caller should redirect).
    Enforces the whitelist server-side (ignoring stale/tampered POSTs) and notifies
    the patient when the appointment is cancelled. Flash messages are bilingual.
    """
    from django.contrib import messages as _msg

    _, valid_values = allowed_status_transitions(appointment)
    new_status = request.POST.get("status", "").strip()
    notes = request.POST.get("notes", "").strip()
    cancellation_reason = request.POST.get("cancellation_reason", "").strip()
    is_rtl = getattr(request, "LANGUAGE_CODE", "ar") == "ar"

    if new_status in valid_values:
        # Cancelling requires a non-blank reason (visible to the patient + staff).
        if new_status == Appointment.Status.CANCELLED and not cancellation_reason:
            _msg.error(
                request,
                "يرجى ذكر سبب الإلغاء." if is_rtl else "Please provide a cancellation reason.",
            )
            return False

        appointment.status = new_status
        if notes:
            appointment.notes = notes
        update_fields = ["status", "notes", "updated_at"]

        if new_status == Appointment.Status.CANCELLED:
            appointment.cancellation_reason = cancellation_reason
            update_fields.append("cancellation_reason")

        # On check-in, stamp the arrival time and assign a queue position — mirroring
        # the secretary check-in — so the patient surfaces correctly (with arrival
        # time and proper ordering) in the secretary waiting-room queue.
        if new_status == Appointment.Status.CHECKED_IN:
            from django.utils import timezone as _tz
            from secretary.services import _next_queue_priority
            appointment.checked_in_at = _tz.now()
            appointment.queue_priority = _next_queue_priority(
                appointment.clinic_id, date.today()
            )
            update_fields += ["checked_in_at", "queue_priority"]

        appointment.save(update_fields=update_fields)

        # Notify the patient AND the clinic secretaries when the doctor cancels.
        if new_status == Appointment.Status.CANCELLED:
            from django.db import transaction as _txn
            from clinics.models import ClinicStaff as _CS
            from appointments.services.appointment_notification_service import (
                notify_appointment_cancelled_by_staff,
                notify_secretaries_appointment_cancelled_by_doctor,
            )
            doctor_staff = _CS.objects.filter(
                clinic=appointment.clinic, user=user, revoked_at__isnull=True
            ).first()
            _txn.on_commit(
                lambda: notify_appointment_cancelled_by_staff(appointment, doctor_staff)
            )
            _txn.on_commit(
                lambda: notify_secretaries_appointment_cancelled_by_doctor(
                    appointment, doctor_staff
                )
            )

        _msg.success(
            request,
            "تم تحديث حالة الموعد." if is_rtl else "Appointment status updated.",
        )
        return True

    if new_status:
        _msg.error(
            request,
            "هذا التحديث غير مسموح به." if is_rtl else "This status update is not allowed.",
        )
    return False


def _validated_next(request):
    """Return a safe internal ``next`` URL from the request, or None.

    Lets the back/cancel buttons return to wherever the doctor came from
    (e.g. the appointment overview page) instead of a hard-coded destination.
    """
    from django.utils.http import url_has_allowed_host_and_scheme

    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return None


def _appointment_doctor_notes(appointment, viewer=None):
    """Staff notes on this appointment the doctor may see: doctor-audience notes
    (visible to doctor + secretaries), plus the viewer's own private notes. Never
    secretary-only notes — even ones the viewer authored via the secretary portal."""
    from django.db.models import Q
    from patients.models import StaffNote
    visible = Q(audience=StaffNote.Audience.DOCTOR)
    if viewer is not None:
        visible |= Q(audience=StaffNote.Audience.DOCTOR_PRIVATE, author=viewer)
    return list(
        StaffNote.objects.filter(appointment=appointment)
        .filter(visible)
        .select_related("author").order_by("-created_at")
    )


def _patient_doctor_notes(patient, clinic_ids, viewer=None):
    """Patient-profile staff notes (appointment is None) the doctor may see: doctor-audience
    notes, plus the viewer's own private notes. Never secretary-only notes."""
    from django.db.models import Q
    from patients.models import StaffNote
    visible = Q(audience=StaffNote.Audience.DOCTOR)
    if viewer is not None:
        visible |= Q(audience=StaffNote.Audience.DOCTOR_PRIVATE, author=viewer)
    return list(
        StaffNote.objects.filter(
            patient=patient, clinic_id__in=clinic_ids, appointment__isnull=True
        ).filter(visible).select_related("author").order_by("-created_at")
    )


def _staff_note_lang(ar, en):
    """Pick AR/EN by the active language (avoids relying on compiled .po catalogs)."""
    from django.utils.translation import get_language
    lang = (get_language() or "ar").split("-")[0]
    return en if lang.startswith("en") else ar


@login_required
@require_POST
def appointment_note_add(request, appointment_id):
    """Doctor adds a note (private to himself, or for himself + secretaries) on one of
    their appointments. The doctor portal never authors secretary-only notes."""
    from patients.models import StaffNote
    from appointments.services.appointment_notification_service import notify_staff_note

    user = request.user
    if not _is_doctor(user):
        return HttpResponseForbidden("هذه الصفحة متاحة للأطباء فقط.")
    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "clinic", "doctor"),
        id=appointment_id, doctor=user,
    )

    audience = (request.POST.get("audience") or "").strip().upper()
    body = (request.POST.get("body") or "").strip()
    if audience not in (StaffNote.Audience.DOCTOR, StaffNote.Audience.DOCTOR_PRIVATE):
        messages.error(request, _staff_note_lang("نوع الملاحظة غير صالح.", "Invalid note type."))
    elif not body:
        messages.error(request, _staff_note_lang("لا يمكن إضافة ملاحظة فارغة.", "Cannot add an empty note."))
    else:
        note = StaffNote.objects.create(
            clinic=appointment.clinic,
            patient=appointment.patient,
            appointment=appointment,
            audience=audience,
            body=body,
            author=user,
            author_name=user.name,
            author_role="DOCTOR",
        )
        transaction.on_commit(lambda: notify_staff_note(note, user))
        messages.success(request, _staff_note_lang("تمت إضافة الملاحظة.", "Note added."))

    return _doctor_note_redirect(request, appointment_id)


@login_required
@require_POST
def appointment_note_delete(request, appointment_id, note_id):
    """Doctor deletes their OWN note on one of their appointments.

    The lookup is scoped to doctor-visible notes so the endpoint can never reference a
    secretary-only note (no 403-vs-404 existence oracle), and deletion is allowed only
    for notes authored from the doctor portal (``can_delete(..., "DOCTOR")``)."""
    from django.db.models import Q
    from patients.models import StaffNote

    user = request.user
    if not _is_doctor(user):
        return HttpResponseForbidden("هذه الصفحة متاحة للأطباء فقط.")
    # Ensure the appointment belongs to this doctor before touching the note.
    get_object_or_404(Appointment, id=appointment_id, doctor=user)
    visible = Q(audience=StaffNote.Audience.DOCTOR) | Q(
        audience=StaffNote.Audience.DOCTOR_PRIVATE, author=user
    )
    note = get_object_or_404(
        StaffNote.objects.filter(visible), id=note_id, appointment_id=appointment_id
    )
    if not note.can_delete(user, "DOCTOR"):
        return HttpResponseForbidden("لا يمكنك حذف ملاحظة كتبها شخص آخر.")
    note.delete()
    messages.success(request, _staff_note_lang("تم حذف الملاحظة.", "Note deleted."))
    return _doctor_note_redirect(request, appointment_id)


def _doctor_note_redirect(request, appointment_id):
    """Return to the POSTed local ``next`` (the detail/overview page), else the detail page."""
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(reverse("doctors:appointment_detail", args=[appointment_id]) + "#staff-notes")


def _doctor_profile_note_redirect(request, patient_id):
    """Return to the POSTed local ``next`` (the workspace page), else the overview tab."""
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(
        reverse("doctors:patient_workspace", args=[patient_id]) + "?tab=overview#staff-notes"
    )


@login_required
@require_POST
def patient_note_add(request, patient_id):
    """Doctor adds a patient-profile note (private to himself, or for himself + secretaries)."""
    from patients.models import StaffNote
    from appointments.services.appointment_notification_service import notify_staff_note

    ctx = _ws_access(request, patient_id)
    if ctx is None:
        return HttpResponseForbidden("هذه الصفحة متاحة للأطباء فقط.")
    user = ctx["doctor"]
    patient = ctx["patient"]
    clinic_id = ctx["shared_clinic_ids"][0]

    audience = (request.POST.get("audience") or "").strip().upper()
    body = (request.POST.get("body") or "").strip()
    if audience not in (StaffNote.Audience.DOCTOR, StaffNote.Audience.DOCTOR_PRIVATE):
        messages.error(request, _staff_note_lang("نوع الملاحظة غير صالح.", "Invalid note type."))
    elif not body:
        messages.error(request, _staff_note_lang("لا يمكن إضافة ملاحظة فارغة.", "Cannot add an empty note."))
    else:
        note = StaffNote.objects.create(
            clinic_id=clinic_id,
            patient=patient,
            appointment=None,
            audience=audience,
            body=body,
            author=user,
            author_name=user.name,
            author_role="DOCTOR",
        )
        transaction.on_commit(lambda: notify_staff_note(note, user))
        messages.success(request, _staff_note_lang("تمت إضافة الملاحظة.", "Note added."))

    return _doctor_profile_note_redirect(request, patient_id)


@login_required
@require_POST
def patient_note_delete(request, patient_id, note_id):
    """Doctor deletes their OWN patient-profile note.

    Scoped to doctor-visible notes (no secretary-only existence oracle); deletion is
    allowed only for notes authored from the doctor portal."""
    from django.db.models import Q
    from patients.models import StaffNote

    ctx = _ws_access(request, patient_id)
    if ctx is None:
        return HttpResponseForbidden("هذه الصفحة متاحة للأطباء فقط.")
    doctor = ctx["doctor"]
    visible = Q(audience=StaffNote.Audience.DOCTOR) | Q(
        audience=StaffNote.Audience.DOCTOR_PRIVATE, author=doctor
    )
    note = get_object_or_404(
        StaffNote.objects.filter(visible),
        id=note_id, patient_id=patient_id, appointment__isnull=True,
    )
    if not note.can_delete(doctor, "DOCTOR"):
        return HttpResponseForbidden("لا يمكنك حذف ملاحظة كتبها شخص آخر.")
    note.delete()
    messages.success(request, _staff_note_lang("تم حذف الملاحظة.", "Note deleted."))
    return _doctor_profile_note_redirect(request, patient_id)


@login_required
@doctor_required
def appointment_detail(request, appointment_id):
    """Single appointment view with patient info, intake answers, and status controls."""
    user = request.user

    appointment = get_object_or_404(Appointment, id=appointment_id, doctor=user)
    next_url = _validated_next(request)

    if request.method == "POST" and apply_status_transition(request, appointment, user):
        target = reverse("doctors:appointment_detail", args=[appointment_id])
        if next_url:
            from django.utils.http import urlencode
            target = f"{target}?{urlencode({'next': next_url})}"
        return redirect(target)

    intake_data = build_appointment_intake_data(appointment)
    allowed_transitions, valid_values = allowed_status_transitions(appointment)

    return render(request, "doctors/appointment_detail.html", {
        "appointment": appointment,
        "intake_data": intake_data,
        "allowed_transitions": allowed_transitions,
        "can_cancel": Appointment.Status.CANCELLED in valid_values,
        "next_url": next_url,
        "back_url": next_url or reverse("doctors:appointments"),
        "staff_doctor_notes": _appointment_doctor_notes(appointment, viewer=user),
    })


@login_required
@doctor_required
def appointment_overview(request, appointment_id):
    """Patient-scoped view of a single appointment.

    Reached from the doctor notification center "view appointment" link. Shows the
    patient's details, the notification's appointment highlighted (with its submitted
    intake form and status-update controls), and a timeline of the patient's other
    appointments with this doctor (upcoming + past) whose forms can be revealed inline.
    """
    user = request.user

    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "clinic", "appointment_type"),
        id=appointment_id,
        doctor=user,
    )

    if request.method == "POST" and apply_status_transition(request, appointment, user):
        return redirect("doctors:appointment_overview", appointment_id=appointment_id)

    patient = appointment.patient
    intake_data = build_appointment_intake_data(appointment)
    allowed_transitions, valid_values = allowed_status_transitions(appointment)

    # The patient's other appointments with this doctor, split into upcoming/past.
    today = date.today()
    active_statuses = [
        Appointment.Status.PENDING,
        Appointment.Status.CONFIRMED,
        Appointment.Status.CHECKED_IN,
        Appointment.Status.IN_PROGRESS,
    ]
    other_appts = (
        Appointment.objects.filter(doctor=user, patient=patient)
        .exclude(id=appointment_id)
        .select_related("clinic", "appointment_type")
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

    # Patient age (mirrors _ws_access age computation).
    profile = getattr(patient, "patient_profile", None)
    age = None
    if profile and profile.date_of_birth:
        dob = profile.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    return render(request, "doctors/appointment_overview.html", {
        "appointment": appointment,
        "patient": patient,
        "profile": profile,
        "age": age,
        "intake_data": intake_data,
        "allowed_transitions": allowed_transitions,
        "can_cancel": Appointment.Status.CANCELLED in valid_values,
        "upcoming": upcoming,
        "past": past,
        "staff_doctor_notes": _appointment_doctor_notes(appointment, viewer=user),
        "patient_staff_notes": _patient_doctor_notes(
            patient, [appointment.clinic_id], viewer=user
        ),
    })


@login_required
@doctor_required
def appointment_intake_partial(request, appointment_id):
    """HTMX endpoint: render an appointment's submitted intake form (inline expander)."""
    user = request.user

    appointment = get_object_or_404(Appointment, id=appointment_id, doctor=user)
    intake_data = build_appointment_intake_data(appointment)
    return render(request, "doctors/partials/_appointment_intake_panel.html", {
        "intake_data": intake_data,
    })


def _doctor_clinic_ids(user):
    """Return ``(my_clinics, clinic_ids)`` for a doctor's active memberships.

    A multi-role user (e.g. MAIN_DOCTOR + DOCTOR) can hold several ``ClinicStaff``
    rows for the same clinic, so we keep the first row per clinic (ordered by clinic
    name) plus the set of distinct clinic ids. Shared by the patient list.
    """
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("clinic__name")
    )
    seen = set()
    my_clinics = []
    for staff in memberships:
        if staff.clinic_id not in seen:
            seen.add(staff.clinic_id)
            my_clinics.append(staff)
    return my_clinics, list(seen)


def _classify_patient_status(last_visit, today):
    """Recency bucket for a patient: active (≤30d), follow_up (≤90d), else inactive."""
    if not last_visit:
        return "inactive"
    days = (today - last_visit).days
    if days <= 30:
        return "active"
    if days <= 90:
        return "follow_up"
    return "inactive"


def _enrich_patient_row(patient, profile, clinics, today):
    """Build the template/test row dict for one patient (annotated User instance)."""
    age = None
    if profile and profile.date_of_birth:
        dob = profile.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    gender = profile.gender if profile else ""
    return {
        "patient_id": patient.id,
        "patient__name": patient.name,
        "patient__phone": patient.phone,
        "patient__national_id": patient.national_id,
        "last_visit": patient.last_visit,
        "total_visits": patient.total_visits or 0,
        "age": age,
        "gender": gender,
        "gender_display": {"M": "Male", "F": "Female", "O": "Other"}.get(gender, "—"),
        "clinics": clinics,
        "patient_status": _classify_patient_status(patient.last_visit, today),
    }


@login_required
@doctor_required
def patients_list(request):
    """Doctor's patient management page — full clinical tool with search, filter, sort, pagination.

    Filtering, status bucketing, sorting and pagination all run in the database; only
    the 25 rows of the current page are pulled into Python for enrichment, so the view
    no longer materialises the clinic's entire patient table on every request.
    """
    user = request.user

    from collections import defaultdict
    from datetime import date, datetime, timedelta
    from django.db.models import Count, F, Max, Q
    from django.db.models.functions import Lower
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    from patients.models import PatientProfile, ClinicPatient

    # ── Query params ─────────────────────────────────────────
    q             = request.GET.get("q", "").strip()
    clinic_filter = request.GET.get("clinic_id", "")
    status_filter = request.GET.get("status", "")
    date_from     = request.GET.get("date_from", "")
    date_to       = request.GET.get("date_to", "")
    sort          = request.GET.get("sort", "-last_visit")
    page_num      = request.GET.get("page", "1")

    my_clinics, clinic_ids = _doctor_clinic_ids(user)

    # ── Determine effective clinic IDs for this request ──────
    if clinic_filter:
        try:
            _fid = int(clinic_filter)
        except (ValueError, TypeError):
            _fid = None
        if _fid in clinic_ids:
            effective_clinic_ids = [_fid]
        else:
            clinic_filter = ""
            effective_clinic_ids = clinic_ids
    else:
        effective_clinic_ids = clinic_ids

    # ── Candidate patients: anyone registered (ClinicPatient) in the
    # effective clinics, optionally matching the search term. Kept as a
    # subquery so we never materialise the whole patient table. ─────────
    cp_qs = ClinicPatient.objects.filter(clinic_id__in=effective_clinic_ids)
    if q:
        phone_q = q.replace(" ", "").replace("-", "")
        cp_qs = cp_qs.filter(
            Q(patient__name__icontains=q)
            | Q(patient__phone__icontains=phone_q)
            | Q(patient__national_id__icontains=q)
        )

    # ── Appointment recency/volume per patient (this doctor only). The
    # optional date window is applied inside the aggregate filter. ───────
    appt_match = (
        Q(appointments_as_patient__doctor=user)
        & Q(appointments_as_patient__clinic_id__in=effective_clinic_ids)
        & ~Q(appointments_as_patient__status=Appointment.Status.CANCELLED)
    )
    if date_from:
        try:
            appt_match &= Q(
                appointments_as_patient__appointment_date__gte=datetime.strptime(date_from, "%Y-%m-%d").date()
            )
        except ValueError:
            date_from = ""
    if date_to:
        try:
            appt_match &= Q(
                appointments_as_patient__appointment_date__lte=datetime.strptime(date_to, "%Y-%m-%d").date()
            )
        except ValueError:
            date_to = ""

    patients_qs = User.objects.filter(id__in=cp_qs.values("patient_id")).annotate(
        last_visit=Max("appointments_as_patient__appointment_date", filter=appt_match),
        total_visits=Count("appointments_as_patient__id", filter=appt_match),
    )

    # ── Status buckets, expressed as last_visit recency windows so they
    # can be both filtered and counted in the database. ──────────────────
    today = date.today()
    active_since   = today - timedelta(days=30)
    followup_since = today - timedelta(days=90)
    STATUS_FILTERS = {
        "active":    Q(last_visit__gte=active_since),
        "follow_up": Q(last_visit__lt=active_since, last_visit__gte=followup_since),
        "inactive":  Q(last_visit__isnull=True) | Q(last_visit__lt=followup_since),
    }

    # ── Summary counts — preserves the historical "counts over the filtered
    # set" behaviour: a non-empty status filter zeroes the other buckets. ─
    if status_filter:
        display_qs = (
            patients_qs.filter(STATUS_FILTERS[status_filter])
            if status_filter in STATUS_FILTERS else patients_qs.none()
        )
        total_count    = display_qs.count()
        active_count   = total_count if status_filter == "active" else 0
        followup_count = total_count if status_filter == "follow_up" else 0
        inactive_count = total_count if status_filter == "inactive" else 0
    else:
        display_qs = patients_qs
        active_count   = patients_qs.filter(STATUS_FILTERS["active"]).count()
        followup_count = patients_qs.filter(STATUS_FILTERS["follow_up"]).count()
        inactive_count = patients_qs.filter(STATUS_FILTERS["inactive"]).count()
        total_count    = active_count + followup_count + inactive_count

    # ── Sort in the database; "id" is the stable tie-breaker. ─────────
    SORT_FIELDS = {
        "-last_visit": (F("last_visit").desc(nulls_last=True), "id"),
        "last_visit":  (F("last_visit").asc(nulls_first=True), "id"),
        "name":        (Lower("name").asc(), "id"),
        "-name":       (Lower("name").desc(), "id"),
        "-visits":     (F("total_visits").desc(), "id"),
        "visits":      (F("total_visits").asc(), "id"),
    }
    display_qs = display_qs.order_by(*SORT_FIELDS.get(sort, SORT_FIELDS["-last_visit"]))

    # ── Paginate the queryset (only the current page is fetched). ─────
    paginator = Paginator(display_qs, 25)
    try:
        page_obj = paginator.page(int(page_num))
    except (ValueError, EmptyPage, PageNotAnInteger):
        page_obj = paginator.page(1)

    # ── Enrich ONLY the current page. ─────────────────────────────────
    page_patients = list(page_obj.object_list)
    page_pids = [p.id for p in page_patients]
    profiles = {
        pp.user_id: pp
        for pp in PatientProfile.objects.filter(user_id__in=page_pids)
    }
    # Clinic tags span ALL the doctor's clinics (not just the filtered one).
    patient_clinic_map = defaultdict(list)
    seen_pairs = set()
    for cp in ClinicPatient.objects.filter(
        patient_id__in=page_pids, clinic_id__in=clinic_ids
    ).select_related("clinic"):
        key = (cp.patient_id, cp.clinic_id)
        if key not in seen_pairs:
            seen_pairs.add(key)
            patient_clinic_map[cp.patient_id].append({"id": cp.clinic_id, "name": cp.clinic.name})

    page_obj.object_list = [
        _enrich_patient_row(p, profiles.get(p.id), patient_clinic_map.get(p.id, []), today)
        for p in page_patients
    ]

    ctx = {
        "patient_page":    page_obj,
        "total_count":     total_count,
        "active_count":    active_count,
        "followup_count":  followup_count,
        "inactive_count":  inactive_count,
        "my_clinics":      my_clinics,
        "current_clinic":  clinic_filter,
        "current_status":  status_filter,
        "current_sort":    sort,
        "current_q":       q,
        "date_from":       date_from,
        "date_to":         date_to,
        "paginator":       paginator,
    }

    # HTMX partial request → return only the table section
    if request.headers.get("HX-Request"):
        return render(request, "doctors/partials/patients_table.html", ctx)

    return render(request, "doctors/patients_list.html", ctx)


# --- Patient-facing views ---


@login_required
def doctor_availability_view(request, doctor_id):
    """
    Patient-facing view: Shows a doctor's weekly schedule and
    available time slots for a selected date.

    Query params:
        clinic_id (required): Which clinic to view availability for.
        date (optional): Date to generate slots for (YYYY-MM-DD).
        appointment_type_id (optional): Required when date is provided.
    """
    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    clinic_id = request.GET.get("clinic_id")

    if not clinic_id:
        return render(
            request,
            "doctors/doctor_availability.html",
            {"error": "clinic_id is required.", "doctor": doctor},
        )

    # Weekly schedule
    weekly_schedule = DoctorAvailability.objects.filter(
        doctor=doctor,
        clinic_id=clinic_id,
        is_active=True,
    )

    # Appointment types enabled for this doctor at this clinic (not all clinic types)
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )
    appointment_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=doctor.id, clinic_id=int(clinic_id)
    )

    # Slot generation (if date + appointment_type_id provided)
    slots = []
    target_date = None
    selected_type = None
    date_str = request.GET.get("date")
    appointment_type_id = request.GET.get("appointment_type_id")

    if date_str and appointment_type_id:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = None

        if target_date and target_date >= date.today():
            # Only allow slot generation for types the doctor actually offers
            try:
                apt_type_id_int = int(appointment_type_id)
            except (ValueError, TypeError):
                apt_type_id_int = None

            if apt_type_id_int:
                selected_type = next(
                    (t for t in appointment_types if t.id == apt_type_id_int), None
                )
            if selected_type:
                slots = generate_slots_for_date(
                    doctor_id=doctor.id,
                    clinic_id=int(clinic_id),
                    target_date=target_date,
                    duration_minutes=selected_type.duration_minutes,
                )

    context = {
        "doctor": doctor,
        "clinic_id": clinic_id,
        "weekly_schedule": weekly_schedule,
        "appointment_types": appointment_types,
        "slots": slots,
        "target_date": target_date,
        "selected_type": selected_type,
        "today": date.today().isoformat(),
    }
    return render(request, "doctors/doctor_availability.html", context)


@login_required
def doctor_appointment_types_view(request, doctor_id):
    """
    Patient-facing view: Shows appointment types offered by a doctor.

    Returns only the types that doctor has enabled for the given clinic
    (falling back to all active clinic types if the doctor has no
    DoctorClinicAppointmentType rows configured yet).

    Query params:
        clinic_id (required): Which clinic to view types for.
    """
    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )

    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    clinic_id = request.GET.get("clinic_id")

    if not clinic_id:
        return render(
            request,
            "doctors/doctor_appointment_types.html",
            {"error": "clinic_id is required.", "doctor": doctor},
        )

    appointment_types = get_appointment_types_for_doctor_in_clinic(
        doctor_id=doctor_id,
        clinic_id=int(clinic_id),
    )

    context = {
        "doctor": doctor,
        "clinic_id": clinic_id,
        "appointment_types": appointment_types,
    }
    return render(request, "doctors/doctor_appointment_types.html", context)


# ============================================
# DOCTOR INVITATIONS FLOW
# ============================================

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils import timezone
from django.core.exceptions import ValidationError

from .clinical_note_template_service import create_clinical_note_template, update_clinical_note_template
from clinics.models import ClinicInvitation
from clinics.services import accept_invitation, reject_invitation
from accounts.backends import PhoneNumberAuthBackend

@login_required
def doctor_invitations_inbox(request):
    """View pending invitations for the logged-in doctor."""
    user = request.user
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    
    invitations = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, 
        status="PENDING"
    ).select_related('clinic', 'invited_by').prefetch_related('specialties').order_by('-created_at')
    
    return render(request, "doctors/invitations_inbox.html", {
        "invitations": invitations,
    })

@login_required
def accept_invitation_view(request, invitation_id):
    """Accept an invitation.

    The lookup is scoped to the caller's own phone, so unknown or someone else's
    invitation id returns a uniform 404 (no existence oracle); ``accept_invitation``
    re-verifies ownership inside its transaction as the real guard.
    """
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    invitation = get_object_or_404(
        ClinicInvitation, id=invitation_id, doctor_phone=normalized_phone
    )

    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, f"You have successfully joined {staff.clinic.name}.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except Exception:
            logger.exception("Failed to accept invitation %s", invitation_id)
            messages.error(request, "Something went wrong while accepting the invitation. Please try again.")

    return redirect(reverse("doctors:doctor_invitations_inbox"))

@login_required
def reject_invitation_view(request, invitation_id):
    """Reject an invitation (scoped to the caller's own phone; see accept view)."""
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
    invitation = get_object_or_404(
        ClinicInvitation, id=invitation_id, doctor_phone=normalized_phone
    )

    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, "Invitation rejected.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except Exception:
            logger.exception("Failed to reject invitation %s", invitation_id)
            messages.error(request, "Something went wrong while rejecting the invitation. Please try again.")

    return redirect(reverse("doctors:doctor_invitations_inbox"))


def guest_accept_invitation_view(request, token):
    """
    Public endpoint for email/SMS link.
    Shows generic error if token invalid/expired.
    If valid, redirects to login/reg storing token in session.
    """
    try:
        invitation = ClinicInvitation.objects.get(token=token)
    except ClinicInvitation.DoesNotExist:
        return render(request, "doctors/invalid_invitation.html", {
            "error": "This invitation link is invalid or has already been used."
        })

    if invitation.status != "PENDING" or invitation.is_expired:
         return render(request, "doctors/invalid_invitation.html", {
            "error": "This invitation has expired or is no longer available."
        })

    is_secretary_invite = (invitation.role == "SECRETARY")

    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
            # Already logged in as the right user — send to the correct inbox
            if is_secretary_invite:
                return redirect(reverse("secretary:secretary_invitations_inbox"))
            return redirect(reverse("doctors:doctor_invitations_inbox"))
        else:
            # Logged in as someone else (wrong phone)
            return render(request, "doctors/invalid_invitation.html", {
               "error": "You don't have permission to access this invitation. Please log in with the correct account."
           })

    # Check if the invited phone belongs to an existing user.
    from django.contrib.auth import get_user_model as _get_user_model
    _User = _get_user_model()
    existing_user = _User.objects.filter(phone=invitation.doctor_phone).first()

    # Determine which app slug to store in session for post-login redirect
    app_slug = "secretary" if is_secretary_invite else "doctors"

    if existing_user:
        # Store token for redirection after login
        request.session["pending_invitation_token"] = str(token)
        request.session["pending_invitation_app"] = app_slug
        login_url = reverse("accounts:login")

        if is_secretary_invite:
            messages.info(
                request,
                f"Welcome {invitation.doctor_name}, you already have an account. Please log in to accept the invitation."
            )
        else:
            messages.info(
                request,
                f"Welcome Dr. {invitation.doctor_name}, you already have an account. Please log in to accept the invitation."
            )
        return redirect(login_url)

    # No account yet: instruct them to register first.
    if is_secretary_invite:
        messages.info(
            request,
            f"Welcome {invitation.doctor_name}, you have been invited to join {invitation.clinic.name} as a secretary. Please create an account to accept the invitation."
        )
    else:
        messages.info(
            request,
            f"Welcome Dr. {invitation.doctor_name}, you have been invited to join {invitation.clinic.name}. Please create an account to accept the invitation."
        )

    # Store token for redirection after registration
    request.session["pending_invitation_token"] = str(token)
    request.session["pending_invitation_app"] = app_slug
    return redirect(reverse("accounts:register"))


# ============================================
# DOCTOR VERIFICATION & CREDENTIAL UPLOAD
# ============================================

from doctors.models import DoctorVerification, ClinicDoctorCredential
from django import forms as django_forms
from core.validators.file_validators import validate_file_extension, validate_file_signature, validate_file_size


_FILE_INPUT_CLASSES = (
    "w-full text-sm text-gray-700 dark:text-gray-300 cursor-pointer "
    "border border-gray-300 dark:border-gray-600 rounded-xl "
    "file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 "
    "file:text-sm file:font-semibold file:bg-indigo-50 dark:file:bg-indigo-900/40 "
    "file:text-indigo-700 dark:file:text-indigo-300 hover:file:bg-indigo-100 dark:hover:file:bg-indigo-900/60"
)

_TEXT_INPUT_CLASSES = (
    "w-full text-sm rounded-xl border border-gray-300 dark:border-gray-600 "
    "bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-100 "
    "px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
)


class DoctorCredentialUploadForm(django_forms.Form):
    """Form for uploading doctor identity verification documents."""
    identity_document = django_forms.FileField(
        label="Identity Document (National ID / Passport)",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": _FILE_INPUT_CLASSES, "accept": ".jpg,.jpeg,.png,.pdf"}),
    )
    medical_license = django_forms.FileField(
        label="Medical Practice License",
        required=False,
        widget=django_forms.ClearableFileInput(attrs={"class": _FILE_INPUT_CLASSES, "accept": ".jpg,.jpeg,.png,.pdf"}),
    )

    def clean_identity_document(self):
        f = self.cleaned_data.get("identity_document")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f

    def clean_medical_license(self):
        f = self.cleaned_data.get("medical_license")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f


@login_required
@doctor_required
def doctor_verification_status(request):
    """Show the doctor's dual-layer verification status."""
    user = request.user

    verification = DoctorVerification.objects.filter(user=user).first()
    credentials = ClinicDoctorCredential.objects.filter(
        doctor=user
    ).select_related("clinic", "specialty").order_by("clinic__name")

    return render(request, "doctors/verification_status.html", {
        "verification": verification,
        "credentials": credentials,
    })


@login_required
@doctor_required
def doctor_upload_credentials(request):
    """Upload identity documents for platform verification."""
    user = request.user

    verification, _ = DoctorVerification.objects.get_or_create(
        user=user,
        defaults={"identity_status": "IDENTITY_UNVERIFIED"},
    )

    if request.method == "POST":
        form = DoctorCredentialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            changed = False
            if form.cleaned_data.get("identity_document"):
                verification.identity_document = form.cleaned_data["identity_document"]
                changed = True
            if form.cleaned_data.get("medical_license"):
                verification.medical_license = form.cleaned_data["medical_license"]
                changed = True

            if changed:
                if verification.identity_status in ("IDENTITY_UNVERIFIED", "IDENTITY_REJECTED"):
                    verification.identity_status = "IDENTITY_PENDING_REVIEW"
                verification.save()
                messages.success(request, "Documents uploaded successfully. They will be reviewed by the admin team.")
            else:
                messages.warning(request, "Please select at least one file.")

            return redirect(reverse("doctors:verification_status"))
    else:
        form = DoctorCredentialUploadForm()

    return render(request, "doctors/upload_credentials.html", {
        "form": form,
        "verification": verification,
    })


# ============================================
# DOCTOR PROFILE
# ============================================


class DoctorProfileForm(django_forms.Form):
    """Form for editing the doctor's public profile."""
    bio = django_forms.CharField(
        label="Bio",
        required=False,
        widget=django_forms.Textarea(attrs={
            "class": _TEXT_INPUT_CLASSES + " resize-none",
            "rows": 4,
            "placeholder": "Write a short bio about your experience and specialties...",
        }),
    )
    years_of_experience = django_forms.IntegerField(
        label="Years of Experience",
        required=False,
        min_value=0,
        max_value=70,
        widget=django_forms.NumberInput(attrs={
            "class": _TEXT_INPUT_CLASSES,
            "placeholder": "e.g. 10",
        }),
    )


@login_required
@doctor_required
def doctor_profile_view(request):
    """Read-only view of the doctor's profile."""
    user = request.user

    profile, _ = DoctorProfile.objects.get_or_create(user=user)
    specialties = profile.specialties.all()
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .exclude(role__in=["SECRETARY", "MAIN_DOCTOR"])
        .select_related("clinic")
    )

    return render(request, "doctors/doctor_profile.html", {
        "profile": profile,
        "specialties": specialties,
        "memberships": memberships,
    })


@login_required
@doctor_required
def doctor_verify_phone_view(request):
    """Send / confirm OTP to mark the doctor's phone as verified."""
    user = request.user

    if user.is_verified:
        return redirect(reverse("doctors:doctor_profile"))

    phone = user.phone

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "send":
            success, _ = request_otp(phone)
            if success:
                messages.success(request, "A verification code was sent to your phone.")
            else:
                messages.error(request, "Could not send the code. Please try again.")
            return redirect(reverse("doctors:verify_phone"))

        if action == "resend":
            remaining = get_remaining_resends(phone)
            if remaining <= 0:
                messages.error(request, "You have reached the maximum OTP requests for today.")
            else:
                success, _ = request_otp(phone)
                if success:
                    messages.success(request, "A new code was sent to your phone.")
                else:
                    messages.error(request, "Could not resend the code. Please try again.")
            return redirect(reverse("doctors:verify_phone"))

        # Confirm OTP
        entered_otp = request.POST.get("otp", "").strip()
        if not entered_otp:
            messages.error(request, "Please enter the verification code.")
        else:
            success, _ = verify_otp(phone, entered_otp)
            if success:
                user.is_verified = True
                user.save(update_fields=["is_verified"])
                messages.success(request, "Phone number verified successfully.")
                return redirect(reverse("doctors:doctor_profile"))
            else:
                messages.error(request, "Invalid or expired code. Please try again.")

    otp_sent = is_in_cooldown(phone)
    return render(request, "doctors/verify_phone.html", {
        "phone": phone,
        "otp_sent": otp_sent,
        "remaining_resends": get_remaining_resends(phone),
        "cooldown": otp_sent,
        "cooldown_seconds": get_cooldown_remaining(phone),
    })


@login_required
@doctor_required
def doctor_edit_profile_view(request):
    """Edit doctor's bio, experience, and email (email change via OTP)."""
    user = request.user

    profile, _ = DoctorProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        form = DoctorProfileForm(request.POST)
        email = request.POST.get("email", "").strip().lower()
        current_email = user.email or ""
        email_changed = email and email != current_email.lower()

        if email_changed:
            from django.core.validators import validate_email as _validate_email
            from django.core.exceptions import ValidationError as DjangoValidationError
            from django.contrib.auth import get_user_model
            _User = get_user_model()
            try:
                _validate_email(email)
                if _User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    messages.error(request, "This email is already registered.")
                    return render(request, "doctors/doctor_edit_profile.html", {
                        "form": form,
                        "profile": profile,
                    })
                if form.is_valid():
                    profile.bio = form.cleaned_data.get("bio", "")
                    profile.years_of_experience = form.cleaned_data.get("years_of_experience")
                    profile.save()
                request.session["pending_email_change"] = email
                return redirect(reverse("accounts:change_email_request"))
            except DjangoValidationError:
                messages.error(request, "Invalid email address.")
                return render(request, "doctors/doctor_edit_profile.html", {
                    "form": form,
                    "profile": profile,
                })

        if form.is_valid():
            profile.bio = form.cleaned_data.get("bio", "")
            profile.years_of_experience = form.cleaned_data.get("years_of_experience")
            profile.save()
            messages.success(request, "Profile updated successfully.")
            return redirect(reverse("doctors:doctor_profile"))

    else:
        form = DoctorProfileForm(initial={
            "bio": profile.bio,
            "years_of_experience": profile.years_of_experience,
        })

    return render(request, "doctors/doctor_edit_profile.html", {
        "form": form,
        "profile": profile,
    })


# ============================================
# PER-CLINIC CREDENTIAL UPLOAD
# ============================================


class ClinicCredentialUploadForm(django_forms.Form):
    """Form for uploading a specialty certificate for a specific clinic-credential."""
    specialty_certificate = django_forms.FileField(
        label="Specialty Certificate",
        required=True,
        widget=django_forms.ClearableFileInput(attrs={
            "class": _FILE_INPUT_CLASSES,
            "accept": ".jpg,.jpeg,.png,.pdf",
        }),
    )

    def clean_specialty_certificate(self):
        f = self.cleaned_data.get("specialty_certificate")
        if f:
            validate_file_extension(f)
            validate_file_signature(f)
            validate_file_size(f)
        return f


@login_required
def doctor_upload_clinic_credential(request, credential_id):
    """Upload a specialty certificate for a specific clinic credential."""
    user = request.user
    credential = get_object_or_404(
        ClinicDoctorCredential, id=credential_id, doctor=user
    )

    if request.method == "POST":
        form = ClinicCredentialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            credential.specialty_certificate = form.cleaned_data["specialty_certificate"]
            if credential.credential_status in ("CREDENTIALS_PENDING", "CREDENTIALS_REJECTED"):
                credential.credential_status = "CREDENTIALS_PENDING"
            credential.save()
            spec_name = credential.specialty.name if credential.specialty else "General"
            messages.success(
                request,
                f"Specialty certificate ({spec_name}) uploaded successfully. It will be reviewed."
            )
            return redirect(reverse("doctors:verification_status"))
    else:
        form = ClinicCredentialUploadForm()

    return render(request, "doctors/upload_clinic_credential.html", {
        "form": form,
        "credential": credential,
    })


# ============================================
# DOCTOR SCHEDULE MANAGEMENT
# ============================================


@login_required
@doctor_required
def my_schedule(request):
    """Doctor manages their weekly availability schedule per clinic."""
    user = request.user

    # All active clinic memberships (deduplicated by clinic)
    _all_memberships = list(
        ClinicStaff.objects.filter(user=user, is_active=True, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("clinic__name")
    )
    seen_clinic_ids = set()
    memberships = []
    for _m in _all_memberships:
        if _m.clinic_id not in seen_clinic_ids:
            seen_clinic_ids.add(_m.clinic_id)
            memberships.append(_m)

    # Determine selected clinic (from GET or POST param)
    clinic_id_param = request.GET.get("clinic_id") or request.POST.get("clinic_id")
    selected_clinic = None

    if memberships:
        if clinic_id_param:
            try:
                cid = int(clinic_id_param)
                selected_clinic = next((m.clinic for m in memberships if m.clinic_id == cid), None)
            except (ValueError, TypeError):
                pass
        if selected_clinic is None:
            selected_clinic = memberships[0].clinic

    # Handle POST: add or delete a slot
    if request.method == "POST" and selected_clinic:
        action = request.POST.get("action")
        redirect_url = reverse("doctors:my_schedule") + f"?clinic_id={selected_clinic.id}"

        if action == "add":
            day_str = request.POST.get("day_of_week", "")
            start_str = request.POST.get("start_time", "")
            end_str = request.POST.get("end_time", "")
            try:
                slot = DoctorAvailability(
                    doctor=user,
                    clinic=selected_clinic,
                    day_of_week=int(day_str),
                    start_time=start_str,
                    end_time=end_str,
                    is_active=True,
                )
                slot.full_clean()
                slot.save()
                messages.success(request, "Working hours added successfully.")
            except ValidationError as e:
                if hasattr(e, "message_dict"):
                    for errs in e.message_dict.values():
                        for err in errs:
                            messages.error(request, err)
                else:
                    for err in e.messages:
                        messages.error(request, err)
            except Exception as e:
                messages.error(request, str(e))
            return redirect(redirect_url)

        elif action == "delete":
            slot_id = request.POST.get("slot_id")
            try:
                slot = DoctorAvailability.objects.get(id=slot_id, doctor=user, clinic=selected_clinic)
                slot.delete()
                messages.success(request, "Working hours deleted.")
            except DoctorAvailability.DoesNotExist:
                messages.error(request, "Time slot not found.")
            return redirect(redirect_url)

    # Build per-day data for the template
    from clinics.models import ClinicWorkingHours
    clinic_wh_map = {}
    my_slots_map = {}

    if selected_clinic:
        for wh in ClinicWorkingHours.objects.filter(clinic=selected_clinic).order_by("weekday", "start_time"):
            clinic_wh_map.setdefault(wh.weekday, []).append(wh)

        for slot in DoctorAvailability.objects.filter(
            doctor=user, clinic=selected_clinic
        ).order_by("day_of_week", "start_time"):
            my_slots_map.setdefault(slot.day_of_week, []).append(slot)

    days_data = []
    for day_num, day_name in DoctorAvailability.DAY_CHOICES:
        whs = clinic_wh_map.get(day_num, [])
        is_closed = bool(whs) and any(wh.is_closed for wh in whs)
        open_ranges = [(wh.start_time, wh.end_time) for wh in whs if not wh.is_closed]
        days_data.append({
            "day_num": day_num,
            "day_name": day_name,
            "working_hours": whs,
            "is_closed": is_closed,
            "open_ranges": open_ranges,
            "slots": my_slots_map.get(day_num, []),
            "clinic_has_hours": bool(whs),
        })

    return render(request, "doctors/my_schedule.html", {
        "memberships": memberships,
        "selected_clinic": selected_clinic,
        "selected_clinic_id": selected_clinic.id if selected_clinic else None,
        "days_data": days_data,
        "day_choices": DoctorAvailability.DAY_CHOICES,
    })


# ============================================
# DOCTOR APPOINTMENT TYPES (self-service)
# ============================================


@login_required
@doctor_required
def my_appointment_types(request):
    """Doctor manages their own enabled appointment types per clinic."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    from appointments.services.appointment_type_service import (
        get_doctor_type_assignments,
        set_doctor_clinic_appointment_types,
    )
    from django.core.exceptions import ValidationError as DjangoValidationError

    user = request.user

    # All active clinics the doctor belongs to (deduplicated by clinic)
    _all_memberships = list(
        ClinicStaff.objects.filter(user=user, is_active=True, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("clinic__name")
    )
    seen_clinic_ids = set()
    memberships = []
    for _m in _all_memberships:
        if _m.clinic_id not in seen_clinic_ids:
            seen_clinic_ids.add(_m.clinic_id)
            memberships.append(_m)

    # Handle POST: update types for a specific clinic
    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        if clinic_id:
            try:
                clinic_id = int(clinic_id)
                # Verify doctor is in that clinic
                membership = next((m for m in memberships if m.clinic_id == clinic_id), None)
                if not membership:
                    _messages.error(request, "You don't have permission to edit this clinic.")
                else:
                    active_ids = request.POST.getlist("type_ids")
                    set_doctor_clinic_appointment_types(user.id, clinic_id, active_ids)
                    _messages.success(request, f"Appointment types saved for {membership.clinic.name}.")
            except (ValueError, DjangoValidationError) as e:
                err = e.messages[0] if hasattr(e, "messages") else str(e)
                _messages.error(request, err)
        return redirect(_reverse("doctors:my_appointment_types"))

    # Build per-clinic assignment data for the template
    clinic_data = []
    for m in memberships:
        assignments = get_doctor_type_assignments(user.id, m.clinic_id)
        if assignments:  # only show clinics that have at least one appointment type defined
            clinic_data.append({
                "clinic": m.clinic,
                "assignments": assignments,
            })

    # Annotate each assignment with intake form question count
    for item in clinic_data:
        for a in item["assignments"]:
            template = DoctorIntakeFormTemplate.objects.filter(
                doctor=user,
                appointment_type=a["appointment_type"],
                is_active=True,
            ).first()
            a["question_count"] = template.questions.count() if template else 0

    return render(request, "doctors/my_appointment_types.html", {
        "clinic_data": clinic_data,
    })


# ============================================
# INTAKE FORM BUILDER
# ============================================

def _doctor_required(request):
    """Returns None if the user is a doctor, otherwise a redirect response.

    Thin shim around ``_is_doctor`` for call sites (e.g. catalog views) that prefer a
    returned response over the ``@doctor_required`` decorator.
    """
    if _is_doctor(request.user):
        return None
    messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
    return redirect(reverse("accounts:home"))


@login_required
def intake_form_builder(request, appointment_type_id):
    """
    Doctor builds / edits the intake form for a specific appointment type.
    GET  → show current template + questions.
    POST → save template title/description (creates template if needed).
    """
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    user = request.user

    # Fetch appointment type and verify doctor belongs to that clinic
    appointment_type = get_object_or_404(AppointmentType, pk=appointment_type_id, is_active=True)
    membership = ClinicStaff.objects.filter(
        user=user,
        clinic=appointment_type.clinic,
        is_active=True,
        revoked_at__isnull=True,
    ).first()
    if not membership:
        _messages.error(request, "You don't have permission to manage forms for this clinic.")
        return redirect(_reverse("doctors:my_appointment_types"))

    template, _ = DoctorIntakeFormTemplate.objects.get_or_create(
        doctor=user,
        appointment_type=appointment_type,
        defaults={
            "title": f"{appointment_type.name} Form",
            "is_active": True,
        },
    )

    if request.method == "POST" and "save_template" in request.POST:
        title_ar = request.POST.get("title_ar", "").strip()
        description = request.POST.get("description", "").strip()
        if not title_ar:
            _messages.error(request, "Form title is required.")
        else:
            template.title_ar = title_ar
            template.title = title_ar  # keep in sync for display_title
            template.description = description
            template.show_reason_field = "show_reason_field" in request.POST
            template.reason_field_label = request.POST.get("reason_field_label", "").strip()
            template.reason_field_placeholder = request.POST.get("reason_field_placeholder", "").strip()
            template.reason_field_required = "reason_field_required" in request.POST
            template.save(update_fields=[
                "title", "title_ar", "description",
                "show_reason_field", "reason_field_label",
                "reason_field_placeholder", "reason_field_required",
                "updated_at",
            ])
            _messages.success(request, "Form information saved.")
        return redirect(_reverse("doctors:intake_form_builder", args=[appointment_type_id]))

    questions = list(template.questions.order_by("order"))

    # Build follow-up structure: which questions are targets of SHOW rules
    rules_qs = DoctorIntakeRule.objects.filter(
        source_question__template=template,
        action=DoctorIntakeRule.Action.SHOW,
    ).select_related("target_question")

    # Map source_question_id → list of rules (each with target question)
    from collections import defaultdict as _dd
    followups_by_source = _dd(list)
    target_ids = set()
    for rule in rules_qs:
        followups_by_source[rule.source_question_id].append(rule)
        target_ids.add(rule.target_question_id)

    # Triggerable field types (can have follow-up questions)
    triggerable_types = {
        DoctorIntakeQuestion.FieldType.CHECKBOX,
        DoctorIntakeQuestion.FieldType.SELECT,
        DoctorIntakeQuestion.FieldType.MULTISELECT,
    }

    # Only top-level questions (not follow-up targets) shown in main list
    top_questions = [q for q in questions if q.id not in target_ids]

    next_order = (max(q.order for q in questions) + 1) if questions else 0

    return render(request, "doctors/intake_form_builder.html", {
        "appointment_type": appointment_type,
        "template": template,
        "questions": top_questions,
        "followups_by_source": dict(followups_by_source),
        "triggerable_types": triggerable_types,
        "next_order": next_order,
        "field_types": DoctorIntakeQuestion.FieldType.choices,
    })


@login_required
def intake_question_add(request, template_id):
    """Add a question to an intake form template."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    import json as _json

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    apt_type_id = template.appointment_type_id

    if request.method != "POST":
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    question_text_ar = request.POST.get("question_text_ar", "").strip()
    field_type = request.POST.get("field_type", DoctorIntakeQuestion.FieldType.TEXT)
    is_required = request.POST.get("is_required") == "on"
    placeholder = request.POST.get("placeholder", "").strip()
    help_text_content = request.POST.get("help_text_content", "").strip()

    # Parse order (auto-increment to end)
    existing_max = template.questions.order_by("-order").values_list("order", flat=True).first()
    order = (existing_max + 1) if existing_max is not None else 0

    # Choices for SELECT/MULTISELECT
    choices = []
    if field_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
        raw = request.POST.get("choices_raw", "")
        choices = [c.strip() for c in raw.splitlines() if c.strip()]
        if len(choices) < 2:
            _messages.error(request, "Please add at least two choices for list fields.")
            return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    try:
        DoctorIntakeQuestion.objects.create(
            template=template,
            question_text=question_text_ar,
            question_text_ar=question_text_ar,
            field_type=field_type,
            is_required=is_required,
            order=order,
            placeholder=placeholder,
            help_text_content=help_text_content,
            choices=choices,
        )
        _messages.success(request, "Question added.")
    except Exception as e:
        _messages.error(request, f"Error adding question: {e}")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_question_edit(request, template_id, question_id):
    """Edit an existing intake question."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        question_text_ar = request.POST.get("question_text_ar", "").strip()
        field_type = request.POST.get("field_type", question.field_type)
        is_required = request.POST.get("is_required") == "on"
        placeholder = request.POST.get("placeholder", "").strip()
        help_text_content = request.POST.get("help_text_content", "").strip()

        choices = question.choices
        if field_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
            raw = request.POST.get("choices_raw", "")
            choices = [c.strip() for c in raw.splitlines() if c.strip()]
            if len(choices) < 2:
                _messages.error(request, "Please add at least two choices for list fields.")
                return redirect(_reverse("doctors:intake_question_edit", args=[template_id, question_id]))

        question.question_text = question_text_ar
        question.question_text_ar = question_text_ar
        question.field_type = field_type
        question.is_required = is_required
        question.placeholder = placeholder
        question.help_text_content = help_text_content
        question.choices = choices
        question.save()
        _messages.success(request, "Question updated.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    return render(request, "doctors/intake_question_form.html", {
        "template": template,
        "question": question,
        "appointment_type": template.appointment_type,
        "field_types": DoctorIntakeQuestion.FieldType.choices,
        "choices_raw": "\n".join(question.choices) if question.choices else "",
        "is_edit": True,
    })


@login_required
def intake_question_delete(request, template_id, question_id):
    """Delete a question from an intake form template."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        # Collect every descendant question ID (BFS) so they are deleted too
        descendant_ids = set()
        queue = [question.id]
        while queue:
            current_id = queue.pop()
            child_ids = list(
                DoctorIntakeRule.objects.filter(
                    source_question_id=current_id,
                    action=DoctorIntakeRule.Action.SHOW,
                ).values_list("target_question_id", flat=True)
            )
            for child_id in child_ids:
                if child_id not in descendant_ids:
                    descendant_ids.add(child_id)
                    queue.append(child_id)

        if descendant_ids:
            DoctorIntakeQuestion.objects.filter(id__in=descendant_ids).delete()

        question.delete()
        _messages.success(request, "Question and all its sub-questions deleted.")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_followup_add(request, template_id, question_id):
    """Add a follow-up (conditional) question triggered when source has a specific answer."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse
    from django.db import transaction

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    source_question = get_object_or_404(DoctorIntakeQuestion, pk=question_id, template=template)
    apt_type_id = template.appointment_type_id

    if request.method != "POST":
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    trigger_value = request.POST.get("trigger_value", "").strip()
    followup_text = request.POST.get("followup_text_ar", "").strip()
    followup_type = request.POST.get("followup_field_type", DoctorIntakeQuestion.FieldType.TEXT)
    followup_required = request.POST.get("followup_is_required") == "on"
    followup_placeholder = request.POST.get("followup_placeholder", "").strip()

    if not trigger_value:
        _messages.error(request, "Trigger value is required.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    if not followup_text:
        _messages.error(request, "Sub-question text is required.")
        return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    followup_choices = []
    if followup_type in (DoctorIntakeQuestion.FieldType.SELECT, DoctorIntakeQuestion.FieldType.MULTISELECT):
        raw = request.POST.get("followup_choices_raw", "")
        followup_choices = [c.strip() for c in raw.splitlines() if c.strip()]
        if len(followup_choices) < 2:
            _messages.error(request, "Please add at least two choices for list fields.")
            return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

    try:
        with transaction.atomic():
            existing_max = template.questions.order_by("-order").values_list("order", flat=True).first()
            order = (existing_max + 1) if existing_max is not None else 0

            followup_question = DoctorIntakeQuestion.objects.create(
                template=template,
                question_text=followup_text,
                question_text_ar=followup_text,
                field_type=followup_type,
                is_required=followup_required,
                order=order,
                placeholder=followup_placeholder,
                choices=followup_choices,
            )

            DoctorIntakeRule.objects.create(
                source_question=source_question,
                expected_value=trigger_value,
                operator=DoctorIntakeRule.Operator.EQUALS,
                target_question=followup_question,
                action=DoctorIntakeRule.Action.SHOW,
            )

        _messages.success(request, "Sub-question added.")
    except Exception as e:
        _messages.error(request, f"Error: {e}")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))


@login_required
def intake_rule_delete(request, template_id, rule_id):
    """Delete a follow-up rule and its orphaned target question."""
    from django.contrib import messages as _messages
    from django.urls import reverse as _reverse

    denied = _doctor_required(request)
    if denied:
        return denied

    template = get_object_or_404(DoctorIntakeFormTemplate, pk=template_id, doctor=request.user)
    rule = get_object_or_404(
        DoctorIntakeRule,
        pk=rule_id,
        source_question__template=template,
    )
    apt_type_id = template.appointment_type_id

    if request.method == "POST":
        target_question = rule.target_question
        rule.delete()
        if not target_question.rules_as_target.exists():
            target_question.delete()
        _messages.success(request, "Sub-question deleted.")

    return redirect(_reverse("doctors:intake_form_builder", args=[apt_type_id]))

# ══════════════════════════════════════════════════════════════════════════════
# PATIENT WORKSPACE
# ══════════════════════════════════════════════════════════════════════════════

from patients.models import (
    ClinicalNote, ClinicalNoteAddendum, Order, Prescription, PrescriptionItem, MedicalRecord,
    ClinicPatient, PatientProfile,
)


def _ws_access(request, patient_id):
    """
    Verify the doctor can access this patient.
    Returns a context dict or None if access denied.
    """
    user = request.user
    if not (user.is_authenticated and (
        "DOCTOR" in (user.roles or []) or "MAIN_DOCTOR" in (user.roles or [])
    )):
        return None

    doctor_clinic_ids = set(
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .values_list("clinic_id", flat=True)
    )

    cp_qs = ClinicPatient.objects.filter(
        patient_id=patient_id, clinic_id__in=doctor_clinic_ids
    ).select_related("clinic")
    if not cp_qs.exists():
        return None

    patient = get_object_or_404(User, pk=patient_id)
    shared_clinics = [cp.clinic for cp in cp_qs]
    shared_clinic_ids = [c.id for c in shared_clinics]
    profile = getattr(patient, "patient_profile", None)

    from datetime import date as _date
    age = None
    if profile and profile.date_of_birth:
        today = _date.today()
        dob = profile.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    return {
        "doctor": user,
        "patient": patient,
        "profile": profile,
        "age": age,
        "shared_clinic_ids": shared_clinic_ids,
        "clinics": shared_clinics,
    }


def _ws_last_visit(patient_id, doctor):
    return (
        Appointment.objects.filter(doctor=doctor, patient_id=patient_id)
        .exclude(status=Appointment.Status.CANCELLED)
        .order_by("-appointment_date")
        .values_list("appointment_date", flat=True)
        .first()
    )


@login_required
def patient_workspace(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied.")

    tab = request.GET.get("tab", "overview")
    if tab not in {"overview", "notes", "orders", "prescriptions", "records"}:
        tab = "overview"

    ctx["active_tab"] = tab
    ctx["last_visit"] = _ws_last_visit(patient_id, ctx["doctor"])
    _rtl = getattr(request, "LANGUAGE_CODE", "ar") == "ar"
    ctx["tabs"] = [
        ("overview",      "نظرة عامة"       if _rtl else "Overview",       "fa-solid fa-chart-pie"),
        ("notes",         "ملاحظات طبية"     if _rtl else "Clinical Notes", "fa-solid fa-file-medical"),
        ("orders",        "الطلبات"          if _rtl else "Orders",         "fa-solid fa-flask"),
        ("prescriptions", "الوصفات"          if _rtl else "Prescriptions",  "fa-solid fa-prescription"),
        ("records",       "السجلات"          if _rtl else "Records",        "fa-solid fa-folder-open"),
    ]
    patient = ctx["patient"]
    cids = ctx["shared_clinic_ids"]

    if tab == "overview":
        ctx.update(_ws_overview_data(patient, cids, viewer=ctx["doctor"]))
        ctx["active_note_sections"] = _get_active_note_sections(ctx["doctor"])
    elif tab == "notes":
        ctx.update(_ws_notes_data(patient, cids, request))
        ctx["active_note_sections"] = _get_active_note_sections(ctx["doctor"])
    elif tab == "orders":
        ctx.update(_ws_orders_data(patient, cids, request))
    elif tab == "prescriptions":
        ctx.update(_ws_prescriptions_data(patient, cids))
    elif tab == "records":
        ctx.update(_ws_records_data(patient, cids, request))

    if request.headers.get("HX-Request"):
        template_map = {
            "overview":      "doctors/partials/ws_overview.html",
            "notes":         "doctors/partials/ws_notes.html",
            "orders":        "doctors/partials/ws_orders.html",
            "prescriptions": "doctors/partials/ws_prescriptions.html",
            "records":       "doctors/partials/ws_records.html",
        }
        return render(request, template_map[tab], ctx)

    return render(request, "doctors/patient_workspace.html", ctx)


# ── Tab data helpers ──────────────────────────────────────────────────────────

def _ws_overview_data(patient, cids, viewer=None):
    all_notes = list(
        ClinicalNote.objects.filter(patient=patient, clinic_id__in=cids)
        .select_related("doctor", "clinic")
        .prefetch_related("addenda__doctor")
        .order_by("-created_at")
    )

    _annotate_notes_with_labeled_extras(all_notes)

    # Patient-profile staff notes the doctor may see: doctor-audience notes (shared with
    # secretaries) + the viewer's own private notes. Never secretary-only notes.
    staff_doctor_notes = _patient_doctor_notes(patient, cids, viewer=viewer)

    active_orders  = list(Order.objects.filter(patient=patient, clinic_id__in=cids, status=Order.Status.PENDING).select_related("doctor")[:8])
    latest_rx      = Prescription.objects.filter(patient=patient, clinic_id__in=cids, is_active=True).prefetch_related("items").first()
    recent_records = list(MedicalRecord.objects.filter(patient=patient, clinic_id__in=cids)[:5])
    lab_records    = [r for r in recent_records if r.category == 'LAB']

    # Build unified activity timeline (most recent 15 events across all types)
    tl_notes   = list(ClinicalNote.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic").order_by("-created_at")[:6])
    tl_orders  = list(Order.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic").order_by("-created_at")[:6])
    tl_rxs     = list(Prescription.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic").prefetch_related("items").order_by("-created_at")[:6])
    tl_records = list(MedicalRecord.objects.filter(patient=patient, clinic_id__in=cids).select_related("uploaded_by").order_by("-uploaded_at")[:6])

    events = (
        [{"kind": "note",         "obj": n, "ts": n.created_at}  for n in tl_notes]
        + [{"kind": "order",      "obj": o, "ts": o.created_at}  for o in tl_orders]
        + [{"kind": "prescription","obj": r, "ts": r.created_at} for r in tl_rxs]
        + [{"kind": "record",     "obj": rec, "ts": rec.uploaded_at} for rec in tl_records]
    )
    events.sort(key=lambda x: x["ts"], reverse=True)

    return {
        "all_notes":      all_notes,
        "staff_doctor_notes": staff_doctor_notes,
        "active_orders":  active_orders,
        "latest_rx":      latest_rx,
        "recent_records": recent_records,
        "lab_records":    lab_records,
        "timeline_events": events[:15],
    }


def _ws_notes_data(patient, cids, request):
    from django.core.paginator import Paginator
    qs = (
        ClinicalNote.objects.filter(patient=patient, clinic_id__in=cids)
        .select_related("doctor", "clinic")
        .prefetch_related("addenda__doctor")
    )
    paginator = Paginator(qs, 10)
    notes_page = paginator.get_page(request.GET.get("notes_page", 1))

    notes_list = list(notes_page.object_list)
    _annotate_notes_with_labeled_extras(notes_list)
    notes_page.object_list = notes_list

    return {"notes": notes_page, "notes_paginator": paginator}


def _ws_orders_data(patient, cids, request):
    from clinics.models import DrugFamily
    type_filter = request.GET.get("order_type", "")
    qs = Order.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic")
    if type_filter:
        qs = qs.filter(order_type=type_filter)
    drug_families = list(
        DrugFamily.objects.filter(clinic_id__in=cids).values("id", "name", "clinic_id").order_by("name")
    )
    return {
        "orders": list(qs[:50]),
        "order_type_filter": type_filter,
        "order_types": Order.OrderType.choices,
        "drug_families": drug_families,
        "catalog_clinic_ids": list(cids),
    }


def _ws_prescriptions_data(patient, cids):
    qs = (
        Prescription.objects.filter(patient=patient, clinic_id__in=cids)
        .select_related("doctor", "clinic")
        .prefetch_related("items")
    )
    return {
        "active_prescriptions": list(qs.filter(is_active=True)),
        "inactive_prescriptions": list(qs.filter(is_active=False)),
    }


def _ws_records_data(patient, cids, request):
    cat_filter = request.GET.get("record_cat", "")
    qs = MedicalRecord.objects.filter(patient=patient, clinic_id__in=cids).select_related("uploaded_by")
    if cat_filter:
        qs = qs.filter(category=cat_filter)
    return {
        "records": list(qs[:50]),
        "record_cat_filter": cat_filter,
        "record_categories": MedicalRecord.Category.choices,
    }


# ── Clinical Notes CRUD ───────────────────────────────────────────────────────

@login_required
def ws_note_add(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        try:
            clinic_id = int(clinic_id)
            if clinic_id not in ctx["shared_clinic_ids"]:
                clinic_id = ctx["shared_clinic_ids"][0]
        except (TypeError, ValueError):
            clinic_id = ctx["shared_clinic_ids"][0]

        # Resolve sections first so we can snapshot labels alongside the content.
        active_sections = _get_active_note_sections(ctx["doctor"])
        extra_sections  = _collect_extra_sections(request.POST)
        ClinicalNote.objects.create(
            patient=ctx["patient"],
            clinic_id=clinic_id,
            doctor=ctx["doctor"],
            subjective=request.POST.get("subjective", "").strip(),
            objective=request.POST.get("objective", "").strip(),
            assessment=request.POST.get("assessment", "").strip(),
            plan=request.POST.get("plan", "").strip(),
            free_text=request.POST.get("free_text", "").strip(),
            extra_sections=extra_sections,
            extra_sections_labels=_collect_extra_sections_labels(active_sections),
            is_secretary_allowed=request.POST.get("is_secretary_allowed") == "on",
        )
        ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["note_saved"]           = True
        ctx["active_note_sections"] = active_sections
        return render(request, "doctors/partials/ws_notes.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_note_edit(request, patient_id, note_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    note = get_object_or_404(ClinicalNote, pk=note_id, patient_id=patient_id, doctor=ctx["doctor"])

    if request.method == "POST":
        # Resolve sections first so we can snapshot labels at edit time too.
        active_sections         = _get_active_note_sections(ctx["doctor"])
        note.subjective         = request.POST.get("subjective", "").strip()
        note.objective          = request.POST.get("objective", "").strip()
        note.assessment         = request.POST.get("assessment", "").strip()
        note.plan               = request.POST.get("plan", "").strip()
        note.free_text          = request.POST.get("free_text", "").strip()
        note.extra_sections     = _collect_extra_sections(request.POST)
        note.extra_sections_labels = _collect_extra_sections_labels(active_sections)
        note.is_secretary_allowed = request.POST.get("is_secretary_allowed") == "on"
        note.save()
        ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["note_saved"]           = True
        ctx["active_note_sections"] = active_sections
        return render(request, "doctors/partials/ws_notes.html", ctx)

    # GET — open the form pre-filled with the note's existing content
    ctx["edit_note"]          = note
    ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
    # Pass sections with pre-filled values so the template can populate the form
    ctx["active_note_sections"] = _get_active_note_sections(ctx["doctor"], note=note)
    return render(request, "doctors/partials/ws_notes.html", ctx)


@login_required
def ws_note_delete(request, patient_id, note_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        note = get_object_or_404(ClinicalNote, pk=note_id, patient_id=patient_id, doctor=ctx["doctor"])
        note.delete()
        ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["active_note_sections"] = _get_active_note_sections(ctx["doctor"])
        return render(request, "doctors/partials/ws_notes.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_note_addendum_add(request, patient_id, note_id):
    """Append an addendum to an existing note. Any doctor sharing a clinic with
    the patient may add one (not just the note's author). Returns the updated
    per-note addenda fragment so the rest of the page/panel stays intact."""
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    # Note must belong to the patient and a clinic shared with this doctor.
    note = get_object_or_404(
        ClinicalNote,
        pk=note_id,
        patient_id=patient_id,
        clinic_id__in=ctx["shared_clinic_ids"],
    )

    if request.method == "POST":
        text = request.POST.get("addendum_text", "").strip()
        if text:
            ClinicalNoteAddendum.objects.create(
                note=note, doctor=ctx["doctor"], text=text
            )
        return render(
            request,
            "doctors/partials/_note_addenda.html",
            {"note": note, "patient": ctx["patient"], "doctor": ctx["doctor"]},
        )

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_note_print(request, patient_id, note_id):
    """Render a printable (browser 'Save as PDF') version of a single clinical note
    for the treating doctor."""
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    note = get_object_or_404(
        ClinicalNote.objects.select_related("doctor", "clinic"),
        pk=note_id,
        patient_id=patient_id,
        clinic_id__in=ctx["shared_clinic_ids"],
    )
    _annotate_notes_with_labeled_extras([note])
    return render(
        request,
        "doctors/clinical_note_print.html",
        {"note": note, "patient": ctx["patient"], "clinic": note.clinic},
    )


# ── Orders CRUD ───────────────────────────────────────────────────────────────

@login_required
def ws_order_add(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        try:
            clinic_id = int(clinic_id)
            if clinic_id not in ctx["shared_clinic_ids"]:
                clinic_id = ctx["shared_clinic_ids"][0]
        except (TypeError, ValueError):
            clinic_id = ctx["shared_clinic_ids"][0]

        order_type = request.POST.get("order_type", "").upper()
        valid_types = [c[0] for c in Order.OrderType.choices]
        if order_type not in valid_types:
            order_type = Order.OrderType.LAB

        Order.objects.create(
            patient=ctx["patient"],
            clinic_id=clinic_id,
            doctor=ctx["doctor"],
            order_type=order_type,
            title=request.POST.get("title", "").strip(),
            notes=request.POST.get("notes", "").strip(),
            dosage=request.POST.get("dosage", "").strip(),
            frequency=request.POST.get("frequency", "").strip(),
            duration=request.POST.get("duration", "").strip(),
        )
        ctx.update(_ws_orders_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["order_saved"] = True
        return render(request, "doctors/partials/ws_orders.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_order_update(request, patient_id, order_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        order = get_object_or_404(
            Order, pk=order_id, patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        new_status = request.POST.get("status", "").upper()
        if new_status in [c[0] for c in Order.Status.choices]:
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])
        ctx.update(_ws_orders_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        return render(request, "doctors/partials/ws_orders.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_order_edit(request, patient_id, order_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    order = get_object_or_404(
        Order, pk=order_id, patient_id=patient_id,
        clinic_id__in=ctx["shared_clinic_ids"]
    )

    if request.method == "POST":
        order_type = request.POST.get("order_type", order.order_type).upper()
        valid_types = [c[0] for c in Order.OrderType.choices]
        if order_type not in valid_types:
            order_type = order.order_type
        order.order_type = order_type
        title = request.POST.get("title", "").strip()
        if title:
            order.title = title
        order.notes = request.POST.get("notes", "").strip()
        order.dosage = request.POST.get("dosage", "").strip()
        order.frequency = request.POST.get("frequency", "").strip()
        order.duration = request.POST.get("duration", "").strip()
        order.save()
        ctx.update(_ws_orders_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["order_saved"] = True
        return render(request, "doctors/partials/ws_orders.html", ctx)

    ctx["edit_order"] = order
    ctx.update(_ws_orders_data(ctx["patient"], ctx["shared_clinic_ids"], request))
    return render(request, "doctors/partials/ws_orders.html", ctx)


@login_required
def ws_order_delete(request, patient_id, order_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        order = get_object_or_404(
            Order, pk=order_id, patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        order.delete()
        ctx.update(_ws_orders_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        return render(request, "doctors/partials/ws_orders.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


# ── Order Catalog HTMX Search ─────────────────────────────────────────────────

@login_required
def htmx_catalog_drug_search(request, patient_id):
    """HTMX endpoint: search clinic drug catalog for the order picker."""
    from clinics.models import DrugProduct
    from django.http import HttpResponseForbidden

    ctx = _ws_access(request, patient_id)
    if ctx is None:
        return HttpResponseForbidden()

    try:
        clinic_id = int(request.GET.get("clinic_id", 0))
    except (ValueError, TypeError):
        clinic_id = 0
    if clinic_id not in ctx["shared_clinic_ids"]:
        clinic_id = ctx["shared_clinic_ids"][0] if ctx["shared_clinic_ids"] else 0
    if not clinic_id:
        return render(request, "doctors/partials/catalog_drug_results.html", {"drugs": []})

    from django.db.models import Q, Case, When, IntegerField
    from doctors.models import DoctorFavouriteDrug

    mode = request.GET.get("mode", "generic")
    qs = DrugProduct.objects.filter(clinic_id=clinic_id, is_active=True).select_related("family")

    family_id = request.GET.get("family_id", "").strip()
    if family_id:
        qs = qs.filter(family_id=family_id)

    q = request.GET.get("drug_q", "").strip()
    if q:
        qs = qs.filter(Q(generic_name__icontains=q) | Q(commercial_name__icontains=q))

    fav_ids = set(
        DoctorFavouriteDrug.objects
        .filter(user=request.user, drug_product__clinic_id=clinic_id)
        .values_list("drug_product_id", flat=True)
    )
    fav_rank = Case(When(id__in=fav_ids, then=0), default=1, output_field=IntegerField())
    qs = qs.annotate(_fav_rank=fav_rank)

    if mode == "commercial":
        drugs = list(qs.order_by("_fav_rank", "commercial_name", "generic_name")[:60])
    else:
        drugs = list(qs.order_by("_fav_rank", "generic_name")[:60])

    for d in drugs:
        d.is_favourite = d.id in fav_ids

    return render(request, "doctors/partials/catalog_drug_results.html", {
        "drugs": drugs,
        "mode": mode,
        "fav_ids": fav_ids,
    })


@login_required
def htmx_catalog_nondrug_search(request, patient_id):
    """HTMX endpoint: search clinic non-drug catalog for the order picker."""
    from clinics.models import OrderCatalogItem
    from django.http import HttpResponseForbidden

    ctx = _ws_access(request, patient_id)
    if ctx is None:
        return HttpResponseForbidden()

    try:
        clinic_id = int(request.GET.get("clinic_id", 0))
    except (ValueError, TypeError):
        clinic_id = 0
    if clinic_id not in ctx["shared_clinic_ids"]:
        clinic_id = ctx["shared_clinic_ids"][0] if ctx["shared_clinic_ids"] else 0
    if not clinic_id:
        return render(request, "doctors/partials/catalog_nondrug_results.html", {"items": []})

    category = request.GET.get("category", "").upper()
    valid = {c for c, _ in OrderCatalogItem.Category.choices}
    if category not in valid:
        return render(request, "doctors/partials/catalog_nondrug_results.html", {"items": []})

    q = request.GET.get("q", "").strip()
    qs = OrderCatalogItem.objects.filter(clinic_id=clinic_id, category=category, is_active=True)
    if q:
        qs = qs.filter(name__icontains=q)
    items = list(qs.order_by("name")[:60])

    return render(request, "doctors/partials/catalog_nondrug_results.html", {"items": items})


# ── Prescriptions ─────────────────────────────────────────────────────────────

@login_required
def ws_prescription_add(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        try:
            clinic_id = int(clinic_id)
            if clinic_id not in ctx["shared_clinic_ids"]:
                clinic_id = ctx["shared_clinic_ids"][0]
        except (TypeError, ValueError):
            clinic_id = ctx["shared_clinic_ids"][0]

        rx = Prescription.objects.create(
            patient=ctx["patient"],
            clinic_id=clinic_id,
            doctor=ctx["doctor"],
            notes=request.POST.get("rx_notes", "").strip(),
        )

        i = 1
        while True:
            med = request.POST.get(f"med_name_{i}", "").strip()
            if not med:
                break
            PrescriptionItem.objects.create(
                prescription=rx,
                medication_name=med,
                dosage=request.POST.get(f"dosage_{i}", "").strip(),
                frequency=request.POST.get(f"frequency_{i}", "").strip(),
                duration=request.POST.get(f"duration_{i}", "").strip(),
                instructions=request.POST.get(f"instructions_{i}", "").strip(),
            )
            i += 1

        if rx.items.count() == 0:
            rx.delete()
            ctx.update(_ws_prescriptions_data(ctx["patient"], ctx["shared_clinic_ids"]))
            ctx["rx_error"] = "Please add at least one medication."
            return render(request, "doctors/partials/ws_prescriptions.html", ctx)

        ctx.update(_ws_prescriptions_data(ctx["patient"], ctx["shared_clinic_ids"]))
        ctx["rx_saved"] = True
        return render(request, "doctors/partials/ws_prescriptions.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_prescription_print(request, patient_id, rx_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    rx = get_object_or_404(
        Prescription.objects.prefetch_related("items").select_related("doctor", "clinic"),
        pk=rx_id, patient_id=patient_id, clinic_id__in=ctx["shared_clinic_ids"]
    )
    ctx["rx"] = rx
    return render(request, "doctors/ws_prescription_print.html", ctx)


@login_required
def ws_prescription_print_active(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    active_rxs = list(
        Prescription.objects.filter(
            patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"],
            is_active=True,
        )
        .select_related("doctor", "clinic")
        .prefetch_related("items")
    )
    ctx["active_rxs"] = active_rxs
    ctx["all_active_items"] = [item for rx in active_rxs for item in rx.items.all()]
    ctx["all_active_notes"] = [rx.notes for rx in active_rxs if rx.notes]
    ctx["clinic_name"] = active_rxs[0].clinic.name if active_rxs else (ctx["clinics"][0].name if ctx.get("clinics") else "")
    return render(request, "doctors/ws_prescription_print_active.html", ctx)


@login_required
def ws_prescription_delete(request, patient_id, rx_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        rx = get_object_or_404(
            Prescription, pk=rx_id, patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        rx.delete()
        ctx.update(_ws_prescriptions_data(ctx["patient"], ctx["shared_clinic_ids"]))
        return render(request, "doctors/partials/ws_prescriptions.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_prescription_from_order(request, patient_id, order_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        order = get_object_or_404(
            Order, pk=order_id, patient_id=patient_id,
            order_type=Order.OrderType.DRUG,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        rx = Prescription.objects.create(
            patient=ctx["patient"],
            clinic_id=order.clinic_id,
            doctor=ctx["doctor"],
            notes="",
        )
        PrescriptionItem.objects.create(
            prescription=rx,
            medication_name=order.title,
            dosage=order.dosage or "",
            frequency=order.frequency or "",
            duration=order.duration or "",
            instructions=order.notes or "",
        )
        ctx.update(_ws_prescriptions_data(ctx["patient"], ctx["shared_clinic_ids"]))
        ctx["rx_saved"] = True
        # Return prescriptions tab so the doctor can review/print
        return render(request, "doctors/partials/ws_prescriptions.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_prescription_toggle_active(request, patient_id, rx_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        rx = get_object_or_404(
            Prescription, pk=rx_id, patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        rx.is_active = not rx.is_active
        rx.save(update_fields=["is_active"])
        from django.http import HttpResponse
        return HttpResponse(status=200)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


# ── Medical Records ───────────────────────────────────────────────────────────

@login_required
def ws_record_upload(request, patient_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        clinic_id = request.POST.get("clinic_id")
        try:
            clinic_id = int(clinic_id)
            if clinic_id not in ctx["shared_clinic_ids"]:
                clinic_id = ctx["shared_clinic_ids"][0]
        except (TypeError, ValueError):
            clinic_id = ctx["shared_clinic_ids"][0]

        uploaded_file = request.FILES.get("record_file")
        if not uploaded_file:
            ctx.update(_ws_records_data(ctx["patient"], ctx["shared_clinic_ids"], request))
            ctx["record_error"] = "Please select a file to upload."
            return render(request, "doctors/partials/ws_records.html", ctx)

        # MedicalRecord is created via objects.create(), which bypasses model-field
        # validators — enforce extension + magic-byte signature + size here so a
        # renamed/oversized/disallowed file can never be stored.
        from django.core.exceptions import ValidationError as _DjangoValidationError
        from core.validators.file_validators import (
            validate_file_extension,
            validate_file_signature,
            validate_file_size,
        )
        try:
            validate_file_extension(uploaded_file)
            validate_file_signature(uploaded_file)
            validate_file_size(uploaded_file)
        except _DjangoValidationError as exc:
            ctx.update(_ws_records_data(ctx["patient"], ctx["shared_clinic_ids"], request))
            ctx["record_error"] = "؛ ".join(exc.messages)
            return render(request, "doctors/partials/ws_records.html", ctx)

        title = request.POST.get("title", "").strip() or uploaded_file.name
        category = request.POST.get("category", MedicalRecord.Category.GENERAL)
        if category not in [c[0] for c in MedicalRecord.Category.choices]:
            category = MedicalRecord.Category.GENERAL

        MedicalRecord.objects.create(
            patient=ctx["patient"],
            clinic_id=clinic_id,
            uploaded_by=ctx["doctor"],
            title=title,
            category=category,
            file=uploaded_file,
            original_name=uploaded_file.name,
            file_size=uploaded_file.size,
            notes=request.POST.get("record_notes", "").strip(),
        )
        ctx.update(_ws_records_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["record_saved"] = True
        return render(request, "doctors/partials/ws_records.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


@login_required
def ws_record_delete(request, patient_id, record_id):
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == "POST":
        record = get_object_or_404(
            MedicalRecord, pk=record_id, patient_id=patient_id,
            clinic_id__in=ctx["shared_clinic_ids"]
        )
        record.file.delete(save=False)
        record.delete()
        ctx.update(_ws_records_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        return render(request, "doctors/partials/ws_records.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE FOLLOW-UP (doctor-side appointment creation)
# ══════════════════════════════════════════════════════════════════════════════


def _followup_default_type_id(appointment_types):
    """Return the id of the clinic's follow-up appointment type, or None.

    The Schedule Follow-up modal should default to the actual follow-up type so
    its real duration drives slot slicing (e.g. 15-min slots), instead of a
    generic no-type fallback. Matched by name since AppointmentType has no
    dedicated flag — English "follow" or Arabic "متابعة".
    """
    for t in appointment_types:
        name = (t.name or "")
        name_ar = (t.name_ar or "")
        if "follow" in name.lower() or "متابعة" in name or "متابعة" in name_ar:
            return t.id
    return None


@login_required
def ws_schedule_followup(request, patient_id):
    """
    GET  → Return the schedule follow-up modal partial (HTMX target).
    POST → Validate and create the follow-up appointment, return
           success or error fragment in-place (no page reload).
    """
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied.")

    doctor  = ctx["doctor"]
    patient = ctx["patient"]
    clinics = ctx["clinics"]

    from appointments.services.appointment_type_service import (
        get_appointment_types_for_doctor_in_clinic,
    )

    if request.method == "GET":
        default_clinic = clinics[0] if clinics else None
        appointment_types = []
        if default_clinic:
            appointment_types = get_appointment_types_for_doctor_in_clinic(
                doctor_id=doctor.id,
                clinic_id=default_clinic.id,
            )
        return render(request, "doctors/partials/schedule_followup_modal.html", {
            "patient":           patient,
            "doctor":            doctor,
            "clinics":           clinics,
            "default_clinic":    default_clinic,
            "default_clinic_id": default_clinic.id if default_clinic else None,
            "appointment_types": appointment_types,
            "default_type_id":   _followup_default_type_id(appointment_types),
            "today":             date.today().isoformat(),
            "last_visit":        _ws_last_visit(patient_id, doctor),
        })

    # ── POST ──────────────────────────────────────────────────
    from datetime import datetime as _dt
    from appointments.services.doctor_booking_service import (
        schedule_followup, DoctorSchedulingError,
    )

    clinic_id_raw   = request.POST.get("clinic_id", "")
    date_str        = request.POST.get("appointment_date", "")
    time_str        = request.POST.get("appointment_time", "")
    type_id_str     = request.POST.get("appointment_type_id", "")
    notes           = request.POST.get("notes", "").strip()
    allow_override  = request.POST.get("allow_conflict") == "1"

    errors = {}

    try:
        clinic_id = int(clinic_id_raw)
    except (ValueError, TypeError):
        clinic_id = None
        errors["clinic"] = "Please select a clinic."

    try:
        appt_date = _dt.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        appt_date = None
        errors["date"] = "Please select a valid date."

    try:
        appt_time = _dt.strptime(time_str, "%H:%M").time()
    except (ValueError, TypeError):
        appt_time = None
        errors["time"] = "Please select or enter a valid time."

    type_id = None
    if type_id_str:
        try:
            type_id = int(type_id_str)
        except (ValueError, TypeError):
            pass

    def _modal_ctx(extra=None):
        apt = []
        if clinic_id:
            apt = get_appointment_types_for_doctor_in_clinic(
                doctor_id=doctor.id, clinic_id=clinic_id
            )
        base = {
            "patient":           patient,
            "doctor":            doctor,
            "clinics":           clinics,
            "default_clinic_id": clinic_id,
            "appointment_types": apt,
            "default_type_id":   _followup_default_type_id(apt),
            "today":             date.today().isoformat(),
            "post_data":         request.POST,
        }
        if extra:
            base.update(extra)
        return base

    if errors:
        return render(
            request,
            "doctors/partials/schedule_followup_modal.html",
            _modal_ctx({"errors": errors}),
        )

    try:
        appointment = schedule_followup(
            doctor=doctor,
            patient_id=patient_id,
            clinic_id=clinic_id,
            appointment_date=appt_date,
            appointment_time=appt_time,
            appointment_type_id=type_id,
            notes=notes,
            allow_conflict=allow_override,
        )
        return render(request, "doctors/partials/schedule_followup_success.html", {
            "appointment": appointment,
            "patient":     patient,
        })
    except DoctorSchedulingError as exc:
        return render(
            request,
            "doctors/partials/schedule_followup_modal.html",
            _modal_ctx({
                "booking_error":      exc.message,
                "booking_error_code": exc.code,
            }),
        )


@login_required
def htmx_followup_slots(request, patient_id):
    """
    HTMX endpoint: returns available time-slot buttons for a given
    doctor / clinic / date / appointment-type combination.

    Used by the Schedule Follow-up modal's date/clinic selectors.

    Query params:
        clinic_id            – required
        appointment_date     – YYYY-MM-DD, required
        appointment_type_id  – optional (drives duration)
    """
    ctx = _ws_access(request, patient_id)
    if ctx is None:
        from django.http import HttpResponse as _HR
        return _HR("", status=403)

    doctor        = ctx["doctor"]
    clinic_id_str = request.GET.get("clinic_id", "")
    date_str      = request.GET.get("appointment_date", "")
    type_id_str   = request.GET.get("appointment_type_id", "")

    try:
        clinic_id = int(clinic_id_str)
    except (ValueError, TypeError):
        return render(request, "doctors/partials/schedule_followup_slots.html", {
            "slots": [], "error": "Please select a clinic first.",
        })

    try:
        from datetime import datetime as _dt2
        target_date = _dt2.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return render(request, "doctors/partials/schedule_followup_slots.html", {
            "slots": [], "error": "Please select a date first.",
        })

    duration_minutes = 30  # sensible default when no type chosen
    if type_id_str:
        try:
            apt_type = AppointmentType.objects.get(
                id=int(type_id_str), clinic_id=clinic_id, is_active=True,
            )
            duration_minutes = apt_type.duration_minutes
        except (AppointmentType.DoesNotExist, ValueError, TypeError):
            pass

    slots = generate_slots_for_date(
        doctor_id=doctor.id,
        clinic_id=clinic_id,
        target_date=target_date,
        duration_minutes=duration_minutes,
    )

    return render(request, "doctors/partials/schedule_followup_slots.html", {
        "slots":       slots,
        "target_date": target_date,
    })


# ============================================
# CLINICAL NOTE TEMPLATES
# ============================================

# All standard element type codes in their canonical display order.
# NOTE: "CUSTOM" is intentionally absent — custom sections are handled separately.
_ALL_ELEMENT_TYPES = [
    "SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN",
    "FREE_TEXT", "VITALS", "BODY_DIAGRAM", "DENTAL",
]

# Default display labels for standard element types.
_ELEMENT_LABELS = {
    "SUBJECTIVE":   "S — Subjective",
    "OBJECTIVE":    "O — Objective",
    "ASSESSMENT":   "A — Assessment",
    "PLAN":         "P — Plan",
    "FREE_TEXT":    "Free Text",
    "VITALS":       "Vitals",
    "BODY_DIAGRAM": "Body Diagram",
    "DENTAL":       "Dental Chart",
}

# Maps standard element types to their HTML form field names.
# CUSTOM sections use a dynamic name: custom_section_<elem_id>.
# VITALS, BODY_DIAGRAM, DENTAL have no dedicated ClinicalNote model field;
# their content is persisted in ClinicalNote.extra_sections under these keys.
_SECTION_FIELD_MAP = {
    "SUBJECTIVE":   "subjective",
    "OBJECTIVE":    "objective",
    "ASSESSMENT":   "assessment",
    "PLAN":         "plan",
    "FREE_TEXT":    "free_text",
    "VITALS":       "vitals",
    "BODY_DIAGRAM": "body_diagram_notes",
    "DENTAL":       "dental_notes",
}

# Human-readable labels for the non-model standard section keys stored in extra_sections.
_EXTRA_SECTION_DISPLAY_LABELS = {
    "vitals":             "Vitals",
    "body_diagram_notes": "Body Diagram",
    "dental_notes":       "Dental Chart",
}


def _resolve_active_template(doctor):
    """Return the active ClinicalNoteTemplate for a doctor, or None."""
    try:
        settings_obj = DoctorClinicalNoteSettings.objects.select_related(
            "active_template"
        ).get(doctor=doctor)
        tpl = settings_obj.active_template
    except DoctorClinicalNoteSettings.DoesNotExist:
        tpl = None

    if tpl is None:
        tpl = ClinicalNoteTemplate.objects.filter(
            template_type=ClinicalNoteTemplate.TemplateType.SYSTEM,
            is_system_default=True,
        ).first()

    return tpl


def _extract_note_field(note, element_type):
    """
    Return the saved text for a standard element type from a ClinicalNote.
    VITALS, BODY_DIAGRAM, and DENTAL are stored in extra_sections (no DB column).
    Returns empty string when note is None or the field is empty.
    """
    if note is None:
        return ""
    mapping = {
        "SUBJECTIVE":   note.subjective,
        "OBJECTIVE":    note.objective,
        "ASSESSMENT":   note.assessment,
        "PLAN":         note.plan,
        "FREE_TEXT":    note.free_text,
        "VITALS":       note.extra_sections.get("vitals", ""),
        "BODY_DIAGRAM": note.extra_sections.get("body_diagram_notes", ""),
        "DENTAL":       note.extra_sections.get("dental_notes", ""),
    }
    return mapping.get(element_type, "")


def _collect_extra_sections(post_data):
    """
    Build the extra_sections dict from a POST payload.

    Handles three categories:
      1. Custom template sections:  custom_section_<elem_id>  → key = str(elem_id)
      2. VITALS form field:         vitals                    → key = "vitals"
      3. BODY_DIAGRAM form field:   body_diagram_notes        → key = "body_diagram_notes"
      4. DENTAL form field:         dental_notes              → key = "dental_notes"

    Only non-empty values are stored.  The keys must match _extract_note_field()
    so that edit-form pre-filling works correctly.
    """
    extra = {}
    # Standard sections without dedicated ClinicalNote model columns
    for field_name in ("vitals", "body_diagram_notes", "dental_notes"):
        val = post_data.get(field_name, "").strip()
        if val:
            extra[field_name] = val
    # CUSTOM template sections keyed by their ClinicalNoteTemplateElement PK
    for k, v in post_data.items():
        if k.startswith("custom_section_"):
            val = v.strip()
            if val:
                extra[k.removeprefix("custom_section_")] = val
    # Orthopedic structured findings (JSON array of region finding objects)
    ortho_raw = post_data.get("ortho_findings", "").strip()
    if ortho_raw:
        try:
            parsed = json.loads(ortho_raw)
            if isinstance(parsed, list) and parsed:
                extra["ortho_findings"] = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return extra


def _collect_extra_sections_labels(active_sections):
    """
    Build a {key: label} snapshot from the active template sections list.

    Only covers sections whose content lands in extra_sections (CUSTOM, VITALS,
    BODY_DIAGRAM, DENTAL). SOAP and FREE_TEXT use dedicated model columns whose
    semantics are stable and do not require snapshotting.

    This snapshot is written to ClinicalNote.extra_sections_labels at save time
    so that future template edits — including element deletion — cannot
    retroactively rename or erase section titles on already-saved notes.
    """
    labels = {}
    for section in active_sections:
        stype = section["type"]
        if stype == "CUSTOM":
            # key = str(elem_id), matching _collect_extra_sections
            key = str(section["elem_id"])
            labels[key] = section["label"]
        elif stype in ("VITALS", "BODY_DIAGRAM", "DENTAL"):
            # key = section["name"] e.g. "vitals", "body_diagram_notes"
            key = section["name"]
            labels[key] = section["label"]
    return labels


def _annotate_notes_with_labeled_extras(notes_list):
    """
    Attach .labeled_extras to every note in notes_list.

    Label resolution order (highest → lowest priority):
    1. note.extra_sections_labels  — snapshot saved at note-write time.
       Immune to any future template change or element deletion.
    2. _EXTRA_SECTION_DISPLAY_LABELS — code constants for the three built-in
       extra keys (vitals / body_diagram_notes / dental_notes).  These never
       change, so the snapshot and the constant agree; listed here as a safe
       explicit fallback for old notes that predate the snapshot field.
    3. Live ClinicalNoteTemplateElement lookup — backward-compat for notes
       created before the extra_sections_labels field existed.  Still correct
       as long as the element has not been deleted.
    4. "Custom Section" — last resort when the element has already been deleted
       and no snapshot was recorded (pre-migration notes only).
    """
    # Only fetch live element labels for keys not covered by a snapshot,
    # to avoid unnecessary DB queries.
    custom_elem_ids_needed = set()
    for note in notes_list:
        snapshot = note.extra_sections_labels or {}
        for key in note.extra_sections:
            if key in snapshot:
                continue
            if key in _EXTRA_SECTION_DISPLAY_LABELS:
                continue
            try:
                custom_elem_ids_needed.add(int(key))
            except (ValueError, TypeError):
                pass

    live_label_map = {}
    if custom_elem_ids_needed:
        live_label_map = dict(
            ClinicalNoteTemplateElement.objects.filter(
                id__in=custom_elem_ids_needed
            ).values_list("id", "custom_label")
        )

    for note in notes_list:
        snapshot = note.extra_sections_labels or {}
        labeled = []
        ortho_data = None
        for key, val in note.extra_sections.items():
            if not val:
                continue
            # Orthopedic structured findings — rendered separately, not as a text blob
            if key == "ortho_findings":
                if isinstance(val, list):
                    ortho_data = val
                continue
            # 1. Snapshot (historically persisted — survives element deletion)
            if key in snapshot and snapshot[key]:
                label = snapshot[key]
            # 2. Built-in constant labels
            elif key in _EXTRA_SECTION_DISPLAY_LABELS:
                label = _EXTRA_SECTION_DISPLAY_LABELS[key]
            # 3. Live element lookup (backward compat for pre-snapshot notes)
            else:
                try:
                    eid = int(key)
                    label = live_label_map.get(eid) or "Custom Section"
                except (ValueError, TypeError):
                    label = key
            labeled.append({"label": label, "value": val})
        note.labeled_extras = labeled
        note.ortho_findings_data = ortho_data
        note.ortho_findings_json = json.dumps(ortho_data) if ortho_data else '[]'


def _get_active_note_sections(doctor, note=None):
    """
    Return an ordered list of section descriptors for the clinical note editor,
    in exactly the order defined by the doctor's active template.

    Each descriptor is a dict:
      type     – element type code ("SUBJECTIVE", "CUSTOM", "VITALS", …)
      label    – display label (from template custom_label or platform default)
      name     – HTML <textarea> name attribute
      elem_id  – ClinicalNoteTemplateElement PK for CUSTOM sections; None otherwise
      value    – pre-filled value (empty string for new notes; populated for edits)

    ROOT CAUSE FIX: this replaces the old trio of helpers
    (_get_active_template_elements, _get_active_template_element_labels,
    _get_active_custom_sections) that scattered section data across three
    separate context variables and rendered custom sections AFTER all standard
    sections regardless of their defined position in the template.
    """
    tpl = _resolve_active_template(doctor)

    if tpl is None:
        # Ultimate fallback: no template configured at all — show every standard type
        return [
            {
                "type":    et,
                "label":   _ELEMENT_LABELS[et],
                "name":    _SECTION_FIELD_MAP[et],
                "elem_id": None,
                "value":   _extract_note_field(note, et),
            }
            for et in _ALL_ELEMENT_TYPES
        ]

    sections = []
    for elem in tpl.elements.order_by("order"):
        et = elem.element_type
        if et == ClinicalNoteTemplateElement.ElementType.CUSTOM:
            sections.append({
                "type":    "CUSTOM",
                "label":   elem.custom_label or "Custom Section",
                "name":    f"custom_section_{elem.id}",
                "elem_id": elem.id,
                "value":   note.extra_sections.get(str(elem.id), "") if note else "",
            })
        else:
            sections.append({
                "type":    et,
                "label":   elem.custom_label or _ELEMENT_LABELS.get(et, et),
                "name":    _SECTION_FIELD_MAP.get(et, et.lower()),
                "elem_id": None,
                "value":   _extract_note_field(note, et),
            })
    return sections


def _require_doctor(request):
    """Return the user if they are a DOCTOR/MAIN_DOCTOR, else None.

    Thin shim around ``_is_doctor`` so the role rule lives in exactly one place.
    """
    return request.user if _is_doctor(request.user) else None


@login_required
def clinical_note_templates(request):
    """List page: active template, system templates, and doctor's custom templates."""
    user = _require_doctor(request)
    if user is None:
        return redirect("accounts:home")

    # Determine active template
    try:
        settings_obj = DoctorClinicalNoteSettings.objects.select_related(
            "active_template"
        ).get(doctor=user)
        active_template = settings_obj.active_template
    except DoctorClinicalNoteSettings.DoesNotExist:
        active_template = None

    system_default = ClinicalNoteTemplate.objects.filter(
        template_type=ClinicalNoteTemplate.TemplateType.SYSTEM,
        is_system_default=True,
    ).prefetch_related("elements").first()

    system_templates = ClinicalNoteTemplate.objects.filter(
        template_type=ClinicalNoteTemplate.TemplateType.SYSTEM,
        is_system_default=False,
    ).prefetch_related("elements").order_by("name")

    my_templates = ClinicalNoteTemplate.objects.filter(
        template_type=ClinicalNoteTemplate.TemplateType.CUSTOM,
        doctor=user,
    ).prefetch_related("elements").order_by("name")

    # Effective active: explicit or system default
    effective_active = active_template or system_default

    return render(request, "doctors/clinical_note_templates.html", {
        "active_template":  active_template,
        "effective_active": effective_active,
        "system_default":   system_default,
        "system_templates": system_templates,
        "my_templates":     my_templates,
        "element_labels":   _ELEMENT_LABELS,
    })


@login_required
def clinical_note_template_create(request):
    """Create a new custom template."""
    user = _require_doctor(request)
    if user is None:
        return redirect("accounts:home")

    if request.method == "POST":
        name = request.POST.get("name", "")
        description = request.POST.get("description", "")
        section_types = request.POST.getlist("section_type")
        section_labels = request.POST.getlist("section_label")

        try:
            create_clinical_note_template(
                doctor=user,
                name=name,
                description=description,
                section_types=section_types,
                section_labels=section_labels,
            )
            messages.success(request, f'Template "{name}" created.')
            return redirect("doctors:clinical_note_templates")
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, 'message') else str(e.args[0]))
            return redirect("doctors:clinical_note_template_create")

    # Pass the available valid element choices dynamically, preserving the standard ordering.
    element_choices = [
        (et, _ELEMENT_LABELS[et], "")
        for et in _ALL_ELEMENT_TYPES
    ]
    # Explicitly append CUSTOM as the base generic type
    element_choices.append(("CUSTOM", "Generic Text Section", ""))
    
    return render(request, "doctors/clinical_note_template_form.html", {
        "mode": "create",
        "element_choices": element_choices,
        "tpl": None,
    })


@login_required
def clinical_note_template_edit(request, template_id):
    """Edit a doctor-owned custom template."""
    user = _require_doctor(request)
    if user is None:
        return redirect("accounts:home")

    tpl = get_object_or_404(
        ClinicalNoteTemplate,
        id=template_id,
        template_type=ClinicalNoteTemplate.TemplateType.CUSTOM,
        doctor=user,
    )

    if request.method == "POST":
        name = request.POST.get("name", "")
        description = request.POST.get("description", "")
        section_types = request.POST.getlist("section_type")
        section_labels = request.POST.getlist("section_label")

        try:
            update_clinical_note_template(
                template_id=tpl.id,
                doctor=user,
                name=name,
                description=description,
                section_types=section_types,
                section_labels=section_labels,
            )
            messages.success(request, f'Template "{name}" updated.')
            return redirect("doctors:clinical_note_templates")
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, 'message') else str(e.args[0]))
            return redirect("doctors:clinical_note_template_edit", template_id=tpl.id)

    element_choices = [
        (et, _ELEMENT_LABELS[et], "")
        for et in _ALL_ELEMENT_TYPES
    ]
    element_choices.append(("CUSTOM", "Generic Text Section", ""))
    
    return render(request, "doctors/clinical_note_template_form.html", {
        "mode": "edit",
        "tpl": tpl,
        "element_choices": element_choices,
    })


@login_required
def clinical_note_template_activate(request, template_id):
    """Activate a template (system or doctor-owned custom) for the current doctor."""
    if request.method != "POST":
        return redirect("doctors:clinical_note_templates")

    user = _require_doctor(request)
    if user is None:
        return redirect("accounts:home")

    tpl = get_object_or_404(ClinicalNoteTemplate, id=template_id)

    # Doctors may only activate system templates or their own custom templates
    if tpl.template_type == ClinicalNoteTemplate.TemplateType.CUSTOM and tpl.doctor != user:
        messages.error(request, "You cannot activate another doctor's template.")
        return redirect("doctors:clinical_note_templates")

    settings_obj, _ = DoctorClinicalNoteSettings.objects.get_or_create(doctor=user)

    if tpl.is_system_default:
        # Activating the system default = clear the override (use None)
        settings_obj.active_template = None
    else:
        settings_obj.active_template = tpl

    settings_obj.save()
    messages.success(request, f'"{tpl.name}" is now your active template.')
    return redirect("doctors:clinical_note_templates")


@login_required
def clinical_note_template_delete(request, template_id):
    """Delete a doctor-owned custom template."""
    if request.method != "POST":
        return redirect("doctors:clinical_note_templates")

    user = _require_doctor(request)
    if user is None:
        return redirect("accounts:home")

    tpl = get_object_or_404(
        ClinicalNoteTemplate,
        id=template_id,
        template_type=ClinicalNoteTemplate.TemplateType.CUSTOM,
        doctor=user,
    )

    # If this was the active template, clear the setting
    DoctorClinicalNoteSettings.objects.filter(
        doctor=user, active_template=tpl
    ).update(active_template=None)

    name = tpl.name
    tpl.delete()
    messages.success(request, f'Template "{name}" deleted.')
    return redirect("doctors:clinical_note_templates")
