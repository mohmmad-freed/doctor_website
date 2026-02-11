from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from clinics.models import Clinic


class DoctorAvailability(models.Model):
    """
    Recurring weekly availability schedule for a doctor at a specific clinic.

    A doctor can work at multiple clinics on different days/times,
    but overlapping time slots across clinics are not allowed (R-04).

    day_of_week uses Python's weekday() convention:
        0 = Monday, 1 = Tuesday, ..., 6 = Sunday
    """

    DAY_CHOICES = [
        (0, "الاثنين"),       # Monday
        (1, "الثلاثاء"),      # Tuesday
        (2, "الأربعاء"),      # Wednesday
        (3, "الخميس"),        # Thursday
        (4, "الجمعة"),        # Friday
        (5, "السبت"),         # Saturday
        (6, "الأحد"),         # Sunday
    ]

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_slots",
        help_text="The doctor this availability belongs to.",
    )
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="doctor_availabilities",
        help_text="The clinic where this availability applies.",
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(
        default=True,
        help_text="Temporarily disable this slot without deleting it.",
    )

    class Meta:
        verbose_name = "Doctor Availability"
        verbose_name_plural = "Doctor Availabilities"
        ordering = ["day_of_week", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "clinic", "day_of_week", "start_time"],
                name="unique_doctor_clinic_day_start",
            )
        ]

    def __str__(self):
        day = self.get_day_of_week_display()
        return f"{self.doctor.name} - {day} ({self.start_time:%H:%M}-{self.end_time:%H:%M}) @ {self.clinic.name}"

    def clean(self):
        """
        Validate:
        1. start_time < end_time
        2. No overlapping availability for the same doctor on the same day,
           across ALL clinics (enforces R-04: global schedule non-overlap).
        """
        super().clean()

        # 1. Basic time validation
        if self.start_time and self.end_time:
            if self.start_time >= self.end_time:
                raise ValidationError(
                    {"end_time": "End time must be after start time."}
                )

        # 2. Cross-clinic overlap check
        if self.doctor_id and self.day_of_week is not None and self.start_time and self.end_time:
            overlapping = DoctorAvailability.objects.filter(
                doctor=self.doctor,
                day_of_week=self.day_of_week,
                is_active=True,
                # Time overlap condition:
                # existing.start < new.end AND existing.end > new.start
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )

            # Exclude self when updating an existing record
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)

            if overlapping.exists():
                conflict = overlapping.select_related("clinic").first()
                if conflict.clinic_id == self.clinic_id:
                    raise ValidationError(
                        f"This time overlaps with an existing slot on "
                        f"{self.get_day_of_week_display()}: "
                        f"{conflict.start_time:%H:%M}-{conflict.end_time:%H:%M} "
                        f"at this clinic."
                    )
                else:
                    raise ValidationError(
                        f"Time conflict with another clinic schedule: "
                        f"{conflict.clinic.name} on {self.get_day_of_week_display()} "
                        f"{conflict.start_time:%H:%M}-{conflict.end_time:%H:%M}."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)