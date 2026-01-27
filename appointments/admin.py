from django.contrib import admin
from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ['patient', 'clinic', 'doctor', 'appointment_date', 'appointment_time', 'status']
    list_filter = ['status', 'appointment_date', 'clinic']
    search_fields = ['patient__name', 'patient__email', 'clinic__name', 'doctor__name']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Appointment Information', {
            'fields': ('patient', 'clinic', 'doctor', 'appointment_date', 'appointment_time')
        }),
        ('Details', {
            'fields': ('status', 'reason', 'notes')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )