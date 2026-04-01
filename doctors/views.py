from datetime import datetime, date

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.http import HttpResponse

from appointments.models import Appointment, AppointmentType
from clinics.models import ClinicStaff
from .models import DoctorAvailability, DoctorProfile, DoctorVerification, ClinicDoctorCredential, DoctorIntakeFormTemplate, DoctorIntakeQuestion, DoctorIntakeRule
from .services import generate_slots_for_date

User = get_user_model()


# ============================================
# DOCTOR DASHBOARD
# ============================================


@login_required
def dashboard(request):
    """Full doctor dashboard with verification status, clinic memberships, and today's appointments."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

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

    clinic_cards = []
    for m in _best.values():
        credentials = ClinicDoctorCredential.objects.filter(
            doctor=user, clinic=m.clinic
        ).select_related("specialty")
        all_verified = credentials.exists() and all(
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
def appointments_list(request):
    """Doctor's full appointment list — filterable by date and status."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

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


@login_required
def appointment_detail(request, appointment_id):
    """Single appointment view with patient info, intake answers, and status controls."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    appointment = get_object_or_404(
        Appointment,
        id=appointment_id,
        doctor=user,
    )

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

    # Merge text answers and file attachments into one ordered list per question
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

    intake_data = sorted(
        _combined.values(),
        key=lambda x: x["question"].order if x["question"] else 0,
    )

    # Status transitions the doctor can trigger — as (value, label) tuples for template use
    _TRANSITION_MAP = {
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
    raw_transitions = _TRANSITION_MAP.get(appointment.status, [])
    # Build (value, human_label) tuples for the template
    allowed_transitions = [(s.value, s.label) for s in raw_transitions]
    # Keep a set of valid values for POST validation
    valid_transition_values = {s.value for s in raw_transitions}

    if request.method == "POST":
        new_status = request.POST.get("status", "").strip()
        notes = request.POST.get("notes", "").strip()
        # Backend enforcement: only allow whitelisted transitions (ignore stale/tampered POSTs)
        if new_status in valid_transition_values:
            appointment.status = new_status
            if notes:
                appointment.notes = notes
            appointment.save(update_fields=["status", "notes", "updated_at"])

            # Notify patient when doctor cancels
            if new_status == Appointment.Status.CANCELLED:
                from django.db import transaction as _txn
                from clinics.models import ClinicStaff as _CS
                from appointments.services.appointment_notification_service import (
                    notify_appointment_cancelled_by_staff,
                )
                doctor_staff = _CS.objects.filter(
                    clinic=appointment.clinic, user=user, revoked_at__isnull=True
                ).first()
                _txn.on_commit(
                    lambda: notify_appointment_cancelled_by_staff(appointment, doctor_staff)
                )

            from django.contrib import messages as _msg
            _msg.success(request, "تم تحديث حالة الموعد.")
            return redirect("doctors:appointment_detail", appointment_id=appointment_id)
        elif new_status:
            from django.contrib import messages as _msg
            _msg.error(request, "هذا التحديث غير مسموح به.")

    return render(request, "doctors/appointment_detail.html", {
        "appointment": appointment,
        "intake_data": intake_data,
        "allowed_transitions": allowed_transitions,
    })


@login_required
def patients_list(request):
    """Doctor's patient management page — full clinical tool with search, filter, sort, pagination."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))

    from django.db.models import Max, Count, Q
    from django.core.paginator import Paginator
    from datetime import date, datetime
    from patients.models import PatientProfile, ClinicPatient

    # ── Query params ─────────────────────────────────────────
    q             = request.GET.get("q", "").strip()
    clinic_filter = request.GET.get("clinic_id", "")
    status_filter = request.GET.get("status", "")
    date_from     = request.GET.get("date_from", "")
    date_to       = request.GET.get("date_to", "")
    sort          = request.GET.get("sort", "-last_visit")
    page_num      = request.GET.get("page", "1")

    # ── Doctor's active clinics (deduplicated by clinic) ─────
    # A multi-role user (MAIN_DOCTOR + DOCTOR) may have several
    # ClinicStaff rows for the same clinic — deduplicate here.
    my_clinics_qs = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
        .order_by("clinic__name")
    )
    seen_clinic_ids: set = set()
    my_clinics = []
    for staff in my_clinics_qs:
        if staff.clinic_id not in seen_clinic_ids:
            seen_clinic_ids.add(staff.clinic_id)
            my_clinics.append(staff)
    clinic_ids = list(seen_clinic_ids)

    # ── Determine effective clinic IDs for this request ──────
    if clinic_filter:
        try:
            _fid = int(clinic_filter)
            effective_clinic_ids = [_fid] if _fid in clinic_ids else clinic_ids
            if _fid not in clinic_ids:
                clinic_filter = ""
        except (ValueError, TypeError):
            clinic_filter = ""
            effective_clinic_ids = clinic_ids
    else:
        effective_clinic_ids = clinic_ids

    # ── Base: all patients registered in the doctor's clinics ─
    # Use ClinicPatient as the source so secretary-registered
    # patients appear even before their first appointment.
    cp_qs = (
        ClinicPatient.objects.filter(clinic_id__in=effective_clinic_ids)
        .select_related("patient")
    )

    if q:
        phone_q = q.replace(" ", "").replace("-", "")
        cp_qs = cp_qs.filter(
            Q(patient__name__icontains=q)
            | Q(patient__phone__icontains=phone_q)
            | Q(patient__national_id__icontains=q)
        )

    # Deduplicate patients (same patient may be in multiple clinics)
    seen_pids: set = set()
    cp_list = []
    for cp in cp_qs:
        if cp.patient_id not in seen_pids:
            seen_pids.add(cp.patient_id)
            cp_list.append(cp)

    patient_ids = list(seen_pids)

    # ── Appointment stats for those patients (with this doctor) ─
    appt_qs = (
        Appointment.objects.filter(
            doctor=user,
            patient_id__in=patient_ids,
            clinic_id__in=effective_clinic_ids,
        ).exclude(status=Appointment.Status.CANCELLED)
    )

    if date_from:
        try:
            appt_qs = appt_qs.filter(
                appointment_date__gte=datetime.strptime(date_from, "%Y-%m-%d").date()
            )
        except ValueError:
            date_from = ""

    if date_to:
        try:
            appt_qs = appt_qs.filter(
                appointment_date__lte=datetime.strptime(date_to, "%Y-%m-%d").date()
            )
        except ValueError:
            date_to = ""

    appt_stats = {
        s["patient_id"]: s
        for s in appt_qs.values("patient_id").annotate(
            last_visit=Max("appointment_date"), total_visits=Count("id")
        )
    }

    # ── Patient → clinic tags (from ClinicPatient) ────────────
    patient_clinic_map: dict = {}
    for cp in ClinicPatient.objects.filter(
        patient_id__in=patient_ids,
        clinic_id__in=clinic_ids,
    ).select_related("clinic"):
        pid = cp.patient_id
        entry = {"id": cp.clinic_id, "name": cp.clinic.name}
        if pid not in patient_clinic_map:
            patient_clinic_map[pid] = []
        if entry not in patient_clinic_map[pid]:
            patient_clinic_map[pid].append(entry)

    # ── Profiles ──────────────────────────────────────────────
    profiles = {
        pp.user_id: pp
        for pp in PatientProfile.objects.filter(user_id__in=patient_ids)
    }

    GENDER_MAP = {"M": "Male", "F": "Female", "O": "Other"}
    today = date.today()

    # ── Build enriched list ───────────────────────────────────
    enriched = []
    for cp in cp_list:
        pid   = cp.patient_id
        prof  = profiles.get(pid)
        stats = appt_stats.get(pid, {})

        age = None
        if prof and prof.date_of_birth:
            dob = prof.date_of_birth
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        lv           = stats.get("last_visit")
        total_visits = stats.get("total_visits", 0)

        if lv:
            days = (today - lv).days
            if days <= 30:
                p_status = "active"
            elif days <= 90:
                p_status = "follow_up"
            else:
                p_status = "inactive"
        else:
            p_status = "inactive"   # registered but no appointments yet

        enriched.append({
            "patient_id":         pid,
            "patient__name":      cp.patient.name,
            "patient__phone":     cp.patient.phone,
            "patient__national_id": cp.patient.national_id,
            "last_visit":         lv,
            "total_visits":       total_visits,
            "age":                age,
            "gender":             prof.gender if prof else "",
            "gender_display":     GENDER_MAP.get(prof.gender if prof else "", "—"),
            "clinics":            patient_clinic_map.get(pid, []),
            "patient_status":     p_status,
        })

    # ── Sort (Python-level, nullable last_visit safe) ─────────
    _min_date = date.min
    SORT_CONFIGS = {
        "-last_visit": (lambda p: p["last_visit"] or _min_date, True),
        "last_visit":  (lambda p: p["last_visit"] or _min_date, False),
        "name":        (lambda p: p["patient__name"].lower(), False),
        "-name":       (lambda p: p["patient__name"].lower(), True),
        "-visits":     (lambda p: p["total_visits"], True),
        "visits":      (lambda p: p["total_visits"], False),
    }
    sort_fn, reverse = SORT_CONFIGS.get(sort, SORT_CONFIGS["-last_visit"])
    enriched.sort(key=sort_fn, reverse=reverse)

    # ── Status filter (post-enrichment) ──────────────────────
    if status_filter:
        enriched = [p for p in enriched if p["patient_status"] == status_filter]

    # ── Summary counts ────────────────────────────────────────
    total_count    = len(enriched)
    active_count   = sum(1 for p in enriched if p["patient_status"] == "active")
    followup_count = sum(1 for p in enriched if p["patient_status"] == "follow_up")
    inactive_count = sum(1 for p in enriched if p["patient_status"] == "inactive")

    # ── Pagination ────────────────────────────────────────────
    paginator = Paginator(enriched, 25)
    try:
        page_obj = paginator.page(int(page_num))
    except Exception:
        page_obj = paginator.page(1)

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
from django.utils import timezone
from django.core.exceptions import ValidationError
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
    """Action to accept an invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id)
    
    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, f"You have successfully joined {staff.clinic.name}.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"Error: {err_msg}")
            
    return redirect(reverse("doctors:doctor_invitations_inbox"))

@login_required
def reject_invitation_view(request, invitation_id):
    """Action to reject an invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id)
    
    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, "Invitation rejected.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"Error: {err_msg}")
            
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
def doctor_verification_status(request):
    """Show the doctor's dual-layer verification status."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "This page is for doctors only.")
        return redirect(reverse("accounts:home"))

    verification = DoctorVerification.objects.filter(user=user).first()
    credentials = ClinicDoctorCredential.objects.filter(
        doctor=user
    ).select_related("clinic", "specialty").order_by("clinic__name")

    return render(request, "doctors/verification_status.html", {
        "verification": verification,
        "credentials": credentials,
    })


@login_required
def doctor_upload_credentials(request):
    """Upload identity documents for platform verification."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "This page is for doctors only.")
        return redirect(reverse("accounts:home"))

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
def doctor_profile_view(request):
    """Read-only view of the doctor's profile."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "This page is for doctors only.")
        return redirect(reverse("accounts:home"))

    profile, _ = DoctorProfile.objects.get_or_create(user=user)
    specialties = profile.specialties.all()
    memberships = (
        ClinicStaff.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("clinic")
    )

    return render(request, "doctors/doctor_profile.html", {
        "profile": profile,
        "specialties": specialties,
        "memberships": memberships,
    })


@login_required
def doctor_edit_profile_view(request):
    """Edit doctor's bio, experience, and email (email change via OTP)."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "This page is for doctors only.")
        return redirect(reverse("accounts:home"))

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
def my_schedule(request):
    """Doctor manages their weekly availability schedule per clinic."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        messages.error(request, "This page is for doctors only.")
        return redirect(reverse("accounts:home"))

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
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        _messages.error(request, "This page is for doctors only.")
        return redirect(_reverse("accounts:home"))

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
    """Returns None if user is a doctor, otherwise returns a redirect response."""
    user = request.user
    if "DOCTOR" not in (user.roles or []) and "MAIN_DOCTOR" not in (user.roles or []):
        from django.contrib import messages as _msg
        from django.urls import reverse as _rev
        _msg.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(_rev("accounts:home"))
    return None


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
    ClinicalNote, Order, Prescription, PrescriptionItem, MedicalRecord,
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
    ctx["tabs"] = [
        ("overview",      "Overview",       "fa-solid fa-chart-pie"),
        ("notes",         "Clinical Notes", "fa-solid fa-file-medical"),
        ("orders",        "Orders",         "fa-solid fa-flask"),
        ("prescriptions", "Prescriptions",  "fa-solid fa-prescription"),
        ("records",       "Records",        "fa-solid fa-folder-open"),
    ]
    patient = ctx["patient"]
    cids = ctx["shared_clinic_ids"]

    if tab == "overview":
        ctx.update(_ws_overview_data(patient, cids))
    elif tab == "notes":
        ctx.update(_ws_notes_data(patient, cids, request))
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

def _ws_overview_data(patient, cids):
    all_notes      = list(ClinicalNote.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic").order_by("-created_at"))
    active_orders  = list(Order.objects.filter(patient=patient, clinic_id__in=cids, status=Order.Status.PENDING).select_related("doctor")[:8])
    latest_rx      = Prescription.objects.filter(patient=patient, clinic_id__in=cids).prefetch_related("items").first()
    recent_records = list(MedicalRecord.objects.filter(patient=patient, clinic_id__in=cids)[:5])

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
        "active_orders":  active_orders,
        "latest_rx":      latest_rx,
        "recent_records": recent_records,
        "timeline_events": events[:15],
    }


def _ws_notes_data(patient, cids, request):
    from django.core.paginator import Paginator
    qs = ClinicalNote.objects.filter(patient=patient, clinic_id__in=cids).select_related("doctor", "clinic")
    paginator = Paginator(qs, 10)
    notes_page = paginator.get_page(request.GET.get("notes_page", 1))
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
    return {
        "prescriptions": list(
            Prescription.objects.filter(patient=patient, clinic_id__in=cids)
            .select_related("doctor", "clinic")
            .prefetch_related("items")
        )
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

        ClinicalNote.objects.create(
            patient=ctx["patient"],
            clinic_id=clinic_id,
            doctor=ctx["doctor"],
            subjective=request.POST.get("subjective", "").strip(),
            objective=request.POST.get("objective", "").strip(),
            assessment=request.POST.get("assessment", "").strip(),
            plan=request.POST.get("plan", "").strip(),
            free_text=request.POST.get("free_text", "").strip(),
        )
        ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["note_saved"] = True
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
        note.subjective = request.POST.get("subjective", "").strip()
        note.objective  = request.POST.get("objective", "").strip()
        note.assessment = request.POST.get("assessment", "").strip()
        note.plan       = request.POST.get("plan", "").strip()
        note.free_text  = request.POST.get("free_text", "").strip()
        note.save()
        ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
        ctx["note_saved"] = True
        return render(request, "doctors/partials/ws_notes.html", ctx)

    ctx["edit_note"] = note
    ctx.update(_ws_notes_data(ctx["patient"], ctx["shared_clinic_ids"], request))
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
        return render(request, "doctors/partials/ws_notes.html", ctx)

    return redirect("doctors:patient_workspace", patient_id=patient_id)


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

    from django.db.models import Q
    mode = request.GET.get("mode", "generic")
    qs = DrugProduct.objects.filter(clinic_id=clinic_id, is_active=True).select_related("family")

    family_id = request.GET.get("family_id", "").strip()
    if family_id:
        qs = qs.filter(family_id=family_id)

    q = request.GET.get("drug_q", "").strip()
    if q:
        qs = qs.filter(Q(generic_name__icontains=q) | Q(commercial_name__icontains=q))

    if mode == "commercial":
        drugs = list(qs.order_by("commercial_name", "generic_name")[:60])
    else:
        drugs = list(qs.order_by("generic_name")[:60])

    return render(request, "doctors/partials/catalog_drug_results.html", {
        "drugs": drugs,
        "mode": mode,
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
