from django.contrib import admin
from .models import DoctorAvailability


@admin.register(DoctorAvailability)
class DoctorAvailabilityAdmin(admin.ModelAdmin):
    list_display = [
        "doctor",
        "clinic",
        "get_day_display",
        "start_time",
        "end_time",
        "is_active",
    ]
    list_filter = ["clinic", "day_of_week", "is_active"]
    search_fields = ["doctor__name", "doctor__phone", "clinic__name"]
    list_editable = ["is_active"]
    ordering = ["doctor", "day_of_week", "start_time"]

    def get_day_display(self, obj):
        return obj.get_day_of_week_display()

    get_day_display.short_description = "اليوم"
    get_day_display.admin_order_field = "day_of_week"