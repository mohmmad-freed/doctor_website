"""Doctor-review actions (Phase 3): patient submit, anyone report, staff hide.

Mounted at /reviews/ (NOT /doctors/) so patients aren't blocked by
ClinicIsolationMiddleware. Each view enforces its own auth/eligibility; the
public *display* of reviews lives in the browse app.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts import ratelimit
from .models import DoctorReview
from .services import (
    REVIEW_AUTOHIDE_REPORTS,
    patient_can_review_doctor,
    user_can_moderate_doctor_reviews,
)

User = get_user_model()


def _safe_back(request, fallback="patients:my_appointments"):
    """Validated return target (POST ``next`` or Referer), else a safe default."""
    nxt = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return reverse(fallback)


@login_required
@require_POST
def submit_review(request, doctor_id):
    """Create or update the current patient's review of a doctor (auto-published).

    Eligibility: a COMPLETED appointment with that doctor. One review per patient
    per doctor; resubmitting edits the existing one (without un-hiding a moderated
    review)."""
    doctor = get_object_or_404(User, pk=doctor_id, role__in=["DOCTOR", "MAIN_DOCTOR"])
    if not request.user.has_role("PATIENT"):
        return HttpResponseForbidden("Only patients can review.")
    if not patient_can_review_doctor(request.user, doctor.id):
        messages.error(request, _t(
            request, "You can review a doctor only after a completed appointment.",
            "يمكنك تقييم الطبيب فقط بعد موعد مكتمل."))
        return redirect(_safe_back(request))

    try:
        rating = int(request.POST.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if not (1 <= rating <= 5):
        messages.error(request, _t(
            request, "Please choose a rating from 1 to 5.",
            "يرجى اختيار تقييم من 1 إلى 5."))
        return redirect(_safe_back(request))

    comment = (request.POST.get("comment") or "").strip()[:2000]
    # defaults deliberately omit is_hidden so editing can't un-hide a moderated review.
    DoctorReview.objects.update_or_create(
        doctor=doctor, patient=request.user,
        defaults={"rating": rating, "comment": comment},
    )
    messages.success(request, _t(
        request, "Thanks! Your review was published.", "شكراً! تم نشر تقييمك."))
    return redirect(_safe_back(request))


@login_required
@require_POST
def report_review(request, review_id):
    """Any signed-in user may flag a review once per day; auto-hides past a threshold."""
    review = get_object_or_404(DoctorReview, pk=review_id)
    # One effective report per user per review per day (prevents brigading).
    if not ratelimit.hit_rate_limit("review_report", f"{request.user.id}:{review_id}", 1, 86400):
        DoctorReview.objects.filter(pk=review_id).update(report_count=F("report_count") + 1)
        review.refresh_from_db()
        if review.report_count >= REVIEW_AUTOHIDE_REPORTS and not review.is_hidden:
            review.is_hidden = True
            review.hidden_at = timezone.now()
            review.save(update_fields=["is_hidden", "hidden_at"])
    messages.info(request, _t(
        request, "Thanks — this review has been reported for moderation.",
        "شكراً — تم الإبلاغ عن هذا التقييم للمراجعة."))
    return redirect(_safe_back(request))


@login_required
@require_POST
def hide_review(request, review_id):
    """Clinic staff (owner/secretary employing the doctor) or admin hides a review."""
    review = get_object_or_404(DoctorReview, pk=review_id)
    if not user_can_moderate_doctor_reviews(request.user, review.doctor_id):
        return HttpResponseForbidden("Not allowed.")
    review.is_hidden = True
    review.hidden_at = timezone.now()
    review.hidden_by = request.user
    review.save(update_fields=["is_hidden", "hidden_at", "hidden_by"])
    messages.success(request, _t(request, "Review hidden.", "تم إخفاء التقييم."))
    return redirect(_safe_back(request))


@login_required
@require_POST
def unhide_review(request, review_id):
    """Reverse a hide (same moderators)."""
    review = get_object_or_404(DoctorReview, pk=review_id)
    if not user_can_moderate_doctor_reviews(request.user, review.doctor_id):
        return HttpResponseForbidden("Not allowed.")
    review.is_hidden = False
    review.hidden_at = None
    review.hidden_by = None
    review.save(update_fields=["is_hidden", "hidden_at", "hidden_by"])
    messages.success(request, _t(request, "Review restored.", "تمت استعادة التقييم."))
    return redirect(_safe_back(request))


def _t(request, en, ar):
    return en if getattr(request, "LANGUAGE_CODE", "ar") == "en" else ar
