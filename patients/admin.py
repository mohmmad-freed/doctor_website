from django.contrib import admin
from .models import (
    PatientProfile, ClinicPatient,
    ClinicalNote, Order, Prescription, PrescriptionItem, MedicalRecord,
)


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


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ['patient', 'doctor', 'clinic', 'created_at']
    list_filter = ['clinic']
    search_fields = ['patient__name', 'doctor__name']
    raw_id_fields = ['patient', 'doctor', 'clinic', 'appointment']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['title', 'order_type', 'status', 'patient', 'doctor', 'clinic', 'created_at']
    list_filter = ['order_type', 'status', 'clinic']
    search_fields = ['title', 'patient__name', 'doctor__name']
    raw_id_fields = ['patient', 'doctor', 'clinic', 'appointment']
    readonly_fields = ['created_at', 'updated_at']


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ['patient', 'doctor', 'clinic', 'created_at']
    list_filter = ['clinic']
    search_fields = ['patient__name', 'doctor__name']
    raw_id_fields = ['patient', 'doctor', 'clinic', 'appointment']
    readonly_fields = ['created_at']
    inlines = [PrescriptionItemInline]


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'patient', 'clinic', 'uploaded_by', 'uploaded_at']
    list_filter = ['category', 'clinic']
    search_fields = ['title', 'patient__name']
    raw_id_fields = ['patient', 'clinic', 'uploaded_by']
    readonly_fields = ['uploaded_at', 'original_name', 'file_size']