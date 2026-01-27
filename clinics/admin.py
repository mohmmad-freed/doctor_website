from django.contrib import admin
from .models import Clinic, ClinicStaff, ClinicActivationCode


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ['name', 'main_doctor', 'phone', 'email', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'main_doctor__name', 'main_doctor__email']
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Clinic Information', {
            'fields': ('name', 'address', 'phone', 'email', 'description')
        }),
        ('Management', {
            'fields': ('main_doctor', 'is_active')
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
    list_display = ['code', 'clinic_name', 'is_used', 'used_by', 'created_at', 'used_at']
    list_filter = ['is_used', 'created_at']
    search_fields = ['code', 'clinic_name', 'used_by__email']
    readonly_fields = ['is_used', 'used_by', 'used_at', 'created_at']
    
    fields = ['code', 'clinic_name', 'is_used', 'used_by', 'used_at', 'created_at']