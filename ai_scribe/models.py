"""
AI-scribe data model.

Roles:
  • Platform admin  → manages the global ``AIModel`` registry (Django admin):
    which models exist, their friendly names, and which are "free".
  • Clinic owner    → per (clinic, doctor): a monthly USD cap + which models that
    doctor may use (``DoctorClinicAIConfig``).
  • Doctor          → picks their active model from the owner-allowed set.

Spend is metered in ``DoctorMonthlySpend`` (atomic, calendar-month) and every
call is audited in ``AIUsageRecord``. The OpenRouter API key is NEVER stored
here — it lives only in settings/env. No transcript or note content (PHI) is
ever persisted by this app.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


def current_period():
    """Calendar-month key ``'YYYY-MM'`` in the project's local timezone.

    Used as the budget reset boundary (resets on the 1st of each month).
    """
    return timezone.localdate().strftime("%Y-%m")


class AIModel(models.Model):
    """A model exposed to doctors, mapped to an OpenRouter model id.

    Managed by the platform admin via Django admin. ``display_name`` is what
    doctors/owners see (e.g. "Free", "Smart"); ``openrouter_model_id`` is the
    OpenRouter slug actually called.
    """

    display_name = models.CharField(
        max_length=100,
        help_text="Name shown to doctors/owners, e.g. 'Free' or 'Smart'.",
    )
    openrouter_model_id = models.CharField(
        max_length=200,
        help_text=(
            "OpenRouter model slug, e.g. 'anthropic/claude-sonnet-4.6' or a "
            "no-cost model like 'meta-llama/llama-3.3-70b-instruct:free'."
        ),
    )
    is_free = models.BooleanField(
        default=False,
        help_text=(
            "If checked, using this model does NOT consume a doctor's monthly "
            "budget. Pair it with an actually-free OpenRouter model (…:free) so "
            "it also costs you nothing."
        ),
    )
    is_active = models.BooleanField(
        default=True, help_text="Inactive models are hidden everywhere."
    )
    sort_order = models.PositiveIntegerField(default=0)
    description = models.CharField(max_length=255, blank=True, default="")

    # Optional price hints (USD per 1M tokens) — for display/estimation only;
    # the authoritative per-call cost comes from OpenRouter's response.
    input_price_per_mtok = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    output_price_per_mtok = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Model"
        verbose_name_plural = "AI Models"
        ordering = ["sort_order", "display_name"]

    def __str__(self):
        tag = " (free)" if self.is_free else ""
        return f"{self.display_name}{tag} [{self.openrouter_model_id}]"


class DoctorClinicAIConfig(models.Model):
    """Per (clinic, doctor) AI-scribe configuration, owned by the clinic owner."""

    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="ai_doctor_configs"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_clinic_configs",
        limit_choices_to={"role__in": ["DOCTOR", "MAIN_DOCTOR"]},
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Master on/off for AI scribe for this doctor at this clinic.",
    )
    monthly_limit_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Monthly USD cap for paid models (resets on the 1st). The Free model is never capped.",
    )
    allowed_models = models.ManyToManyField(
        AIModel, blank=True, related_name="allowed_for_configs",
        help_text="Models this doctor may choose from.",
    )
    selected_model = models.ForeignKey(
        AIModel,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="selected_by_configs",
        help_text="The doctor's currently-chosen model (must be within allowed_models).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="ai_configs_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctor Clinic AI Config"
        verbose_name_plural = "Doctor Clinic AI Configs"
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "doctor"], name="unique_ai_config_per_doctor_clinic"
            )
        ]

    def __str__(self):
        return f"AI config: doctor={self.doctor_id} clinic={self.clinic_id}"


class DoctorMonthlySpend(models.Model):
    """Authoritative running spend for (clinic, doctor, calendar month).

    Updated atomically under ``select_for_update`` so concurrent drafts can't
    push a doctor over their cap.
    """

    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="ai_monthly_spend"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_monthly_spend"
    )
    period = models.CharField(max_length=7, help_text="Calendar month, 'YYYY-MM'.")
    spent_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctor Monthly AI Spend"
        verbose_name_plural = "Doctor Monthly AI Spend"
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "doctor", "period"],
                name="unique_ai_spend_per_doctor_clinic_period",
            )
        ]
        indexes = [models.Index(fields=["clinic", "doctor", "period"])]

    def __str__(self):
        return f"{self.doctor_id}@{self.clinic_id} {self.period}: ${self.spent_usd}"


class AIUsageRecord(models.Model):
    """Per-call audit log — metadata + cost only. NEVER stores transcript/note text."""

    class Status(models.TextChoices):
        OK = "OK", "OK"
        ERROR = "ERROR", "Error"

    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.CASCADE, related_name="ai_usage"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_usage"
    )
    model = models.ForeignKey(
        AIModel, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage"
    )
    model_label = models.CharField(max_length=100, blank=True, default="")
    openrouter_model_id = models.CharField(max_length=200, blank=True, default="")
    period = models.CharField(max_length=7)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    was_free = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OK)
    error = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "AI Usage Record"
        verbose_name_plural = "AI Usage Records"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["clinic", "doctor", "period"])]

    def __str__(self):
        return f"{self.model_label} ${self.cost_usd} ({self.created_at:%Y-%m-%d})"
