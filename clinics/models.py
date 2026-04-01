from django.db import models
from django.conf import settings


class Clinic(models.Model):
    """Clinic model - each clinic has a main doctor and staff"""

    STATUS_CHOICES = [
        ("PENDING", "Pending Review"),
        ("ACTIVE", "Active"),
        ("SUSPENDED", "Suspended"),
    ]

    name = models.CharField(max_length=255)
    address = models.TextField()
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    description = models.TextField(blank=True)
    specialization = models.CharField(max_length=100, blank=True)
    specialties = models.ManyToManyField(
        "doctors.Specialty",
        blank=True,
        related_name="clinics",
    )
    city = models.ForeignKey(
        "accounts.City",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clinics",
    )
    main_doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_clinic"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["-created_at"]


class ClinicStaff(models.Model):
    """Staff members (doctors and secretaries) working at a clinic"""

    ROLE_CHOICES = [
        ("MAIN_DOCTOR", "Main Doctor"),
        ("DOCTOR", "Doctor"),
        ("SECRETARY", "Secretary"),
    ]

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="staff_members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="clinic_employments",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="staff_added",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When set, indicates this membership was revoked. "
                  "Revoked memberships do not block re-invitation.",
    )

    def __str__(self):
        return f"{self.user.name} - {self.role} at {self.clinic.name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "user", "role"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_staff_role_per_clinic",
            )
        ]
        verbose_name = "Clinic Staff"
        verbose_name_plural = "Clinic Staff"


import uuid
from django.utils import timezone

class ClinicInvitation(models.Model):
    """
    Invitation for a doctor to join a clinic.
    """
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("ACCEPTED", "Accepted"),
        ("REJECTED", "Rejected"),
        ("EXPIRED", "Expired"),
        ("CANCELLED", "Cancelled"),
    ]

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="invitations"
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    doctor_name = models.CharField(max_length=255)
    doctor_phone = models.CharField(max_length=20, db_index=True)
    doctor_email = models.EmailField()
    doctor_national_id = models.CharField(max_length=20, blank=True, default="")
    specialties = models.ManyToManyField(
        "doctors.Specialty",
        blank=True,
        related_name="invitations",
    )
    ROLE_CHOICES = [
        ("DOCTOR", "Doctor"),
        ("SECRETARY", "Secretary"),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="DOCTOR")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Clinic Invitation"
        verbose_name_plural = "Clinic Invitations"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "doctor_phone"],
                condition=models.Q(status="PENDING"),
                name="unique_pending_invitation",
            )
        ]

    def __str__(self):
        return f"Invite for {self.doctor_name} ({self.doctor_phone}) to {self.clinic.name} - {self.status}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.is_expired and self.status == "PENDING":
             pass # Service logic should handle marking this EXPIRED


