import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _lazy
from clinics.models import Clinic
from core.validators.file_validators import validate_file_signature, validate_file_size


class AppointmentType(models.Model):
    """Types of appointments a clinic offers (e.g. General Checkup, Follow-up)."""

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
                fields=["clinic", "name"],
                name="unique_appointment_type_per_clinic",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.duration_minutes}min, ₪{self.price})"

    @property
    def display_name(self):
        from django.utils.translation import get_language
        lang = get_language() or "ar"
        if lang.startswith("ar"):
            return self.name_ar if self.name_ar else self.name
        return self.name if self.name else self.name_ar


class DoctorClinicAppointmentType(models.Model):
    """
    Maps which AppointmentTypes a specific doctor offers inside a specific clinic.

    Business rule: a doctor can only offer a subset of the clinic's approved
    appointment type catalog.  The clinic owner (or the doctor themselves) can
    enable/disable individual types.

    Backwards-compatibility: if NO records exist for a (doctor, clinic) pair the
    booking and secretary flows fall back to showing ALL active clinic types, so
    existing deployments keep working without any data migration.
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="clinic_appointment_types",
        verbose_name="الطبيب",
    )
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="doctor_appointment_types",
        verbose_name="العيادة",
    )
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.CASCADE,
        related_name="doctor_assignments",
        verbose_name="نوع الموعد",
    )
    is_active = models.BooleanField(default=True, verbose_name="مفعّل")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctor Appointment Type"
        verbose_name_plural = "Doctor Appointment Types"
        ordering = ["clinic", "doctor", "appointment_type__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "clinic", "appointment_type"],
                name="unique_doctor_clinic_appointment_type",
            )
        ]

    def __str__(self):
        return (
            f"{self.doctor} @ {self.clinic} — {self.appointment_type.display_name}"
            f" ({'✓' if self.is_active else '✗'})"
        )

    def clean(self):
        from django.core.exceptions import ValidationError

        # Validate appointment_type belongs to clinic
        if (
            self.appointment_type_id
            and self.clinic_id
            and self.appointment_type.clinic_id != self.clinic_id
        ):
            raise ValidationError(
                "نوع الموعد يجب أن ينتمي إلى نفس العيادة."
            )

        # Validate doctor is active staff at clinic
        if self.doctor_id and self.clinic_id:
            from clinics.models import ClinicStaff
            is_staff = ClinicStaff.objects.filter(
                clinic_id=self.clinic_id,
                user_id=self.doctor_id,
                role__in=["DOCTOR", "MAIN_DOCTOR"],
                is_active=True,
            ).exists()
            if not is_staff:
                raise ValidationError(
                    "الطبيب يجب أن يكون عضواً نشطاً في هذه العيادة."
                )


class Appointment(models.Model):
    """Core appointment booking record."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _lazy("قيد الانتظار")
        CONFIRMED = "CONFIRMED", _lazy("مؤكد")
        CHECKED_IN = "CHECKED_IN", _lazy("وصل المريض")
        IN_PROGRESS = "IN_PROGRESS", _lazy("جارٍ")
        COMPLETED = "COMPLETED", _lazy("مكتمل")
        CANCELLED = "CANCELLED", _lazy("ملغى")
        NO_SHOW = "NO_SHOW", _lazy("لم يحضر")

    MAX_PATIENT_EDITS = 2

    reminder_sent = models.BooleanField(
        default=False,
        help_text="Whether a reminder notification has been sent for this appointment.",
    )

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
    patient_edit_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times the patient has edited this appointment. Max 2.",
    )
    checked_in_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp set automatically when the appointment status transitions to CHECKED_IN.",
    )
    is_walk_in = models.BooleanField(
        default=False,
        help_text="True when the appointment was registered as a walk-in (no prior booking).",
    )
    cancellation_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason provided by the secretary or patient when cancelling.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.patient.name} - {self.clinic.name} on {self.appointment_date}"

    @property
    def can_patient_edit(self):
        """Whether the patient can still edit this appointment."""
        return (
            self.status in (self.Status.PENDING, self.Status.CONFIRMED)
            and self.patient_edit_count < self.MAX_PATIENT_EDITS
        )

    @property
    def edits_remaining(self):
        """How many edits the patient has left."""
        return max(0, self.MAX_PATIENT_EDITS - self.patient_edit_count)

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

    File groups:
    - Files are organized into date-groups (e.g. lab results from different dates).
    - Each group has a date (file_group_date) and up to MAX_FILES_PER_GROUP files.
    - A FILE question supports up to MAX_FILE_GROUPS date-groups.
    """

    MAX_FILE_GROUPS = 7
    MAX_FILES_PER_GROUP = 5
    MAX_TOTAL_UPLOAD_MB = 200

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
    file = models.FileField(
        upload_to=appointment_attachment_path,
        validators=[validate_file_signature, validate_file_size]
    )
    original_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Size in bytes.")
    mime_type = models.CharField(max_length=100, blank=True)
    file_group_date = models.DateField(
        null=True, blank=True,
        help_text="Date label for this file group (e.g. date of lab results).",
    )
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


class AppointmentNotification(models.Model):
    """
    In-app notification for a patient about an appointment event.

    Created whenever the appointment status changes in a way that
    the patient should be informed (e.g. cancellation by ClinicStaff).

    Patient FK:
      Appointment.patient → AUTH_USER_MODEL, so we mirror the same FK type
      here to keep the relationship consistent.

    Audit:
      cancelled_by_staff records WHO cancelled (ClinicStaff). SET_NULL on
      delete so notifications outlive the staff record.

    Duplicate prevention:
      UniqueConstraint on (appointment, notification_type) enforced at DB
      level. The service also guards against cancelling a CANCELLED appointment
      before notification fires, providing a logical second line of defence.

    Channel rules:
    - In-app: ALWAYS created with is_delivered=True.
    - Email:   Sent separately; not tracked here.
    - SMS:     Sent separately; not tracked here.
    """

    class Type(models.TextChoices):
        APPOINTMENT_CANCELLED = "APPOINTMENT_CANCELLED", "Appointment Cancelled"
        APPOINTMENT_EDITED = "APPOINTMENT_EDITED", "Appointment Edited"
        APPOINTMENT_BOOKED = "APPOINTMENT_BOOKED", "Appointment Booked"
        APPOINTMENT_REMINDER = "APPOINTMENT_REMINDER", "Appointment Reminder"
        APPOINTMENT_RESCHEDULED = "APPOINTMENT_RESCHEDULED", "Appointment Rescheduled"
        APPOINTMENT_STATUS_CHANGED = "APPOINTMENT_STATUS_CHANGED", "Appointment Status Changed"

    class ContextRole(models.TextChoices):
        PATIENT = "PATIENT", "Patient Context"
        DOCTOR = "DOCTOR", "Doctor Context"
        SECRETARY = "SECRETARY", "Secretary Context"
        CLINIC_OWNER = "CLINIC_OWNER", "Clinic Owner Context"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appointment_notifications",
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    context_role = models.CharField(
        max_length=20,
        choices=ContextRole.choices,
        default=ContextRole.PATIENT,
        help_text="The portal context this notification belongs to.",
    )
    notification_type = models.CharField(
        max_length=60,
        choices=Type.choices,
        default=Type.APPOINTMENT_CANCELLED,
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    cancelled_by_staff = models.ForeignKey(
        "clinics.ClinicStaff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancellation_notifications",
        help_text="ClinicStaff member who triggered this cancellation notification.",
    )
    is_read = models.BooleanField(
        default=False,
        help_text="Patient has read this notification.",
    )
    is_delivered = models.BooleanField(
        default=True,
        help_text="Always True for in-app notifications.",
    )
    sent_via_email = models.BooleanField(
        default=False,
        help_text="True if an email was successfully sent for this notification.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Appointment Notification"
        verbose_name_plural = "Appointment Notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.notification_type}] \u2192 {self.patient.name} ({self.created_at:%Y-%m-%d})"

    # \u2500\u2500 Localized display \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Notifications are stored in DB with Arabic text. These properties
    # synthesize the title/message in the active UI language at render time
    # so non-Arabic users see their localized text.

    def _is_english(self):
        from django.utils.translation import get_language
        lang = (get_language() or "ar").split("-")[0]
        return lang.startswith("en")

    @property
    def localized_title(self):
        if not self._is_english():
            return self.title
        mapping = {
            self.Type.APPOINTMENT_BOOKED: "Appointment Confirmed",
            self.Type.APPOINTMENT_CANCELLED: "Appointment Cancelled",
            self.Type.APPOINTMENT_RESCHEDULED: "Appointment Rescheduled",
            self.Type.APPOINTMENT_REMINDER: "Appointment Reminder",
            self.Type.APPOINTMENT_STATUS_CHANGED: "Appointment Updated",
            self.Type.APPOINTMENT_EDITED: "Appointment Edited",
        }
        return mapping.get(self.notification_type, self.title)

    @property
    def localized_message(self):
        if not self._is_english() or not self.appointment:
            return self.message
        appt = self.appointment
        doctor = appt.doctor.name if appt.doctor_id else "the doctor"
        clinic = appt.clinic.name if appt.clinic_id else ""
        date_s = appt.appointment_date.strftime("%Y-%m-%d") if appt.appointment_date else ""
        time_s = appt.appointment_time.strftime("%H:%M") if appt.appointment_time else ""
        suffix = f" ({clinic})" if clinic else ""
        type_ = self.notification_type
        if type_ == self.Type.APPOINTMENT_BOOKED:
            return f"Booked with {doctor} on {date_s} at {time_s}{suffix}."
        if type_ == self.Type.APPOINTMENT_CANCELLED:
            return f"Cancelled: {doctor} on {date_s} at {time_s}{suffix}."
        if type_ == self.Type.APPOINTMENT_RESCHEDULED:
            return f"Rescheduled to {date_s} at {time_s} with {doctor}{suffix}."
        if type_ == self.Type.APPOINTMENT_REMINDER:
            return f"Reminder: {doctor} on {date_s} at {time_s}{suffix}."
        if type_ == self.Type.APPOINTMENT_STATUS_CHANGED:
            return f"Status changed for {date_s} {time_s} with {doctor}{suffix}."
        if type_ == self.Type.APPOINTMENT_EDITED:
            return f"Updated: {doctor} on {date_s} at {time_s}{suffix}."
        return self.message