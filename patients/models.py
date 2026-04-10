import uuid
from django.db import models
from django.conf import settings
from core.validators.file_validators import validate_file_extension, validate_file_signature, validate_file_size


def _record_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"medical_records/patient_{instance.patient_id}/{uuid.uuid4().hex}.{ext}"


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
    file_number = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Auto-generated per-clinic file number (e.g. 2026-0001). Set by the secretary on registration.",
    )

    class Meta:
        unique_together = [("clinic", "patient")]
        verbose_name = "Clinic Patient"
        verbose_name_plural = "Clinic Patients"
        ordering = ["-registered_at"]

    def __str__(self):
        return f"{self.patient.name} @ {self.clinic.name}"


# ──────────────────────────────────────────────────────────────────────────────
# Clinical Records (created by doctors, linked to patients)
# ──────────────────────────────────────────────────────────────────────────────

class ClinicalNote(models.Model):
    """SOAP-format or free-text clinical note written by a doctor."""

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clinical_notes"
    )
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="clinical_notes"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_notes"
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clinical_notes",
    )

    # SOAP fields
    subjective = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    assessment = models.TextField(blank=True)
    plan = models.TextField(blank=True)
    free_text = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Clinical Note"
        verbose_name_plural = "Clinical Notes"

    def __str__(self):
        return f"Note by {self.doctor.name} for {self.patient.name} ({self.created_at.date()})"


class Order(models.Model):
    """Unified order: drug, lab, radiology, microbiology, or procedure."""

    class OrderType(models.TextChoices):
        DRUG = "DRUG", "Drug"
        LAB = "LAB", "Lab"
        RADIOLOGY = "RADIOLOGY", "Radiology"
        MICROBIOLOGY = "MICROBIOLOGY", "Microbiology"
        PROCEDURE = "PROCEDURE", "Procedure"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="orders"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_orders"
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    order_type = models.CharField(max_length=20, choices=OrderType.choices)
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)

    # Drug-specific (only populated when order_type == DRUG)
    dosage = models.CharField(max_length=100, blank=True)
    frequency = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"

    def __str__(self):
        return f"{self.get_order_type_display()}: {self.title} for {self.patient.name}"


class Prescription(models.Model):
    """A patient-facing prescription, optionally linked to an appointment."""

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prescriptions"
    )
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="prescriptions"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_prescriptions"
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Prescription"
        verbose_name_plural = "Prescriptions"

    def __str__(self):
        return f"Rx for {self.patient.name} by {self.doctor.name} ({self.created_at.date()})"


class PrescriptionItem(models.Model):
    """A single medication line in a prescription."""

    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name="items"
    )
    medication_name = models.CharField(max_length=255)
    dosage = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    duration = models.CharField(max_length=100, blank=True)
    instructions = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Prescription Item"
        verbose_name_plural = "Prescription Items"

    def __str__(self):
        return f"{self.medication_name} – {self.dosage}"


class MedicalRecord(models.Model):
    """Uploaded medical document (PDF, image) categorised by type."""

    class Category(models.TextChoices):
        LAB = "LAB", "Lab Report"
        RADIOLOGY = "RADIOLOGY", "Radiology"
        GENERAL = "GENERAL", "General Document"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medical_records"
    )
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="medical_records"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_records",
    )
    title = models.CharField(max_length=255)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.GENERAL
    )
    file = models.FileField(upload_to=_record_upload_path, validators=[validate_file_size])
    original_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Medical Record"
        verbose_name_plural = "Medical Records"

    def __str__(self):
        return f"{self.title} ({self.get_category_display()}) – {self.patient.name}"