class PendingDoctorIdentity(models.Model):
    """
    Atomic identity creation lock.
    Prevents race conditions when multiple clinics invite the same
    unregistered phone number simultaneously.
    """
    phone = models.CharField(
        max_length=20, unique=True, db_index=True,
        help_text="Standardized phone number being onboarded.",
    )
    created_by_invitation = models.ForeignKey(
        ClinicInvitation,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pending_identity_lock",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pending Doctor Identity"
        verbose_name_plural = "Pending Doctor Identities"

    def __str__(self):
        return f"PendingIdentity({self.phone})"


class InvitationAuditLog(models.Model):
    """Lightweight audit trail for invitation lifecycle events."""

    ACTION_CHOICES = [
        ("CREATED", "Created"),
        ("CANCELLED", "Cancelled"),
        ("ACCEPTED", "Accepted"),
        ("REJECTED", "Rejected"),
        ("EXPIRED", "Expired"),
    ]

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="invitation_audit_logs"
    )
    invitation = models.ForeignKey(
        ClinicInvitation, on_delete=models.CASCADE, related_name="audit_logs"
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Invitation Audit Log"
        verbose_name_plural = "Invitation Audit Logs"

    def __str__(self):
        return f"{self.action} - {self.invitation.doctor_name} @ {self.clinic.name}"


class ClinicSubscription(models.Model):
    """Subscription plan bound to a clinic, seeded from the activation code."""

    # Billing period (kept for backward compatibility)
    PLAN_CHOICES = [
        ("MONTHLY", "Monthly"),
        ("YEARLY", "Yearly"),
    ]
    # Plan tier — defines capacity limits
    class PlanName(models.TextChoices):
        SMALL = "SMALL", "صغير"
        MEDIUM = "MEDIUM", "متوسط"
        ENTERPRISE = "ENTERPRISE", "مؤسسة"

    # Default limits per plan tier.
    # ENTERPRISE is intentionally absent — admin sets max_doctors/max_secretaries
    # explicitly on the subscription or activation code for each enterprise clinic.
    PLAN_LIMITS = {
        "SMALL":  {"doctors": 2, "secretaries": 5},
        "MEDIUM": {"doctors": 4, "secretaries": 5},
    }

    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("SUSPENDED", "Suspended"),
    ]

    clinic = models.OneToOneField(
        Clinic, on_delete=models.CASCADE, related_name="subscription"
    )
    plan_type = models.CharField(max_length=10, choices=PLAN_CHOICES, default="MONTHLY")
    plan_name = models.CharField(
        max_length=20,
        choices=PlanName.choices,
        default=PlanName.SMALL,
        help_text="Plan tier that determines doctor/secretary capacity.",
    )
    expires_at = models.DateTimeField()
    max_doctors = models.PositiveIntegerField(
        default=2,
        help_text="Override capacity; 0 = unlimited. Auto-set from plan_name if left at default.",
    )
    max_secretaries = models.PositiveIntegerField(
        default=5,
        help_text="Override capacity; 0 = unlimited. Auto-set from plan_name if left at default.",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="ACTIVE")
    notes = models.TextField(blank=True, help_text="Internal admin notes (billing, support, etc.)")
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions_activated",
        help_text="Admin who last activated/extended this subscription.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Helper properties ─────────────────────────────────────────────

    def is_effectively_active(self) -> bool:
        """True only when status=ACTIVE and not yet expired."""
        from django.utils import timezone
        return self.status == "ACTIVE" and self.expires_at > timezone.now()

    def current_doctors_count(self) -> int:
        from clinics.models import ClinicStaff
        return ClinicStaff.objects.filter(
            clinic=self.clinic,
            role="DOCTOR",
            revoked_at__isnull=True,
        ).count()

    def current_secretaries_count(self) -> int:
        from clinics.models import ClinicStaff
        return ClinicStaff.objects.filter(
            clinic=self.clinic,
            role="SECRETARY",
            revoked_at__isnull=True,
        ).count()

    def can_add_doctor(self) -> bool:
        """Return True if another doctor can be added under the current limits.

        A max_doctors value of 0 means unlimited — this is an explicit admin
        opt-in (typically used for ENTERPRISE plans where the admin sets the
        field directly rather than relying on PLAN_LIMITS defaults).
        """
        if self.max_doctors == 0:  # 0 = unlimited (explicit admin opt-in)
            return True
        return self.current_doctors_count() < self.max_doctors

    def can_add_secretary(self) -> bool:
        """Return True if another secretary can be added under the current limits.

        A max_secretaries value of 0 means unlimited — this is an explicit
        admin opt-in (typically used for ENTERPRISE plans where the admin sets
        the field directly rather than relying on PLAN_LIMITS defaults).
        """
        if self.max_secretaries == 0:  # 0 = unlimited (explicit admin opt-in)
            return True
        return self.current_secretaries_count() < self.max_secretaries

    def __str__(self):
        return f"{self.clinic.name} — {self.plan_name} / {self.plan_type} (expires {self.expires_at:%Y-%m-%d})"

    class Meta:
        verbose_name = "Clinic Subscription"
        verbose_name_plural = "Clinic Subscriptions"


