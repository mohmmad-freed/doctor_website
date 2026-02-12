from django.contrib import admin
from .models import DoctorAvailability, Specialty, DoctorProfile, DoctorSpecialty


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
    list_display = [
        "user",
        "get_primary_specialty",
        "years_of_experience",
    ]
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