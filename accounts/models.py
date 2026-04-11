import os
import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.conf import settings
from django.db.models import Q

from core.validators.file_validators import (
    validate_file_extension,
    validate_file_signature,
    validate_file_size,
)
from .constants import IdentityClaimStatus


class City(models.Model):
    """Cities available in the system"""

    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Cities"
        ordering = ["name"]


class CustomUserManager(BaseUserManager):
    """Custom user manager where phone is the unique identifier"""

    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("The Phone field must be set")
        # Auto-populate roles from role if not explicitly provided
        if "roles" not in extra_fields:
            role = extra_fields.get("role", "PATIENT")
            extra_fields["roles"] = [role]
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", "MAIN_DOCTOR")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone, password, **extra_fields)


class CustomUser(AbstractUser):
    """Custom user model with email login and role-based access"""

    ROLE_CHOICES = [
        ("PATIENT", "Patient"),
        ("MAIN_DOCTOR", "Main Doctor"),
        ("DOCTOR", "Doctor"),
        ("SECRETARY", "Secretary"),
    ]

    # Remove username, use email instead
    username = None
    email = models.EmailField(blank=True, null=True)  # Made optional for patients

    # Required fields for all users
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)  # Required and unique
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="PATIENT")
    roles = ArrayField(
        models.CharField(max_length=20, choices=ROLE_CHOICES),
        default=list,
        blank=True,
        help_text="All roles assigned to this user (a user may hold multiple roles simultaneously).",
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Designates whether the user has verified their phone number.",
    )
    email_verified = models.BooleanField(
        default=False,
        help_text="Designates whether the user has verified their email address.",
    )
    pending_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Temporary storage for email until verification is complete.",
    )

    # Patient-specific fields (will be NULL for non-patients)
    national_id = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        db_index=True,
        help_text="رقم الهوية الوطنية الموثق فقط. الطلبات المعلقة محفوظة في سجل المطالبات.",
    )
    city = models.ForeignKey("City", on_delete=models.SET_NULL, null=True, blank=True)

    LANGUAGE_CHOICES = [
        ("ar", "Arabic / العربية"),
        ("en", "English"),
    ]

    preferred_language = models.CharField(
        max_length=5,
        choices=LANGUAGE_CHOICES,
        blank=True,
        null=True,
        help_text="User's preferred UI language. Null means role-based default applies.",
    )

    # Set phone as the unique identifier for login
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def has_role(self, role):
        """Return True if *role* is in this user's roles list."""
        return role in (self.roles or [])

    def __str__(self):
        return f"{self.name} ({self.phone}) - {self.role}"

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"


def identity_claim_evidence_upload_path(instance, filename):
    extension = os.path.splitext(filename)[1]
    return f"identity_claims/user_{instance.user_id}/{uuid.uuid4().hex}{extension}"


class IdentityClaim(models.Model):
    """Global national ID claim record."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identity_claims",
    )
    national_id = models.CharField(max_length=9, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=IdentityClaimStatus.choices,
        default=IdentityClaimStatus.UNVERIFIED,
        db_index=True,
    )
    evidence_file = models.FileField(
        upload_to=identity_claim_evidence_upload_path,
        blank=True,
        null=True,
        validators=[validate_file_extension, validate_file_signature, validate_file_size],
        help_text="Optional evidence submitted for manual review.",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="identity_claim_reviews",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Identity Claim"
        verbose_name_plural = "Identity Claims"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="identity_claim_user_status_idx"),
            models.Index(fields=["national_id", "status"], name="identity_claim_nid_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["national_id"],
                condition=models.Q(status=IdentityClaimStatus.VERIFIED),
                name="unique_verified_identity_claim_per_national_id",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status=IdentityClaimStatus.VERIFIED),
                name="unique_verified_identity_claim_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.national_id} ({self.status})"
