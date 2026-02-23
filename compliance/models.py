import uuid
from django.db import models
from clinics.models import Clinic
from patients.models import PatientProfile
from appointments.models import Appointment

class PatientClinicCompliance(models.Model):
    STATUS_CHOICES = [
        ('OK', 'OK'),
        ('WARNED', 'WARNED'),
        ('BLOCKED', 'BLOCKED'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='compliances', db_index=True)
    patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='clinic_compliances', db_index=True)
    
    bad_score = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OK')
    
    last_violation_at = models.DateTimeField(null=True, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    last_forgiven_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('clinic', 'patient')
        verbose_name_plural = 'Patient Clinic Compliances'

    def __str__(self):
        return f"{self.patient} at {self.clinic} - {self.status} (Score: {self.bad_score})"


class ComplianceEvent(models.Model):
    EVENT_TYPE_CHOICES = [
        ('NO_SHOW', 'No Show'),
        ('MANUAL_WAIVER', 'Manual Waiver'),
        ('AUTO_FORGIVENESS', 'Auto Forgiveness'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='compliance_events', db_index=True)
    patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='compliance_events', db_index=True)
    
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    score_change = models.IntegerField(help_text="Change in bad_score. Can be positive (penalty) or negative (forgiveness).")
    
    appointment = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name='compliance_events')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_event_type_display()} for {self.patient} at {self.clinic} ({self.score_change})"


class ClinicComplianceSettings(models.Model):
    clinic = models.OneToOneField(Clinic, on_delete=models.CASCADE, related_name='compliance_settings', primary_key=True)
    
    score_increment_per_no_show = models.PositiveIntegerField(default=1)
    score_threshold_block = models.PositiveIntegerField(default=3, help_text="Score at which patient is blocked.")
    max_score = models.PositiveIntegerField(default=5)
    
    auto_forgive_enabled = models.BooleanField(default=False)
    auto_forgive_after_days = models.PositiveIntegerField(null=True, blank=True, help_text="Days without violation to auto-forgive.")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Clinic Compliance Settings'

    def __str__(self):
        return f"Compliance Settings for {self.clinic.name}"
