from django.utils import timezone
from compliance.models import PatientClinicCompliance, ComplianceEvent, ClinicComplianceSettings
from clinics.models import Clinic
from patients.models import PatientProfile
from appointments.models import Appointment
from django.db import transaction

def get_or_create_compliance(clinic: Clinic, patient: PatientProfile) -> PatientClinicCompliance:
    """
    Retrieves or creates the compliance record for a patient in a specific clinic.
    """
    compliance, created = PatientClinicCompliance.objects.get_or_create(
        clinic=clinic,
        patient=patient,
        defaults={
            'bad_score': 0,
            'status': 'OK'
        }
    )
    return compliance

def is_patient_blocked(clinic: Clinic, patient: PatientProfile) -> bool:
    """
    Returns True if the patient is blocked in the given clinic.
    """
    compliance = get_or_create_compliance(clinic, patient)
    return compliance.status == 'BLOCKED'

def get_global_compliance_warnings(patient: PatientProfile) -> list[Clinic]:
    """
    Returns a list of clinics where this patient is currently blocked.
    """
    blocked_compliances = PatientClinicCompliance.objects.filter(
        patient=patient,
        status='BLOCKED'
    ).select_related('clinic')
    return [comp.clinic for comp in blocked_compliances]

@transaction.atomic
def record_no_show(clinic: Clinic, patient: PatientProfile, appointment: Appointment = None) -> PatientClinicCompliance:
    """
    Records a NO_SHOW event for a patient, incrementing their bad score and checking for block thresholds.
    """
    # ── Idempotency Check ──
    if appointment and ComplianceEvent.objects.filter(appointment=appointment, event_type='NO_SHOW').exists():
        return get_or_create_compliance(clinic, patient)

    compliance = get_or_create_compliance(clinic, patient)
    
    # Get clinic settings, or defaults if not found
    try:
        settings = clinic.compliance_settings
    except ClinicComplianceSettings.DoesNotExist:
        # Create default settings if they don't exist
        settings = ClinicComplianceSettings.objects.create(clinic=clinic)

    # Don't increment if already at max score
    if compliance.bad_score < settings.max_score:
        increment = settings.score_increment_per_no_show
        
        # Ensure we don't exceed max_score
        new_score = min(compliance.bad_score + increment, settings.max_score)
        actual_increment = new_score - compliance.bad_score
        
        compliance.bad_score = new_score
        compliance.last_violation_at = timezone.now()
        
        # Check against threshold
        if compliance.bad_score >= settings.score_threshold_block:
            compliance.status = 'BLOCKED'
            compliance.blocked_at = timezone.now()
        elif compliance.bad_score > 0:
            compliance.status = 'WARNED'

        compliance.save()

        # Create event log
        ComplianceEvent.objects.create(
            clinic=clinic,
            patient=patient,
            event_type='NO_SHOW',
            score_change=actual_increment,
            appointment=appointment
        )
        
    return compliance

@transaction.atomic
def apply_manual_waiver(clinic: Clinic, patient: PatientProfile, staff_user=None) -> PatientClinicCompliance:
    """
    Resets a patient's compliance score to 0 and their status to OK.
    Uses atomic transaction to guarantee data integrity.
    """
    compliance = get_or_create_compliance(clinic, patient)
    
    old_score = compliance.bad_score
    if old_score > 0:
        compliance.bad_score = 0
        compliance.status = 'OK'
        compliance.blocked_at = None
        compliance.last_forgiven_at = timezone.now()
        compliance.save()
        
        # Create event log
        # In the future, we could record which staff_user performed this if we add a user FK to ComplianceEvent
        ComplianceEvent.objects.create(
            clinic=clinic,
            patient=patient,
            event_type='MANUAL_WAIVER',
            score_change=-old_score,
            appointment=None
        )
        
    return compliance

from datetime import timedelta

def process_appointment_no_show(appointment: Appointment):
    """
    Checks if an appointment qualifies as a no-show and triggers the record_no_show logic.
    Rules:
    - status != CANCELLED
    - current_time > appointment end_time + grace_period
    - Idempotent
    """
    if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW, Appointment.Status.COMPLETED):
        return
    
    grace_period_minutes = 60 # Configurable if needed
    
    # Calculate end datetime
    from datetime import datetime
    end_dt = datetime.combine(appointment.appointment_date, appointment.appointment_time)
    
    # Approximation - we add duration if available, else standard
    if appointment.appointment_type:
        end_dt += timedelta(minutes=appointment.appointment_type.duration_minutes)
    else:
        end_dt += timedelta(minutes=30)
        
    end_with_grace = end_dt + timedelta(minutes=grace_period_minutes)
    
    # Convert local naive to timezone aware or compare naive
    local_now = timezone.localtime(timezone.now()).replace(tzinfo=None)
    
    if local_now > end_with_grace:
        # Mark as NO_SHOW
        appointment.status = Appointment.Status.NO_SHOW
        appointment.save(update_fields=['status', 'updated_at'])
        
        # Trigger compliance penalty
        try:
            patient_profile = appointment.patient.patient_profile
            record_no_show(appointment.clinic, patient_profile, appointment)
        except Exception as e:
            # Profile might not exist, skip penalty
            pass

@transaction.atomic
def run_auto_forgiveness():
    """
    Loops through all clinics and applies auto_forgiveness to patients according to clinic settings.
    """
    clinics_with_auto_forgive = ClinicComplianceSettings.objects.filter(auto_forgive_enabled=True, auto_forgive_after_days__isnull=False)
    
    now = timezone.now()
    
    for settings in clinics_with_auto_forgive:
        threshold_date = now - timezone.timedelta(days=settings.auto_forgive_after_days)
        
        # Find compliant users who have a bad score, are not blocked (or maybe blocked too, but usually forgiveness is applied to warn status),
        # but the prompt didn't specify excluding blocks. Let's apply to anyone with a bad score.
        compliances_to_forgive = PatientClinicCompliance.objects.filter(
            clinic=settings.clinic,
            bad_score__gt=0,
            last_violation_at__lte=threshold_date
        )
        
        for compliance in compliances_to_forgive:
            old_score = compliance.bad_score
            compliance.bad_score = 0
            compliance.status = 'OK'
            compliance.blocked_at = None
            compliance.last_forgiven_at = now
            compliance.save()
            
            ComplianceEvent.objects.create(
                clinic=settings.clinic,
                patient=compliance.patient,
                event_type='AUTO_FORGIVENESS',
                score_change=-old_score,
                appointment=None
            )
