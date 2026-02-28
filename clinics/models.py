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
    phone = models.CharField(max_length=20)
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

    def __str__(self):
        return f"{self.user.name} - {self.role} at {self.clinic.name}"

    class Meta:
        unique_together = ["clinic", "user"]
        verbose_name = "Clinic Staff"
        verbose_name_plural = "Clinic Staff"


class ClinicActivationCode(models.Model):
    """Activation codes for creating new clinics with main doctor"""

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
