from django.contrib import admin
from .models import PatientProfile, ClinicPatient


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'date_of_birth', 'gender', 'blood_type']
    list_filter = ['gender', 'blood_type']
    search_fields = ['user__name', 'user__email']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Personal Information', {
            'fields': ('date_of_birth', 'gender', 'blood_type')
        }),
        ('Medical Information', {
            'fields': ('medical_history', 'allergies')
        }),
        ('Emergency Contact', {
            'fields': ('emergency_contact_name', 'emergency_contact_phone')
        }),
    )


@admin.register(ClinicPatient)
class ClinicPatientAdmin(admin.ModelAdmin):
    list_display = ['patient', 'clinic', 'registered_by', 'registered_at']
    list_filter = ['clinic']
    search_fields = ['patient__name', 'patient__phone', 'clinic__name']
    raw_id_fields = ['patient', 'clinic', 'registered_by']
    readonly_fields = ['registered_at']