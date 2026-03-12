from django.contrib import admin
from compliance.models import PatientClinicCompliance, ComplianceEvent, ClinicComplianceSettings

@admin.register(PatientClinicCompliance)
class PatientClinicComplianceAdmin(admin.ModelAdmin):
    list_display = ('patient', 'clinic', 'bad_score', 'status', 'last_violation_at', 'blocked_at')
    list_filter = ('status', 'clinic')
    search_fields = ('patient__user__name', 'patient__user__phone', 'clinic__name')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(ComplianceEvent)
class ComplianceEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'patient', 'clinic', 'score_change', 'created_at')
    list_filter = ('event_type', 'clinic')
    search_fields = ('patient__user__name', 'clinic__name')
    readonly_fields = ('created_at',)

@admin.register(ClinicComplianceSettings)
class ClinicComplianceSettingsAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'score_increment_per_no_show', 'score_threshold_block', 'auto_forgive_enabled')
    search_fields = ('clinic__name',)
    readonly_fields = ('created_at', 'updated_at')
