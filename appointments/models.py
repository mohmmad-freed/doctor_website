from django.db import models
from django.conf import settings
from clinics.models import Clinic


class AppointmentType(models.Model):
    """
    Types of appointments a doctor offers (e.g. General Checkup, Follow-up).

    Each doctor defines their own appointment types per clinic.
    Used by patients to select the type of visit when booking,
    and by the availability engine to generate time slots of the correct duration.
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appointment_types",
        help_text="The doctor who offers this appointment type.",
    )
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="appointment_types",
        help_text="The clinic where this appointment type is offered.",
    )
    name = models.CharField(
        max_length=100,
        help_text="e.g. 'General Checkup', 'Follow-up', 'Consultation'",
    )
    duration_minutes = models.PositiveIntegerField(
        help_text="Duration of this appointment type in minutes.",
    )
    price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="Price in ILS.",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description visible to patients.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive types are hidden from patients but preserved for history.",
    )
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


class Appointment(models.Model):
    """
    Appointment bookings between patients and clinics.

    Status lifecycle:
        CONFIRMED → CHECKED_IN → IN_PROGRESS → COMPLETED
        CONFIRMED → CANCELLED
        CONFIRMED → NO_SHOW
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CHECKED_IN = "CHECKED_IN", "Checked In"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No Show"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appointments_as_patient",
    )
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="appointments"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments_as_doctor",
        help_text="Assigned doctor for this appointment",
    )
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
        help_text="Type of appointment booked",
    )
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CONFIRMED,
    )
    reason = models.TextField(blank=True, help_text="Reason for visit")
    notes = models.TextField(blank=True, help_text="Doctor's notes after appointment")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments_created",
        help_text="Who created this appointment (patient or secretary)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.patient.name} - {self.clinic.name} on {self.appointment_date}"

    class Meta:
        ordering = ["-appointment_date", "-appointment_time"]
        verbose_name = "Appointment"
        verbose_name_plural = "Appointments"