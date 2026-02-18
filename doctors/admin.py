from django.contrib import admin
from .models import (
    DoctorAvailability,
    DoctorIntakeFormTemplate,
    DoctorIntakeQuestion,
    DoctorIntakeRule,
    DoctorProfile,
    DoctorSpecialty,
    Specialty,
)


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ["name_ar", "name", "description"]
    search_fields = ["name", "name_ar"]
    ordering = ["name_ar"]


class DoctorSpecialtyInline(admin.TabularInline):
    model = DoctorSpecialty
    extra = 1
    min_num = 0
    autocomplete_fields = ["specialty"]


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "get_primary_specialty", "years_of_experience"]
    list_filter = ["specialties"]
    search_fields = ["user__name", "user__phone"]
    raw_id_fields = ["user"]
    inlines = [DoctorSpecialtyInline]

    def get_primary_specialty(self, obj):
        ps = obj.primary_specialty
        return ps.name_ar if ps else "—"
    get_primary_specialty.short_description = "التخصص الرئيسي"


@admin.register(DoctorAvailability)
class DoctorAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["doctor", "clinic", "get_day_display", "start_time", "end_time", "is_active"]
    list_filter = ["clinic", "day_of_week", "is_active"]
    search_fields = ["doctor__name", "doctor__phone", "clinic__name"]
    list_editable = ["is_active"]
    ordering = ["doctor", "day_of_week", "start_time"]

    def get_day_display(self, obj):
        return obj.get_day_of_week_display()
    get_day_display.short_description = "اليوم"
    get_day_display.admin_order_field = "day_of_week"


# ─── Intake Forms Admin ──────────────────────────────────────────────────


class DoctorIntakeQuestionInline(admin.TabularInline):
    model = DoctorIntakeQuestion
    extra = 1
    min_num = 0
    fields = ["order", "question_text", "question_text_ar", "field_type", "is_required", "choices", "placeholder"]
    ordering = ["order"]


class DoctorIntakeRuleInline(admin.TabularInline):
    model = DoctorIntakeRule
    fk_name = "source_question"
    extra = 0
    fields = ["source_question", "operator", "expected_value", "action", "target_question"]


@admin.register(DoctorIntakeFormTemplate)
class DoctorIntakeFormTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "display_title",
        "doctor",
        "appointment_type",
        "question_count",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "doctor"]
    search_fields = ["title", "title_ar", "doctor__name"]
    list_editable = ["is_active"]
    raw_id_fields = ["doctor", "appointment_type"]
    inlines = [DoctorIntakeQuestionInline]

    fieldsets = (
        (None, {
            "fields": ("doctor", "appointment_type"),
        }),
        ("Form Details", {
            "fields": ("title", "title_ar", "description", "is_active"),
        }),
    )

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = "عدد الأسئلة"


@admin.register(DoctorIntakeQuestion)
class DoctorIntakeQuestionAdmin(admin.ModelAdmin):
    list_display = ["display_text", "template", "field_type", "is_required", "order"]
    list_filter = ["field_type", "is_required", "template__doctor"]
    search_fields = ["question_text", "question_text_ar"]
    ordering = ["template", "order"]
    inlines = [DoctorIntakeRuleInline]


@admin.register(DoctorIntakeRule)
class DoctorIntakeRuleAdmin(admin.ModelAdmin):
    list_display = ["__str__", "operator", "action"]
    list_filter = ["operator", "action"]
    raw_id_fields = ["source_question", "target_question"]