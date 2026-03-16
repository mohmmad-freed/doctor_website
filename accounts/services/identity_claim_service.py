from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.constants import (
    IDENTITY_CLAIM_ACTIVE_STATUSES,
    INVALID_NATIONAL_ID_VALUES,
    NATIONAL_ID_LENGTH,
    NATIONAL_ID_REPEATED_DIGITS_RE,
    NATIONAL_ID_STRIP_RE,
    IdentityClaimStatus,
)
from accounts.models import IdentityClaim

User = get_user_model()


def normalize_national_id(raw_id):
    value = str(raw_id or "").strip()
    return NATIONAL_ID_STRIP_RE.sub("", value)


def validate_national_id(cleaned_id):
    if not cleaned_id:
        raise ValidationError("يرجى إدخال رقم الهوية الوطنية.")

    if not cleaned_id.isdigit():
        raise ValidationError("رقم الهوية الوطنية يجب أن يحتوي على أرقام فقط.")

    if len(cleaned_id) != NATIONAL_ID_LENGTH:
        raise ValidationError(f"رقم الهوية الوطنية يجب أن يتكون من {NATIONAL_ID_LENGTH} أرقام.")

    if cleaned_id in INVALID_NATIONAL_ID_VALUES:
        raise ValidationError("رقم الهوية الوطنية غير صالح.")

    if NATIONAL_ID_REPEATED_DIGITS_RE.match(cleaned_id):
        raise ValidationError("رقم الهوية الوطنية غير صالح.")

    return cleaned_id


def get_verified_claim_for_national_id(national_id):
    if not national_id:
        return None
    return (
        IdentityClaim.objects.select_related("user")
        .filter(
            national_id=national_id,
            status=IdentityClaimStatus.VERIFIED,
        )
        .first()
    )


def get_verified_claim_for_user(user):
    return (
        IdentityClaim.objects.filter(
            user=user,
            status=IdentityClaimStatus.VERIFIED,
        )
        .first()
    )


def get_effective_national_id_for_user(user):
    """
    Return the best available national ID for a user.

    Verified claims are the source of truth. The legacy shadow field on
    CustomUser is kept as a backward-compatible fallback while older flows
    and tests are still being migrated.
    """
    if not user:
        return None

    verified_claim = get_verified_claim_for_user(user)
    if verified_claim:
        return verified_claim.national_id

    shadow_national_id = normalize_national_id(getattr(user, "national_id", None))
    return shadow_national_id or None


def get_national_id_owner_user(national_id):
    """
    Return the user currently associated with a national ID.

    Prefer the verified claim owner when present. Fall back to the legacy
    shadow field on CustomUser for older records that predate claims.
    """
    cleaned_id = normalize_national_id(national_id)
    if not cleaned_id:
        return None

    verified_claim = get_verified_claim_for_national_id(cleaned_id)
    if verified_claim:
        return verified_claim.user

    return User.objects.filter(national_id=cleaned_id).first()


def _ensure_admin_user(admin_user):
    if not admin_user or not (admin_user.is_staff or admin_user.is_superuser):
        raise ValidationError("فقط إدارة المنصة يمكنها مراجعة طلبات الهوية.")


def _reject_queryset(queryset, *, reviewed_by=None, reason=""):
    now = timezone.now()
    return queryset.update(
        status=IdentityClaimStatus.REJECTED,
        verified_by=reviewed_by,
        verified_at=now,
        rejection_reason=reason or "",
        updated_at=now,
    )


@transaction.atomic
def assign_national_id(user, raw_id):
    cleaned_id = validate_national_id(normalize_national_id(raw_id))
    locked_user = User.objects.select_for_update().get(pk=user.pk)

    verified_claim = (
        IdentityClaim.objects.select_for_update()
        .filter(user=locked_user, status=IdentityClaimStatus.VERIFIED)
        .first()
    )
    if verified_claim:
        if verified_claim.national_id == cleaned_id:
            return verified_claim
        raise ValidationError("لا يمكن استبدال رقم هوية موثق مباشرة.")

    _reject_queryset(
        IdentityClaim.objects.select_for_update().filter(
            user=locked_user,
            status__in=IDENTITY_CLAIM_ACTIVE_STATUSES,
        ),
        reason="Closed because the user submitted a newer national ID claim.",
    )

    claim = IdentityClaim.objects.create(
        user=locked_user,
        national_id=cleaned_id,
        status=IdentityClaimStatus.UNVERIFIED,
    )

    # Keep the legacy shadow field populated for backward compatibility.
    if locked_user.national_id != cleaned_id:
        locked_user.national_id = cleaned_id
        locked_user.save(update_fields=["national_id"])

    return claim


