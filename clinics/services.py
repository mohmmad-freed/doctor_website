from django.db import transaction
from django.utils import timezone

from .models import Clinic, ClinicStaff, ClinicSubscription, ClinicVerification


@transaction.atomic
def create_clinic_for_main_doctor(user, cleaned_data, activation_code_obj):
    """
    Atomically create a clinic and wire up all related records:

    1. Create the Clinic row.
    2. Set specialties (M2M).
    3. Create ClinicStaff(role=MAIN_DOCTOR) linking the owner.
    4. Create ClinicSubscription seeded from the activation code.
    5. Create ClinicVerification (all channels start unverified).
    6. Mark the ClinicActivationCode as used.

    If any step raises an exception the whole transaction is rolled back,
    leaving the DB in a clean state.

    Returns the newly created Clinic instance.
    """
    clinic = Clinic.objects.create(
        name=cleaned_data["clinic_name"],
        address=cleaned_data["clinic_address"],
        city=cleaned_data["clinic_city"],
        phone=cleaned_data["clinic_phone"],
        email=cleaned_data.get("clinic_email") or "",
        description=cleaned_data.get("clinic_description", ""),
        status="PENDING",
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

    ClinicVerification.objects.create(clinic=clinic)

    activation_code_obj.is_used = True
    activation_code_obj.used_by = user
    activation_code_obj.used_by_clinic = clinic
    activation_code_obj.used_at = timezone.now()
    activation_code_obj.save()

    return clinic
