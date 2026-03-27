from django.db import models
from django.conf import settings
from core.validators.file_validators import validate_file_extension, validate_file_signature, validate_file_size


class PatientProfile(models.Model):
    """Extended profile for patient users"""

    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
    ]

    BLOOD_TYPE_CHOICES = [
        ("A+", "A+"),
        ("A-", "A-"),
        ("B+", "B+"),
        ("B-", "B-"),
        ("AB+", "AB+"),
        ("AB-", "AB-"),
        ("O+", "O+"),
        ("O-", "O-"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_profile",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    blood_type = models.CharField(max_length=3, choices=BLOOD_TYPE_CHOICES, blank=True)
    medical_history = models.TextField(blank=True)
    allergies = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)

    emergency_contact_phone = models.CharField(max_length=20, blank=True)

    def get_avatar_upload_path(instance, filename):
        """
        Dynamic path to avoid collisions and Organize files.
        Format: patients/avatars/user_<id>/<filename>
        """
        return f"patients/avatars/user_{instance.user.id}/{filename}"

    avatar = models.ImageField(
        upload_to=get_avatar_upload_path,
        blank=True,
        null=True,
        validators=[validate_file_extension, validate_file_signature, validate_file_size]
    )

    def __str__(self):
        return f"Patient Profile - {self.user.name}"

    class Meta:
        verbose_name = "Patient Profile"
        verbose_name_plural = "Patient Profiles"


class ClinicPatient(models.Model):
    """Tracks which patients are registered in which clinics."""

    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="clinic_patients",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="clinic_registrations",
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patients_registered",
    )
    registered_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("clinic", "patient")]
        verbose_name = "Clinic Patient"
        verbose_name_plural = "Clinic Patients"
        ordering = ["-registered_at"]

    def __str__(self):
        return f"{self.patient.name} @ {self.clinic.name}"