class ClinicActivationCode(models.Model):
    """Activation codes for creating new clinics with main doctor"""

    PLAN_CHOICES = [
        ("MONTHLY", "Monthly"),
        ("YEARLY", "Yearly"),
    ]

    code = models.CharField(max_length=20, unique=True)
    clinic_name = models.CharField(max_length=255, help_text="Pre-assigned clinic name")
    phone = models.CharField(
        max_length=20,
        default="",
        help_text="Normalized phone of the intended owner (059/056 format)",
    )
    national_id = models.CharField(
        max_length=9,
        default="",
        help_text="9-digit national ID of the intended owner",
    )
    plan_type = models.CharField(
        max_length=10,
        choices=PLAN_CHOICES,
        default="MONTHLY",
        help_text="Subscription billing period granted to the clinic.",
    )
    plan_name = models.CharField(
        max_length=20,
        choices=ClinicSubscription.PlanName.choices,
        default=ClinicSubscription.PlanName.SMALL,
        help_text="Plan tier (capacity) granted to the clinic.",
    )
    subscription_expires_at = models.DateTimeField(
        help_text="When the subscription granted by this code expires.",
    )
    max_doctors = models.PositiveIntegerField(
        default=2,
        help_text="Maximum number of doctors allowed under this subscription.",
    )
    max_secretaries = models.PositiveIntegerField(
        default=5,
        help_text="Maximum number of secretaries allowed under this subscription.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional expiry date. Leave blank for no expiry.",
    )
    is_used = models.BooleanField(default=False)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clinic_activation_used",
    )
    used_by_clinic = models.OneToOneField(
        "Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activation_code",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        status = "Used" if self.is_used else "Available"
        return f"{self.code} - {self.clinic_name} ({status})"

    class Meta:
        verbose_name = "Clinic Activation Code"
        verbose_name_plural = "Clinic Activation Codes"
        ordering = ["-created_at"]


class ClinicVerification(models.Model):
    """Tracks OTP verification status for each communication channel of a clinic."""

    clinic = models.OneToOneField(
        Clinic, on_delete=models.CASCADE, related_name="verification"
    )
    owner_phone_verified_at = models.DateTimeField(null=True, blank=True)
    owner_email_verified_at = models.DateTimeField(null=True, blank=True)
    clinic_phone_verified_at = models.DateTimeField(null=True, blank=True)
    clinic_email_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_fully_verified(self):
        """True when all required channels are verified."""
        return bool(self.owner_phone_verified_at and self.owner_email_verified_at)

    def next_pending_step(self, clinic_id):
        """Return the resolved URL of the next unverified step, or None if all done."""
        from django.urls import reverse
        if not self.owner_phone_verified_at:
            return reverse("clinics:verify_owner_phone", kwargs={"clinic_id": clinic_id})
        if not self.owner_email_verified_at:
            return reverse("clinics:verify_owner_email", kwargs={"clinic_id": clinic_id})
        return None

    def __str__(self):
        return f"Verification for {self.clinic.name}"

    class Meta:
        verbose_name = "Clinic Verification"
        verbose_name_plural = "Clinic Verifications"


class ClinicWorkingHours(models.Model):
    """
    General clinic working hours. Defines when the clinic is open.
    Doctors must schedule their availability within these bounds.
    """

    DAY_CHOICES = [
        (0, "الاثنين"),
        (1, "الثلاثاء"),
        (2, "الأربعاء"),
        (3, "الخميس"),
        (4, "الجمعة"),
        (5, "السبت"),
        (6, "الأحد"),
    ]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="working_hours")
    weekday = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Clinic Working Hours"
        verbose_name_plural = "Clinic Working Hours"
        ordering = ["weekday", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "weekday", "start_time", "end_time"],
                name="unique_clinic_working_hours"
            )
        ]

    def __str__(self):
        day = self.get_weekday_display()
        if self.is_closed:
            return f"{self.clinic.name} - {day} (Closed)"
        return f"{self.clinic.name} - {day} ({self.start_time:%H:%M}-{self.end_time:%H:%M})"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()

        if self.is_closed:
            # Option A: If closed, explicitly forbid start_time/end_time
            if self.start_time is not None or self.end_time is not None:
                raise ValidationError("If the clinic is closed on this day, start time and end time must be empty.")
            
            # Prevent creating additional ranges for this weekday if marked as closed
            if self.clinic_id is not None and self.weekday is not None:
                existing = ClinicWorkingHours.objects.filter(clinic=self.clinic, weekday=self.weekday)
                if self.pk:
                    existing = existing.exclude(pk=self.pk)
                if existing.exists():
                    raise ValidationError("Cannot mark the day as closed when other working hour ranges exist for this day.")
        else:
            if self.start_time is None or self.end_time is None:
                raise ValidationError("Start time and end time are required unless the day is marked as closed.")
            
            if self.start_time >= self.end_time:
                raise ValidationError({"end_time": "End time must be after start time."})
            
            # Prevent overlaps
            if self.clinic_id is not None and self.weekday is not None:
                # First check if there is a 'closed' record for this day
                existing_closed = ClinicWorkingHours.objects.filter(clinic=self.clinic, weekday=self.weekday, is_closed=True)
                if self.pk:
                    existing_closed = existing_closed.exclude(pk=self.pk)
                if existing_closed.exists():
                    raise ValidationError("Cannot add working hours to a day that is marked as closed.")

                overlapping = ClinicWorkingHours.objects.filter(
                    clinic=self.clinic,
                    weekday=self.weekday,
                    start_time__lt=self.end_time,
                    end_time__gt=self.start_time,
                    is_closed=False
                )
                if self.pk:
                    overlapping = overlapping.exclude(pk=self.pk)
                if overlapping.exists():
                    conflict = overlapping.first()
                    raise ValidationError(
                        f"This time overlaps with an existing working hour range on "
                        f"{self.get_weekday_display()}: "
                        f"{conflict.start_time:%H:%M}-{conflict.end_time:%H:%M}."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ClinicHoliday(models.Model):
    """
    Clinic-level closure / holiday.
    No bookings are allowed on dates that fall within an active holiday range.
    """
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="holidays"
    )
    title = models.CharField(max_length=255, help_text="e.g. عطلة عيد الأضحى")
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="clinic_holidays_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Clinic Holiday"
        verbose_name_plural = "Clinic Holidays"
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.clinic.name} — {self.title} ({self.start_date} → {self.end_date})"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "تاريخ الانتهاء يجب أن يكون بعد تاريخ البداية."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DoctorAvailabilityException(models.Model):
    """
    Doctor-specific day-off / unavailability at a particular clinic.
    Slots will not be generated for dates within an active exception range.
    """
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_exceptions",
    )
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="doctor_exceptions"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=500, blank=True, help_text="e.g. إجازة سنوية، مؤتمر طبي")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="doctor_exceptions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctor Availability Exception"
        verbose_name_plural = "Doctor Availability Exceptions"
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.doctor} @ {self.clinic.name} — off {self.start_date} → {self.end_date}"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "تاريخ الانتهاء يجب أن يكون بعد تاريخ البداية."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# ============================================================
# ORDER CATALOG
# ============================================================


