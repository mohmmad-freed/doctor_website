import re

from django.db import models


class IdentityClaimStatus(models.TextChoices):
    UNVERIFIED = "UNVERIFIED", "Unverified"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"


NATIONAL_ID_LENGTH = 9
NATIONAL_ID_STRIP_RE = re.compile(r"[\s\-.]+")
NATIONAL_ID_REPEATED_DIGITS_RE = re.compile(r"^(0|1)\1{8}$")

IDENTITY_CLAIM_ACTIVE_STATUSES = (
    IdentityClaimStatus.UNVERIFIED,
    IdentityClaimStatus.UNDER_REVIEW,
)

INVALID_NATIONAL_ID_VALUES = {
    "000000000",
    "111111111",
}
