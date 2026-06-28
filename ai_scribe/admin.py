from django.contrib import admin

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
