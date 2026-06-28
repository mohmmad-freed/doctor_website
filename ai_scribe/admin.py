from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from .models import AIModel, DoctorClinicAIConfig, DoctorMonthlySpend, AIUsageRecord


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = (
        "display_name", "openrouter_model_id", "is_free", "is_active",
        "sort_order", "input_price_per_mtok", "output_price_per_mtok",
    )
    list_editable = ("is_free", "is_active", "sort_order")
    list_filter = ("is_active", "is_free")
    search_fields = ("display_name", "openrouter_model_id")
    ordering = ("sort_order", "display_name")
    change_list_template = "admin/ai_scribe/aimodel_change_list.html"

    def get_urls(self):
        custom = [
            path(
                "stt-test/",
                self.admin_site.admin_view(self.stt_test_view),
                name="ai_scribe_stt_test",
            ),
        ]
        return custom + super().get_urls()

    def stt_test_view(self, request):
        """Admin-only diagnostic: upload audio → run the live STT pipeline →
        show transcript + duration + cost. Not charged to any doctor's budget."""
        from ai_scribe import services

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Test transcription",
            "opts": self.model._meta,
            "provider": getattr(settings, "STT_PROVIDER", "openrouter"),
            "model_id": getattr(settings, "STT_MODEL", ""),
            "configured": services.stt_configured(),
        }
        if request.method == "POST":
            audio = request.FILES.get("audio")
            if not audio:
                ctx["error"] = "Choose an audio file first."
            else:
                try:
                    text, cost, duration = services.transcribe_preview(
                        audio, audio.name, audio.content_type,
                        language=(request.POST.get("language") or None),
                    )
                    ctx["result"] = {
                        "text": text, "cost": cost, "duration": duration, "filename": audio.name,
                    }
                except services.AIScribeError as exc:
                    ctx["error"] = str(exc)
                except Exception as exc:  # surface unexpected errors in the diagnostic
                    ctx["error"] = f"Unexpected error: {exc}"
        return TemplateResponse(request, "admin/ai_scribe/stt_test.html", ctx)


@admin.register(DoctorClinicAIConfig)
class DoctorClinicAIConfigAdmin(admin.ModelAdmin):
    list_display = ("doctor", "clinic", "is_enabled", "monthly_limit_usd", "selected_model")
    list_filter = ("is_enabled", "clinic")
    search_fields = ("doctor__name", "doctor__phone", "clinic__name")
    autocomplete_fields = ()
    filter_horizontal = ("allowed_models",)
    raw_id_fields = ("doctor", "clinic", "created_by")


@admin.register(DoctorMonthlySpend)
class DoctorMonthlySpendAdmin(admin.ModelAdmin):
    list_display = ("doctor", "clinic", "period", "spent_usd", "updated_at")
    list_filter = ("period", "clinic")
    search_fields = ("doctor__name", "doctor__phone", "clinic__name")
    readonly_fields = ("spent_usd", "updated_at")


@admin.register(AIUsageRecord)
class AIUsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "doctor", "clinic", "model_label", "was_free",
        "input_tokens", "output_tokens", "cost_usd", "status",
    )
    list_filter = ("status", "was_free", "period", "clinic")
    search_fields = ("doctor__name", "doctor__phone", "model_label", "openrouter_model_id")
    readonly_fields = [f.name for f in AIUsageRecord._meta.fields]

    def has_add_permission(self, request):
        return False
