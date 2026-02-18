import uuid
from django.db import models
from django.conf import settings
from clinics.models import Clinic


class AppointmentType(models.Model):
    """Types of appointments a doctor offers (e.g. General Checkup, Follow-up)."""

    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appointment_types")
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="appointment_types")
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True, default="")
    duration_minutes = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Appointment Type"
        verbose_name_plural = "Appointment Types"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "clinic", "name"],
                name="unique_appointment_type_per_doctor_clinic",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.duration_minutes}min, ₪{self.price}) - Dr. {self.doctor.name}"

    @property
    def display_name(self):
        return self.name_ar if self.name_ar else self.name


class Appointment(models.Model):
    """Core appointment booking record."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CHECKED_IN = "CHECKED_IN", "Checked In"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No Show"

    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appointments_as_patient")
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="appointments")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments_as_doctor",
    )
    appointment_type = models.ForeignKey(
        AppointmentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments",
    )
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    reason = models.TextField(blank=True, help_text="Reason for visit")
    intake_responses = models.JSONField(
        default=dict, blank=True,
        help_text="Legacy JSON responses. New flow uses AppointmentAnswer records.",
    )
    notes = models.TextField(blank=True, help_text="Doctor's notes after appointment")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.patient.name} - {self.clinic.name} on {self.appointment_date}"

    class Meta:
        ordering = ["-appointment_date", "-appointment_time"]
        verbose_name = "Appointment"
        verbose_name_plural = "Appointments"


class AppointmentAnswer(models.Model):
    """
    Stores a patient's answer to a single intake question for a specific appointment.

    Per APPOINTMENT_BOOKING_DATA_MODEL.md Section 3.4:
    - One answer per question per appointment (unique constraint).
    - Deleting an appointment deletes all its answers (CASCADE).
    - Deleting a question is blocked if answers exist (PROTECT).
    """

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        "doctors.DoctorIntakeQuestion",
        on_delete=models.PROTECT,
        related_name="answers",
    )
    answer_text = models.TextField(
        blank=True,
        help_text="The patient's text/choice answer.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Appointment Answer"
        verbose_name_plural = "Appointment Answers"
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "question"],
                name="unique_answer_per_question_per_appointment",
            ),
        ]

    def __str__(self):
        return f"Appt #{self.appointment_id} → Q{self.question_id}: {self.answer_text[:50]}"


def appointment_attachment_path(instance, filename):
    """Generate upload path: media/appointments/{appointment_id}/{uuid}_{filename}"""
    uid = uuid.uuid4().hex[:8]
    return f"appointments/{instance.appointment_id}/{uid}_{filename}"


class AppointmentAttachment(models.Model):
    """
    Stores file uploads associated with an appointment (from FILE questions).

    Per APPOINTMENT_BOOKING_DATA_MODEL.md Section 3.5.
    """

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    question = models.ForeignKey(
        "doctors.DoctorIntakeQuestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    file = models.FileField(upload_to=appointment_attachment_path)
    original_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Size in bytes.")
    mime_type = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Appointment Attachment"
        verbose_name_plural = "Appointment Attachments"

    def __str__(self):
        return f"{self.original_name} (Appt #{self.appointment_id})"