from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from clinics.models import Clinic


class Specialty(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_ar = models.CharField(max_length=100, unique=True, help_text="Arabic name for display to patients.")
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Specialty"
        verbose_name_plural = "Specialties"
        ordering = ["name_ar"]

    def __str__(self):
        return f"{self.name_ar} ({self.name})"


class DoctorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor_profile",
        limit_choices_to={"role__in": ["DOCTOR", "MAIN_DOCTOR"]},
    )
    bio = models.TextField(blank=True, help_text="Public bio displayed on the booking page.")
    years_of_experience = models.PositiveIntegerField(null=True, blank=True)
    specialties = models.ManyToManyField(Specialty, through="DoctorSpecialty", related_name="doctors", blank=True)

    class Meta:
        verbose_name = "Doctor Profile"
        verbose_name_plural = "Doctor Profiles"

    def __str__(self):
        return f"Dr. {self.user.name}"

    @property
    def primary_specialty(self):
        through = self.doctor_specialties.filter(is_primary=True).select_related("specialty").first()
        return through.specialty if through else None

    @property
    def secondary_specialties(self):
        return Specialty.objects.filter(
            doctor_specialties__doctor_profile=self,
            doctor_specialties__is_primary=False,
        )


class DoctorSpecialty(models.Model):
    doctor_profile = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name="doctor_specialties")
    specialty = models.ForeignKey(Specialty, on_delete=models.CASCADE, related_name="doctor_specialties")
    is_primary = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Doctor Specialty"
        verbose_name_plural = "Doctor Specialties"
        constraints = [
            models.UniqueConstraint(fields=["doctor_profile", "specialty"], name="unique_doctor_specialty"),
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
    DAY_CHOICES = [
        (0, "الاثنين"), (1, "الثلاثاء"), (2, "الأربعاء"), (3, "الخميس"),
        (4, "الجمعة"), (5, "السبت"), (6, "الأحد"),
    ]

    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availability_slots")
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="doctor_availabilities")
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

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
        super().clean()
        if self.start_time and self.end_time:
            if self.start_time >= self.end_time:
                raise ValidationError({"end_time": "End time must be after start time."})

        if self.doctor_id and self.day_of_week is not None and self.start_time and self.end_time:
            overlapping = DoctorAvailability.objects.filter(
                doctor=self.doctor, day_of_week=self.day_of_week, is_active=True,
                start_time__lt=self.end_time, end_time__gt=self.start_time,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                conflict = overlapping.select_related("clinic").first()
                if conflict.clinic_id == self.clinic_id:
                    raise ValidationError(
                        f"This time overlaps with an existing slot on "
                        f"{self.get_day_of_week_display()}: "
                        f"{conflict.start_time:%H:%M}-{conflict.end_time:%H:%M} at this clinic."
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Intake Forms — per APPOINTMENT_BOOKING_WORKFLOW.md Section 6
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DoctorIntakeFormTemplate(models.Model):
    """
    Reusable intake form template that a doctor attaches to appointment types.

    - Linked to a doctor, optionally to a specific AppointmentType.
    - If appointment_type is NULL → applies to ALL of the doctor's types.
    - At most one active template per (doctor, appointment_type).
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="intake_form_templates",
        limit_choices_to={"role__in": ["DOCTOR", "MAIN_DOCTOR"]},
    )
    appointment_type = models.ForeignKey(
        "appointments.AppointmentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intake_form_templates",
        help_text="If set, form only appears for this type. NULL = all types.",
    )
    title = models.CharField(max_length=200)
    title_ar = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, help_text="Instructions displayed before the form.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Intake Form Template"
        verbose_name_plural = "Intake Form Templates"
        ordering = ["doctor", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "appointment_type"],
                condition=models.Q(is_active=True),
                name="unique_active_intake_per_doctor_type",
            ),
        ]

    def __str__(self):
        type_label = self.appointment_type.name if self.appointment_type else "All Types"
        return f"{self.display_title} - Dr. {self.doctor.name} ({type_label})"

    @property
    def display_title(self):
        return self.title_ar if self.title_ar else self.title

    @property
    def ordered_questions(self):
        return self.questions.all().order_by("order")


class DoctorIntakeQuestion(models.Model):
    """
    Individual question within an intake form template.
    Supports: TEXT, TEXTAREA, SELECT, MULTISELECT, CHECKBOX, DATE, FILE, DATED_FILES.
    """

    class FieldType(models.TextChoices):
        TEXT = "TEXT", "نص قصير"
        TEXTAREA = "TEXTAREA", "نص طويل"
        SELECT = "SELECT", "قائمة منسدلة"
        MULTISELECT = "MULTISELECT", "اختيار متعدد"
        CHECKBOX = "CHECKBOX", "مربع اختيار (نعم/لا)"
        DATE = "DATE", "تاريخ"
        FILE = "FILE", "ملف مرفق"
        DATED_FILES = "DATED_FILES", "ملفات مؤرخة"

    template = models.ForeignKey(
        DoctorIntakeFormTemplate,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    question_text = models.CharField(max_length=500, help_text="English question text.")
    question_text_ar = models.CharField(max_length=500, blank=True, default="", help_text="Arabic question text.")
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    choices = models.JSONField(blank=True, default=list, help_text='For SELECT/MULTISELECT: ["opt1", "opt2"].')
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text_content = models.TextField(blank=True, db_column="help_text_content")
    max_file_size_mb = models.PositiveIntegerField(null=True, blank=True)
    allowed_extensions = models.JSONField(blank=True, default=list, help_text='e.g. ["pdf", "jpg", "png"].')

    class Meta:
        verbose_name = "Intake Question"
        verbose_name_plural = "Intake Questions"
        ordering = ["template", "order"]
        constraints = [
            models.UniqueConstraint(fields=["template", "order"], name="unique_question_order_per_template"),
        ]

    def __str__(self):
        req = " *" if self.is_required else ""
        return f"{self.display_text}{req} ({self.get_field_type_display()})"

    @property
    def display_text(self):
        return self.question_text_ar if self.question_text_ar else self.question_text

    def clean(self):
        super().clean()
        if self.field_type in (self.FieldType.SELECT, self.FieldType.MULTISELECT):
            if not self.choices or not isinstance(self.choices, list) or len(self.choices) < 2:
                raise ValidationError({"choices": "Choice fields must have at least 2 options."})


class DoctorIntakeRule(models.Model):
    """
    Conditional display logic: "Show question X only if question Y has answer Z."
    Both questions must belong to the same template.
    """

    class Operator(models.TextChoices):
        EQUALS = "EQUALS", "يساوي"
        NOT_EQUALS = "NOT_EQUALS", "لا يساوي"
        CONTAINS = "CONTAINS", "يحتوي"
        IN = "IN", "ضمن القائمة"

    class Action(models.TextChoices):
        SHOW = "SHOW", "أظهر"
        HIDE = "HIDE", "أخفِ"

    source_question = models.ForeignKey(
        DoctorIntakeQuestion, on_delete=models.CASCADE, related_name="rules_as_source",
    )
    expected_value = models.CharField(max_length=500)
    operator = models.CharField(max_length=20, choices=Operator.choices, default=Operator.EQUALS)
    target_question = models.ForeignKey(
        DoctorIntakeQuestion, on_delete=models.CASCADE, related_name="rules_as_target",
    )
    action = models.CharField(max_length=10, choices=Action.choices, default=Action.SHOW)

    class Meta:
        verbose_name = "Intake Rule"
        verbose_name_plural = "Intake Rules"
        constraints = [
            models.UniqueConstraint(
                fields=["source_question", "target_question", "expected_value"],
                name="unique_intake_rule",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_question=models.F("target_question")),
                name="intake_rule_no_self_reference",
            ),
        ]

    def __str__(self):
        return f"If Q{self.source_question_id} {self.operator} '{self.expected_value}' → {self.action} Q{self.target_question_id}"

    def clean(self):
        super().clean()
        if (self.source_question_id and self.target_question_id
                and self.source_question.template_id != self.target_question.template_id):
            raise ValidationError("Source and target questions must belong to the same template.")


# ─── Legacy models (kept for migration, no longer used in views) ─────────


class DoctorForm(models.Model):
    """LEGACY: Replaced by DoctorIntakeFormTemplate."""
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="intake_forms",
                               limit_choices_to={"role__in": ["DOCTOR", "MAIN_DOCTOR"]})
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="intake_forms")
    title = models.CharField(max_length=200)
    title_ar = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctor Intake Form (Legacy)"
        verbose_name_plural = "Doctor Intake Forms (Legacy)"
        constraints = [
            models.UniqueConstraint(fields=["doctor", "clinic"], condition=models.Q(is_active=True),
                                    name="unique_active_form_per_doctor_clinic"),
        ]

    def __str__(self):
        return f"[Legacy] {self.title}"

    @property
    def display_title(self):
        return self.title_ar if self.title_ar else self.title

    @property
    def ordered_fields(self):
        return self.fields.filter(is_active=True).order_by("order")


class FormField(models.Model):
    """LEGACY: Replaced by DoctorIntakeQuestion."""
    class FieldType(models.TextChoices):
        TEXT = "TEXT", "نص قصير"
        TEXTAREA = "TEXTAREA", "نص طويل"
        NUMBER = "NUMBER", "رقم"
        SINGLE_CHOICE = "SINGLE_CHOICE", "اختيار واحد"
        MULTI_CHOICE = "MULTI_CHOICE", "اختيار متعدد"
        BOOLEAN = "BOOLEAN", "نعم / لا"

    form = models.ForeignKey(DoctorForm, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=300)
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    is_required = models.BooleanField(default=False)
    choices = models.JSONField(blank=True, null=True)
    placeholder = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Form Field (Legacy)"
        verbose_name_plural = "Form Fields (Legacy)"
        ordering = ["form", "order"]

    def __str__(self):
        return f"[Legacy] {self.label}"