from django.contrib import admin
from .models import Appointment, AppointmentAnswer, AppointmentAttachment, AppointmentType


@admin.register(AppointmentType)
class AppointmentTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "name_ar", "doctor", "clinic", "duration_minutes", "price", "is_active"]
    list_filter = ["is_active", "clinic", "doctor"]
    search_fields = ["name", "name_ar", "doctor__name", "clinic__name"]
    list_editable = ["name_ar", "is_active"]
    ordering = ["doctor", "name"]

    fieldsets = (
        (None, {"fields": ("doctor", "clinic")}),
        ("Details", {"fields": ("name", "name_ar", "duration_minutes", "price", "description", "is_active")}),
    )


class AppointmentAnswerInline(admin.TabularInline):
    model = AppointmentAnswer
    extra = 0
    readonly_fields = ["question", "answer_text", "created_at"]
    can_delete = False


class AppointmentAttachmentInline(admin.TabularInline):
    model = AppointmentAttachment
    extra = 0
    readonly_fields = ["question", "original_name", "file_size", "mime_type", "uploaded_at", "uploaded_by"]
    can_delete = False


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "patient",
        "doctor",
        "clinic",
        "appointment_type",
        "appointment_date",
        "appointment_time",
        "status",
        "created_at",
    ]
    list_filter = ["status", "clinic", "appointment_date"]
    search_fields = ["patient__name", "doctor__name", "clinic__name"]
    raw_id_fields = ["patient", "doctor", "clinic", "appointment_type", "created_by"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "appointment_date"
    inlines = [AppointmentAnswerInline, AppointmentAttachmentInline]


@admin.register(AppointmentAnswer)
class AppointmentAnswerAdmin(admin.ModelAdmin):
    list_display = ["appointment", "question", "answer_text_short", "created_at"]
    list_filter = ["appointment__clinic"]
    search_fields = ["answer_text", "appointment__patient__name"]
    raw_id_fields = ["appointment", "question"]

    def answer_text_short(self, obj):
        return obj.answer_text[:80] if obj.answer_text else "—"
    answer_text_short.short_description = "الإجابة"


@admin.register(AppointmentAttachment)
class AppointmentAttachmentAdmin(admin.ModelAdmin):
    list_display = ["original_name", "appointment", "file_size", "mime_type", "uploaded_at"]
    list_filter = ["mime_type"]
    search_fields = ["original_name", "appointment__patient__name"]
    raw_id_fields = ["appointment", "question", "uploaded_by"]