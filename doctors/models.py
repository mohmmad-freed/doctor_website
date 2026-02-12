from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from clinics.models import Clinic


class Specialty(models.Model):
    """
    Medical specialties (e.g. Cardiology, Dermatology, Pediatrics).
    Used to categorize doctors for patient discovery.
    """

    name = models.CharField(max_length=100, unique=True)
    name_ar = models.CharField(
        max_length=100,
        unique=True,
        help_text="Arabic name for display to patients.",
    )
    description = models.TextField(
        blank=True,
        help_text="Brief description of this specialty.",
    )

    class Meta:
        verbose_name = "Specialty"
        verbose_name_plural = "Specialties"
        ordering = ["name_ar"]

    def __str__(self):
        return f"{self.name_ar} ({self.name})"


class DoctorProfile(models.Model):
    """
    Extended profile for doctor users (DOCTOR / MAIN_DOCTOR roles).

    Follows the same pattern as PatientProfile:
        CustomUser (auth/identity) ← OneToOne → DoctorProfile (domain data)

    Created manually by admin or doctor — NOT auto-created on registration.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor_profile",
        limit_choices_to={"role__in": ["DOCTOR", "MAIN_DOCTOR"]},
    )
    bio = models.TextField(
        blank=True,
        help_text="Public bio displayed on the booking page.",
    )
    years_of_experience = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of years of medical practice.",
    )
    specialties = models.ManyToManyField(
        Specialty,
        through="DoctorSpecialty",
        related_name="doctors",
        blank=True,
    )

    class Meta:
        verbose_name = "Doctor Profile"
        verbose_name_plural = "Doctor Profiles"

    def __str__(self):
        return f"Dr. {self.user.name}"

    @property
    def primary_specialty(self):
        """Returns the doctor's primary specialty, or None."""
        through = self.doctor_specialties.filter(is_primary=True).select_related("specialty").first()
        return through.specialty if through else None

    @property
    def secondary_specialties(self):
        """Returns queryset of secondary specialties."""
        return Specialty.objects.filter(
            doctor_specialties__doctor_profile=self,
            doctor_specialties__is_primary=False,
        )


class DoctorSpecialty(models.Model):
    """
    Through table for DoctorProfile ↔ Specialty M2M.
    Tracks which specialty is the primary one for each doctor.

    Constraints:
    - Each doctor must have exactly one primary specialty (enforced in clean()).
    - A doctor cannot have the same specialty twice.
    """

    doctor_profile = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name="doctor_specialties",
    )
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.CASCADE,
        related_name="doctor_specialties",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Is this the doctor's primary specialty?",
    )

    class Meta:
        verbose_name = "Doctor Specialty"
        verbose_name_plural = "Doctor Specialties"
        constraints = [
            models.UniqueConstraint(
                fields=["doctor_profile", "specialty"],
                name="unique_doctor_specialty",
            ),
            # Only one primary per doctor
            models.UniqueConstraint(
                fields=["doctor_profile"],
                condition=models.Q(is_primary=True),
                name="unique_primary_specialty_per_doctor",
            ),
        ]

    def __str__(self):
        label = "Primary" if self.is_primary else "Secondary"
        return f"{self.doctor_profile.user.name} → {self.specialty.name_ar} ({label})"


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
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )

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