class DrugFamily(models.Model):
    """Drug families / categories for the clinic's drug catalog (e.g. Antibiotics, Beta-Blockers)."""

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="drug_families"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("clinic", "name")]
        verbose_name = "Drug Family"
        verbose_name_plural = "Drug Families"

    def __str__(self):
        return f"{self.name} ({self.clinic.name})"


class DrugProduct(models.Model):
    """Individual drug in the clinic's catalog, optionally grouped under a DrugFamily."""

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="drug_products"
    )
    family = models.ForeignKey(
        DrugFamily,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    generic_name = models.CharField(max_length=255)
    commercial_name = models.CharField(max_length=255, blank=True)
    default_dosage = models.CharField(max_length=100, blank=True)
    default_frequency = models.CharField(max_length=100, blank=True)
    default_duration = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["generic_name"]
        verbose_name = "Drug Product"
        verbose_name_plural = "Drug Products"

    def __str__(self):
        return f"{self.generic_name} ({self.clinic.name})"


class OrderCatalogItem(models.Model):
    """Named catalog items for non-drug order types (Lab, Radiology, Microbiology, Procedure)."""

    class Category(models.TextChoices):
        LAB = "LAB", "Lab"
        RADIOLOGY = "RADIOLOGY", "Radiology"
        MICROBIOLOGY = "MICROBIOLOGY", "Microbiology"
        PROCEDURE = "PROCEDURE", "Procedure"

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="catalog_items"
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "name"]
        unique_together = [("clinic", "category", "name")]
        verbose_name = "Order Catalog Item"
        verbose_name_plural = "Order Catalog Items"

    def __str__(self):
        return f"[{self.category}] {self.name} ({self.clinic.name})"
