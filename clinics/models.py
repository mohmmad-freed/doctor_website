from django.db import models
from django.conf import settings


class Clinic(models.Model):
    """Clinic model - each clinic has a main doctor and staff"""

    name = models.CharField(max_length=255)
    address = models.TextField()
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    description = models.TextField(blank=True)
    specialization = models.CharField(max_length=100, blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["-created_at"]


class ClinicStaff(models.Model):
    """Staff members (doctors and secretaries) working at a clinic"""

    ROLE_CHOICES = [
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
    is_used = models.BooleanField(default=False)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clinic_activation_used",
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
