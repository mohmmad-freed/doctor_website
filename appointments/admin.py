from django.contrib import admin
from .models import Appointment, AppointmentType


@admin.register(AppointmentType)
class AppointmentTypeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "name_ar",
        "doctor",
        "clinic",
        "duration_minutes",
        "price",
        "is_active",
        "created_at",
    ]
    list_filter = ["clinic", "is_active", "doctor"]
    search_fields = ["name", "name_ar", "doctor__name", "clinic__name"]
    list_editable = ["name_ar", "is_active", "price", "duration_minutes"]
    ordering = ["clinic", "doctor", "name"]

    fieldsets = (
        (None, {
            "fields": ("doctor", "clinic", "name", "name_ar"),
        }),
        ("Details", {
            "fields": ("duration_minutes", "price", "description", "is_active"),
        }),
    )


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = [
        "patient",
        "doctor",
        "clinic",
        "appointment_type",
        "appointment_date",
        "appointment_time",
        "status",
    ]
    list_filter = ["clinic", "status", "appointment_date"]
    search_fields = [
        "patient__name",
        "patient__phone",
        "doctor__name",
        "clinic__name",
    ]
    ordering = ["-appointment_date", "-appointment_time"]
    raw_id_fields = ["patient", "doctor", "created_by"]