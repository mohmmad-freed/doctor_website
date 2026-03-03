from django.db import transaction
from django.utils import timezone

from .models import Clinic, ClinicStaff, ClinicSubscription, ClinicVerification


@transaction.atomic
def create_clinic_for_main_doctor(user, cleaned_data, activation_code_obj, owner_verified_at=None):
    """
    Atomically create a clinic and wire up all related records:

    1. Create the Clinic row.
    2. Set specialties (M2M).
    3. Create ClinicStaff(role=MAIN_DOCTOR) linking the owner.
    4. Create ClinicSubscription seeded from the activation code.
    5. Create ClinicVerification (channels pre-stamped if owner_verified_at given).
    6. Mark the ClinicActivationCode as used.

    owner_verified_at: if provided, owner phone + email are already verified
    (new 3-stage wizard flow) — clinic is created ACTIVE and verification
    timestamps are set to this value. If None (old single-page flow), clinic
    status is PENDING and verification timestamps remain null.

    Returns the newly created Clinic instance.
    """
    if activation_code_obj.is_used:
        from django.core.exceptions import ValidationError
        raise ValidationError("This activation code has already been used and cannot be reused.")

    status = "ACTIVE" if owner_verified_at else "PENDING"
    clinic = Clinic.objects.create(
        name=cleaned_data["clinic_name"],
        address=cleaned_data["clinic_address"],
        city=cleaned_data["clinic_city"],
        phone=cleaned_data.get("clinic_phone", ""),
        email=cleaned_data.get("clinic_email") or "",
        description=cleaned_data.get("clinic_description", ""),
        status=status,
        main_doctor=user,
    )
    clinic.specialties.set(cleaned_data["specialties"])

    ClinicStaff.objects.create(
        clinic=clinic,
        user=user,
        role="MAIN_DOCTOR",
        added_by=user,
    )

    ClinicSubscription.objects.create(
        clinic=clinic,
        plan_type=activation_code_obj.plan_type,
        expires_at=activation_code_obj.subscription_expires_at,
        max_doctors=activation_code_obj.max_doctors,
        status="ACTIVE",
    )

    ClinicVerification.objects.create(
        clinic=clinic,
        owner_phone_verified_at=owner_verified_at,
        owner_email_verified_at=owner_verified_at,
    )

    activation_code_obj.is_used = True
    activation_code_obj.used_by = user
    activation_code_obj.used_by_clinic = clinic
    activation_code_obj.used_at = timezone.now()
    activation_code_obj.save()

    return clinic


# === Clinic Working Hours Services (SCRUM-243..246) ===

from django.core.exceptions import ValidationError
from .models import ClinicWorkingHours


def create_working_hours(clinic, weekday, start_time, end_time, is_closed=False):
    """
    Creates a new ClinicWorkingHours record.
    """
    working_hours = ClinicWorkingHours(
        clinic=clinic,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        is_closed=is_closed
    )
    # The models' clean() method will ensure data integrity
    working_hours.full_clean()
    working_hours.save()
    return working_hours


def update_working_hours(instance, start_time, end_time, is_closed):
    """
    Updates an existing ClinicWorkingHours record.
    """
    instance.start_time = start_time
    instance.end_time = end_time
    instance.is_closed = is_closed
    instance.full_clean()
    instance.save()
    return instance


def delete_working_hours(instance):
    """
    Deletes an existing ClinicWorkingHours record.
    """
    instance.delete()


def get_clinic_working_hours(clinic):
    """
    Retrieves all ClinicWorkingHours for a given clinic, ordered by weekday and start_time.
    """
    return clinic.working_hours.all()