@transaction.atomic
def submit_national_id_for_review(claim_id, evidence_file=None):
    claim = (
        IdentityClaim.objects.select_for_update()
        .select_related("user")
        .get(pk=claim_id)
    )

    if claim.status == IdentityClaimStatus.VERIFIED:
        return claim

    if claim.status == IdentityClaimStatus.REJECTED:
        raise ValidationError("لا يمكن إعادة إرسال مطالبة مرفوضة. أنشئ مطالبة جديدة.")

    update_fields = ["status", "updated_at"]
    claim.status = IdentityClaimStatus.UNDER_REVIEW

    if evidence_file is not None:
        claim.evidence_file = evidence_file
        update_fields.append("evidence_file")

    claim.save(update_fields=update_fields)
    return claim


@transaction.atomic
def verify_national_id(claim_id, admin_user):
    _ensure_admin_user(admin_user)

    claim = (
        IdentityClaim.objects.select_for_update()
        .select_related("user")
        .get(pk=claim_id)
    )

    if claim.status == IdentityClaimStatus.REJECTED:
        raise ValidationError("لا يمكن توثيق مطالبة مرفوضة.")

    if claim.status == IdentityClaimStatus.VERIFIED:
        return claim

    same_nid_claims = list(
        IdentityClaim.objects.select_for_update().filter(national_id=claim.national_id)
    )
    for other in same_nid_claims:
        if other.pk != claim.pk and other.status == IdentityClaimStatus.VERIFIED:
            raise ValidationError("تم توثيق رقم الهوية هذا بالفعل.")

    other_verified_for_user = (
        IdentityClaim.objects.select_for_update()
        .filter(user=claim.user, status=IdentityClaimStatus.VERIFIED)
        .exclude(pk=claim.pk)
        .first()
    )
    if other_verified_for_user and other_verified_for_user.national_id != claim.national_id:
        raise ValidationError("لا يمكن استبدال رقم هوية موثق مباشرة.")

    now = timezone.now()
    claim.status = IdentityClaimStatus.VERIFIED
    claim.verified_by = admin_user
    claim.verified_at = now
    claim.rejection_reason = ""
    try:
        claim.save(
            update_fields=["status", "verified_by", "verified_at", "rejection_reason", "updated_at"]
        )
    except IntegrityError as exc:
        raise ValidationError("تم توثيق رقم الهوية هذا بالفعل.") from exc

    _reject_queryset(
        IdentityClaim.objects.select_for_update().filter(
            national_id=claim.national_id,
            status__in=IDENTITY_CLAIM_ACTIVE_STATUSES,
        ).exclude(pk=claim.pk),
        reviewed_by=admin_user,
        reason="Rejected because another claim for this national ID was verified.",
    )
    _reject_queryset(
        IdentityClaim.objects.select_for_update().filter(
            user=claim.user,
            status__in=IDENTITY_CLAIM_ACTIVE_STATUSES,
        ).exclude(pk=claim.pk),
        reviewed_by=admin_user,
        reason="Rejected because this user now has a verified national ID claim.",
    )

    claim.user.national_id = claim.national_id
    claim.user.save(update_fields=["national_id"])
    return claim


@transaction.atomic
def reject_national_id(claim_id, admin_user, reason=None):
    _ensure_admin_user(admin_user)

    claim = (
        IdentityClaim.objects.select_for_update()
        .select_related("user")
        .get(pk=claim_id)
    )

    if claim.status == IdentityClaimStatus.VERIFIED:
        raise ValidationError("لا يمكن رفض مطالبة موثقة.")

    if claim.status == IdentityClaimStatus.REJECTED:
        return claim

    claim.status = IdentityClaimStatus.REJECTED
    claim.verified_by = admin_user
    claim.verified_at = timezone.now()
    claim.rejection_reason = reason or ""
    claim.save(update_fields=["status", "verified_by", "verified_at", "rejection_reason", "updated_at"])
    return claim
