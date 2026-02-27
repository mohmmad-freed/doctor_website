from django.contrib import admin
from .models import Clinic, ClinicStaff, ClinicActivationCode


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ['name', 'main_doctor', 'phone', 'email', 'status', 'is_active', 'created_at']
    list_filter = ['status', 'is_active', 'created_at']
    search_fields = ['name', 'main_doctor__name', 'main_doctor__email']
    readonly_fields = ['created_at']
    filter_horizontal = ['specialties']

    fieldsets = (
        ('Clinic Information', {
            'fields': ('name', 'address', 'phone', 'email', 'description', 'specialties')
        }),
        ('Management', {
            'fields': ('main_doctor', 'status', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )


@admin.register(ClinicStaff)
class ClinicStaffAdmin(admin.ModelAdmin):
    list_display = ['user', 'clinic', 'role', 'added_by', 'is_active', 'added_at']
    list_filter = ['role', 'is_active', 'clinic', 'added_at']
    search_fields = ['user__name', 'user__email', 'clinic__name']
    readonly_fields = ['added_at']

    fieldsets = (
        ('Staff Information', {
            'fields': ('clinic', 'user', 'role', 'is_active')
        }),
        ('Metadata', {
            'fields': ('added_by', 'added_at')
        }),
    )


@admin.register(ClinicActivationCode)
class ClinicActivationCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'clinic_name', 'phone', 'national_id', 'expires_at', 'is_used', 'used_by', 'created_at', 'used_at']
    list_filter = ['is_used', 'created_at']
    search_fields = ['code', 'clinic_name', 'phone', 'national_id', 'used_by__name']
    readonly_fields = ['is_used', 'used_by', 'used_at', 'created_at']

    fieldsets = (
        ('Code Details', {
            'fields': ('code', 'clinic_name', 'expires_at')
        }),
        ('Intended Owner', {
            'fields': ('phone', 'national_id'),
            'description': 'Phone and national ID of the intended clinic owner. These must match what the user enters during signup.',
        }),
        ('Usage', {
            'fields': ('is_used', 'used_by', 'used_at', 'created_at'),
        }),
    )