def validate_doctor_availability_within_clinic_hours(clinic, weekday, start_time, end_time):
    """
    Validates that a doctor's proposed availability falls within the clinic's defined working hours.
    
    Rules:
    - If the weekday is marked explicitly as closed, raises a ValidationError.
    - If working ranges are defined, the proposed availability must fall completely
      within at least ONE valid working range.
    - If no working ranges are defined at all for this weekday (and not marked closed),
      validation passes (allows backward compatibility / optional use).
    """
    hours_for_day = ClinicWorkingHours.objects.filter(clinic=clinic, weekday=weekday)

    # If no records exist for this day at all, we don't block (optional enforcement)
    if not hours_for_day.exists():
        return

    # If any record explicitly marks the day as closed
    if hours_for_day.filter(is_closed=True).exists():
        raise ValidationError(
            f"The clinic '{clinic.name}' is closed on this day. Doctors cannot schedule availability."
        )
    
    # We must find AT LEAST ONE working range that completely contains the proposed availability
    valid_range_found = False
    for working_range in hours_for_day:
        if working_range.start_time <= start_time and working_range.end_time >= end_time:
            valid_range_found = True
            break
            
    if not valid_range_found:
        # Build a helpful error message to show available times
        ranges_str = ", ".join(
            [f"{hr.start_time.strftime('%H:%M')}-{hr.end_time.strftime('%H:%M')}" for hr in hours_for_day]
        )
        raise ValidationError(
            f"The proposed availability ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}) "
            f"falls outside the clinic's operating hours for this day ({ranges_str})."
        )


# === Clinic Compliance Settings Services ===

from compliance.models import ClinicComplianceSettings


def get_clinic_compliance_settings(clinic):
    """
    Retrieves (or creates) the ClinicComplianceSettings for a given clinic.
    Enforces clinic isolation by querying only the given clinic.
    Uses the reverse relation to keep Django's OneToOne cache in sync.
    """
    try:
        return clinic.compliance_settings
    except ClinicComplianceSettings.DoesNotExist:
        settings = ClinicComplianceSettings.objects.create(clinic=clinic)
        # Populate the reverse cache so subsequent accesses see this object
        clinic.compliance_settings = settings
        return settings


def update_clinic_compliance_settings(clinic, max_no_show_count, forgiveness_enabled, forgiveness_days):
    """
    Updates the clinic's compliance settings.

    Maps user-facing parameter names to existing model fields:
      max_no_show_count  → score_threshold_block
      forgiveness_enabled → auto_forgive_enabled
      forgiveness_days    → auto_forgive_after_days

    Validates via model clean() before saving.
    """
    settings = get_clinic_compliance_settings(clinic)
    settings.score_threshold_block = max_no_show_count
    settings.auto_forgive_enabled = forgiveness_enabled
    settings.auto_forgive_after_days = forgiveness_days if forgiveness_enabled else None
    settings.save()  # triggers full_clean() via model override
    return settings


def should_block_patient(clinic, patient):
    """
    Returns True if the patient should be blocked at this clinic.
    Delegates to the compliance service layer.
    """
    from compliance.services.compliance_service import is_patient_blocked
    return is_patient_blocked(clinic, patient)


def apply_auto_forgiveness(clinic):
    """
    Runs auto-forgiveness logic for a single clinic.
    Only applies if the clinic has auto_forgive_enabled=True.
    """
    from compliance.services.compliance_service import run_auto_forgiveness as _run_all
    from compliance.models import PatientClinicCompliance, ComplianceEvent
    from django.utils import timezone as tz
    from django.db import transaction as txn

    settings = get_clinic_compliance_settings(clinic)
    if not settings.auto_forgive_enabled or not settings.auto_forgive_after_days:
        return

    now = tz.now()
    threshold_date = now - tz.timedelta(days=settings.auto_forgive_after_days)

    with txn.atomic():
        compliances = PatientClinicCompliance.objects.filter(
            clinic=clinic,
            bad_score__gt=0,
            last_violation_at__lte=threshold_date,
        )
        for compliance in compliances:
            old_score = compliance.bad_score
            compliance.bad_score = 0
            compliance.status = 'OK'
            compliance.blocked_at = None
            compliance.last_forgiven_at = now
            compliance.save()

            ComplianceEvent.objects.create(
                clinic=clinic,
                patient=compliance.patient,
                event_type='AUTO_FORGIVENESS',
                score_change=-old_score,
                appointment=None,
            )
