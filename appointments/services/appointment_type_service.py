from django.core.exceptions import ValidationError
from appointments.models import AppointmentType, DoctorClinicAppointmentType


DEFAULT_SLOT_STEP_MINUTES = 15


# ──────────────────────────────────────────────────────────────────────────────
# CLINIC-LEVEL APPOINTMENT TYPE MANAGEMENT  (clinic owner)
# ──────────────────────────────────────────────────────────────────────────────

def get_appointment_types_for_clinic(clinic_id):
    """Return all appointment types for a clinic, ordered by name."""
    return AppointmentType.objects.filter(clinic_id=clinic_id).order_by("name")


def create_appointment_type(clinic, data):
    """Create a new appointment type for a clinic."""
    duration_minutes = int(data.get("duration_minutes", 0))
    if duration_minutes <= 0:
        raise ValidationError("يجب أن تكون المدة بالدقائق رقماً صحيحاً موجباً.")

    name = data.get("name", "").strip()
    if not name:
        raise ValidationError("اسم نوع الموعد مطلوب.")

    if AppointmentType.objects.filter(clinic=clinic, name=name).exists():
        raise ValidationError("يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة.")

    return AppointmentType.objects.create(
        clinic=clinic,
        name=name,
        name_ar=data.get("name_ar", "").strip(),
        duration_minutes=duration_minutes,
        is_active=(
            data.get("is_active") == "True"
            or data.get("is_active") is True
            or data.get("is_active") == "on"
        ),
        price=data.get("price", 0.0),
        description=data.get("description", "").strip(),
    )


def update_appointment_type(clinic, type_id, data):
    """Update an existing appointment type for a clinic."""
    appointment_type = AppointmentType.objects.get(id=type_id, clinic=clinic)

    if "duration_minutes" in data:
        duration_minutes = int(data["duration_minutes"])
        if duration_minutes <= 0:
            raise ValidationError("يجب أن تكون المدة بالدقائق رقماً صحيحاً موجباً.")
        appointment_type.duration_minutes = duration_minutes

    if "name" in data:
        name = data["name"].strip()
        if not name:
            raise ValidationError("اسم نوع الموعد مطلوب.")
        if (
            name != appointment_type.name
            and AppointmentType.objects.filter(clinic=clinic, name=name).exists()
        ):
            raise ValidationError("يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة.")
        appointment_type.name = name

    if "name_ar" in data:
        appointment_type.name_ar = data["name_ar"].strip()

    if "is_active" in data:
        appointment_type.is_active = (
            data["is_active"] == "True"
            or data["is_active"] is True
            or data.get("is_active") == "on"
        )

    if "price" in data:
        appointment_type.price = data["price"]

    if "description" in data:
        appointment_type.description = data["description"].strip()

    appointment_type.save()
    return appointment_type


def toggle_appointment_type_status(clinic, type_id):
    """Toggle the active/inactive status of a clinic appointment type."""
    appointment_type = AppointmentType.objects.get(id=type_id, clinic=clinic)
    appointment_type.is_active = not appointment_type.is_active
    appointment_type.save()
    return appointment_type


# ──────────────────────────────────────────────────────────────────────────────
# DOCTOR-SPECIFIC APPOINTMENT TYPE MANAGEMENT
# ──────────────────────────────────────────────────────────────────────────────

def get_appointment_types_for_doctor_in_clinic(doctor_id, clinic_id):
    """
    Return the appointment types a doctor offers inside a specific clinic.

    Backwards-compatibility rule:
      If NO DoctorClinicAppointmentType rows exist for (doctor, clinic), we fall
      back to returning all ACTIVE clinic types.  This means existing clinics that
      have never configured doctor-specific types continue to work unchanged.

    Once at least one row is configured, only the IS_ACTIVE=True rows are returned.
    """
    configured_qs = DoctorClinicAppointmentType.objects.filter(
        doctor_id=doctor_id,
        clinic_id=clinic_id,
    ).select_related("appointment_type")

    if not configured_qs.exists():
        # Backwards-compat: no config → all active clinic types
        return AppointmentType.objects.filter(
            clinic_id=clinic_id, is_active=True
        ).order_by("name")

    # Config exists → return only enabled, active types
    active_type_ids = configured_qs.filter(
        is_active=True,
        appointment_type__is_active=True,
    ).values_list("appointment_type_id", flat=True)

    return AppointmentType.objects.filter(
        id__in=active_type_ids
    ).order_by("name")


def get_slot_step_minutes_for_clinic(clinic_id) -> int:
    """
    Return the smallest ``duration_minutes`` among all *active* appointment
    types in the clinic, used as the bucket size when no specific doctor is
    selected (e.g. the secretary's "All doctors" calendar view). Falls back to
    ``DEFAULT_SLOT_STEP_MINUTES`` when the clinic has no active types yet.
    """
    durations = list(
        AppointmentType.objects.filter(clinic_id=clinic_id, is_active=True)
        .values_list("duration_minutes", flat=True)
    )
    valid = [d for d in durations if d]
    return min(valid) if valid else DEFAULT_SLOT_STEP_MINUTES


def get_slot_step_minutes_for_doctor(doctor_id, clinic_id) -> int:
    """
    Return the slot-grid step (in minutes) that the booking workflow should
    use when rendering time slots for ``doctor`` in ``clinic``.

    The step is the smallest ``duration_minutes`` among the doctor's enabled
    appointment types in this clinic. Reuses the same fallback rule as
    ``get_appointment_types_for_doctor_in_clinic`` (no per-doctor config →
    all active clinic types). Falls back to ``DEFAULT_SLOT_STEP_MINUTES`` if
    the doctor has no enabled types yet.
    """
    types = get_appointment_types_for_doctor_in_clinic(doctor_id, clinic_id)
    durations = [t.duration_minutes for t in types if t.duration_minutes]
    return min(durations) if durations else DEFAULT_SLOT_STEP_MINUTES


def get_doctor_type_assignments(doctor_id, clinic_id):
    """
    Return a list of dicts describing ALL clinic appointment types with their
    assignment status for a given doctor.

    Used by the clinic-owner and doctor UIs to show a checklist.

    Each dict:  {"appointment_type": <AppointmentType>, "is_assigned": bool, "is_active": bool, "dcat_id": int|None}
    """
    all_types = AppointmentType.objects.filter(
        clinic_id=clinic_id, is_active=True
    ).order_by("name")

    dcat_map = {
        d.appointment_type_id: d
        for d in DoctorClinicAppointmentType.objects.filter(
            doctor_id=doctor_id, clinic_id=clinic_id
        )
    }

    result = []
    for at in all_types:
        dcat = dcat_map.get(at.id)
        result.append(
            {
                "appointment_type": at,
                "is_assigned": dcat is not None,
                "is_active": dcat.is_active if dcat else False,
                "dcat_id": dcat.id if dcat else None,
            }
        )
    return result


def set_doctor_clinic_appointment_types(doctor_id, clinic_id, active_type_ids):
    """
    Replace the doctor's enabled appointment types for a clinic.

    `active_type_ids` is an iterable of AppointmentType PKs that should be
    ACTIVE for this doctor.  All other clinic types will be set is_active=False
    (rows created if needed).

    Validates that all supplied type IDs belong to the clinic.

    Returns the count of active assignments after the update.
    """
    active_type_ids = set(int(x) for x in active_type_ids)

    # Validate all IDs belong to this clinic
    valid_ids = set(
        AppointmentType.objects.filter(
            clinic_id=clinic_id, is_active=True
        ).values_list("id", flat=True)
    )
    invalid = active_type_ids - valid_ids
    if invalid:
        raise ValidationError(
            "بعض أنواع المواعيد المختارة لا تنتمي إلى هذه العيادة أو غير مفعّلة."
        )

    # Upsert rows for each clinic type
    for type_id in valid_ids:
        should_be_active = type_id in active_type_ids
        DoctorClinicAppointmentType.objects.update_or_create(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            appointment_type_id=type_id,
            defaults={"is_active": should_be_active},
        )

    return DoctorClinicAppointmentType.objects.filter(
        doctor_id=doctor_id, clinic_id=clinic_id, is_active=True
    ).count()


def toggle_doctor_clinic_appointment_type(doctor_id, clinic_id, type_id):
    """
    Toggle a single DoctorClinicAppointmentType row's is_active flag.
    Creates the row if it doesn't exist (defaulting to active=True then toggling to False).
    Returns the resulting is_active value.
    """
    # Validate the type belongs to this clinic
    at = AppointmentType.objects.filter(
        id=type_id, clinic_id=clinic_id, is_active=True
    ).first()
    if at is None:
        raise ValidationError("نوع الموعد غير موجود أو غير مفعّل في هذه العيادة.")

    dcat, created = DoctorClinicAppointmentType.objects.get_or_create(
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        appointment_type_id=type_id,
        defaults={"is_active": True},
    )
    if not created:
        dcat.is_active = not dcat.is_active
        dcat.save(update_fields=["is_active", "updated_at"])
    # If just created, it defaults to True — no toggle needed
    return dcat.is_active
