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

    plan_name = getattr(activation_code_obj, 'plan_name', ClinicSubscription.PlanName.SMALL)
    # For SMALL and MEDIUM plans apply PLAN_LIMITS defaults unless the activation
    # code already overrides them with non-default values.  For ENTERPRISE the
    # admin is expected to set max_doctors/max_secretaries explicitly on the code.
    plan_defaults = ClinicSubscription.PLAN_LIMITS.get(plan_name, {})
    max_doctors = activation_code_obj.max_doctors
    max_secretaries = getattr(activation_code_obj, 'max_secretaries', plan_defaults.get('secretaries', 5))
    if plan_defaults:
        # If the activation code still carries the old model-level defaults,
        # prefer the corrected PLAN_LIMITS value so existing codes get the right limits.
        if max_doctors == 2 and plan_defaults.get('doctors') != 2:
            max_doctors = plan_defaults['doctors']
        if max_secretaries in (1, 5) and plan_defaults.get('secretaries'):
            max_secretaries = plan_defaults['secretaries']

    ClinicSubscription.objects.create(
        clinic=clinic,
        plan_type=activation_code_obj.plan_type,
        plan_name=plan_name,
        expires_at=activation_code_obj.subscription_expires_at,
        max_doctors=max_doctors,
        max_secretaries=max_secretaries,
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

# === Clinic Invitation Services ===

from accounts.backends import PhoneNumberAuthBackend
from accounts.models import CustomUser
from accounts.services.identity_claim_service import (
    assign_national_id,
    get_effective_national_id_for_user,
    get_national_id_owner_user,
    get_verified_claim_for_national_id,
    get_verified_claim_for_user,
    normalize_national_id,
    validate_national_id,
)
from doctors.models import DoctorProfile, DoctorSpecialty
from accounts.services.tweetsms import send_sms
from .models import ClinicInvitation, InvitationAuditLog


def _normalize_phone_for_invite(phone_str):
    phone = PhoneNumberAuthBackend.normalize_phone_number(phone_str)
    if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
        raise ValidationError("رقم الهاتف غير صحيح. يجب أن يتكون من 10 أرقام ويبدأ بـ 05.")
    return phone


def _log_invitation_action(invitation, action, performed_by=None):
    """Create an audit log entry for an invitation lifecycle event."""
    InvitationAuditLog.objects.create(
        clinic=invitation.clinic,
        invitation=invitation,
        action=action,
        performed_by=performed_by,
    )


def _check_invitation_rate_limits(clinic, normalized_phone):
    """Enforce rate limits: max 3/phone/hour and max 10/clinic/hour."""
    one_hour_ago = timezone.now() - timezone.timedelta(hours=1)

    # Per-phone limit (across all clinics)
    phone_count = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone,
        created_at__gte=one_hour_ago,
    ).count()
    if phone_count >= 3:
        raise ValidationError(
            "تم تجاوز الحد الأقصى لعدد الدعوات لهذا الرقم. يرجى المحاولة لاحقاً."
        )

    # Per-clinic limit
    clinic_count = ClinicInvitation.objects.filter(
        clinic=clinic,
        created_at__gte=one_hour_ago,
    ).count()
    if clinic_count >= 10:
        raise ValidationError(
            "تم تجاوز الحد الأقصى لعدد الدعوات من هذه العيادة. يرجى المحاولة لاحقاً."
        )


def create_invitation(
    clinic,
    owner,
    data,
    role="DOCTOR",
    request=None,
    accept_base_url=None,
):
    """
    Creates a ClinicInvitation for a doctor or secretary.

    Architectural rules enforced:
    - Phone = primary identity key
    - Email = delivery destination (not identity key)
    - Email is the primary invitation channel
    - PendingDoctorIdentity lock for new phone numbers
    - Expired invitations are immutable (fresh invitation required)
    - Revoked memberships allow re-invitation
    """
    if owner != clinic.main_doctor:
        raise ValidationError("Only the main doctor can invite other staff.")

    # 1. Check subscription exists and is effectively active
    try:
        subscription = clinic.subscription
    except ClinicSubscription.DoesNotExist:
        raise ValidationError("العيادة ليس لديها اشتراك نشط.")

    if not subscription.is_effectively_active():
        raise ValidationError("اشتراك العيادة غير نشط أو منتهي الصلاحية. يرجى التواصل مع الإدارة.")

    # 2. Enforce plan capacity limits
    if role == "DOCTOR":
        if not subscription.can_add_doctor():
            raise ValidationError(
                f"لقد وصلت للحد الأقصى لعدد الأطباء ({subscription.max_doctors}) حسب اشتراكك."
            )

    elif role == "SECRETARY":
        if not subscription.can_add_secretary():
            raise ValidationError(
                f"لقد وصلت للحد الأقصى لعدد السكرتيرين ({subscription.max_secretaries}) حسب اشتراكك."
            )

    # Determine data keys conditionally
    name_key = "secretary_name" if role == "SECRETARY" else "doctor_name"
    phone_key = "secretary_phone" if role == "SECRETARY" else "doctor_phone"
    email_key = "secretary_email" if role == "SECRETARY" else "doctor_email"
    national_id_key = "secretary_national_id" if role == "SECRETARY" else "doctor_national_id"

    # 2. Normalize phone
    raw_phone = data.get(phone_key, "")
    normalized_phone = _normalize_phone_for_invite(raw_phone)

    # 3. Rate limiting
    _check_invitation_rate_limits(clinic, normalized_phone)

    # 4. Handle existing PENDING invitations (expired immutability rule)
    existing_invite = ClinicInvitation.objects.filter(
        clinic=clinic, doctor_phone=normalized_phone, status="PENDING"
    ).first()
    if existing_invite:
        if not existing_invite.is_expired:
            raise ValidationError("يوجد دعوة معلقة بالفعل لهذا الرقم.")
        else:
            # Mark it expired — expired invitations are immutable, cannot be reused
            existing_invite.status = "EXPIRED"
            existing_invite.save()
            _log_invitation_action(existing_invite, "EXPIRED")

    # 5. Identity resolution using phone as PRIMARY identity key
    user_exists = CustomUser.objects.filter(phone=normalized_phone).first()
    entered_email = data.get(email_key, "")
    entered_nid = data.get(national_id_key, "")
    if entered_nid:
        entered_nid = validate_national_id(normalize_national_id(entered_nid))

    # Determine delivery email
    delivery_email = entered_email  # Default: use what clinic owner entered

    if user_exists:
        # Prevent the clinic owner from inviting themselves
        if user_exists == owner:
            raise ValidationError("لا يمكنك إرسال دعوة لنفسك.")

        # Check active membership (only non-revoked memberships block)
        existing_staff = ClinicStaff.objects.filter(
            clinic=clinic, user=user_exists, revoked_at__isnull=True
        ).first()
        if existing_staff and role in (user_exists.roles or []):
            if role == "SECRETARY":
                raise ValidationError("هذا السكرتير/ة موجود بالفعل ضمن طاقم العيادة.")
            else:
                raise ValidationError("هذا الطبيب موجود بالفعل ضمن طاقم العيادة.")

        # Email mismatch UX: email is delivery destination, NOT identity key.
        # Use the stored email if it exists, regardless of what the clinic entered.
        if user_exists.email:
            delivery_email = user_exists.email

        # NID validation — must not contradict stored NID, and if they don't have one,
        # it must not belong to someone else.
        _IDENTITY_ERROR = "تعذر إرسال الدعوة. يرجى التحقق من صحة البيانات المُدخلة."
        if entered_nid:
            existing_user_nid = get_effective_national_id_for_user(user_exists)
            if existing_user_nid:
                if entered_nid != existing_user_nid:
                    raise ValidationError(_IDENTITY_ERROR)
            else:
                nid_owner = get_national_id_owner_user(entered_nid)
                if nid_owner and nid_owner.pk != user_exists.pk:
                    raise ValidationError(_IDENTITY_ERROR)
    else:
        # Phone not in the system: NID must not be claimed by someone else
        _IDENTITY_ERROR = "تعذر إرسال الدعوة. يرجى التحقق من صحة البيانات المُدخلة."
        if entered_nid:
            nid_owner = get_national_id_owner_user(entered_nid)
            if nid_owner:
                raise ValidationError(_IDENTITY_ERROR)

        # Email must not belong to a different existing user
        if entered_email:
            email_owner = CustomUser.objects.filter(email__iexact=entered_email).first()
            if email_owner:
                raise ValidationError(_IDENTITY_ERROR)

    # 6. PendingDoctorIdentity lock (only for new phones, DOCTOR role)
    from .models import PendingDoctorIdentity
    if not user_exists and role == "DOCTOR":
        pending_lock = PendingDoctorIdentity.objects.filter(phone=normalized_phone).first()
        if pending_lock:
            # Another clinic already initiated onboarding — this is fine,
            # create the invitation but it will be picked up after onboarding completes
            pass  # Invitation will still be created and linked
        else:
            # No lock exists — we'll create one after the invitation is saved

            pass

    # 7. Create Invitation
    expires_at = timezone.now() + timezone.timedelta(hours=48)
    invitation = ClinicInvitation.objects.create(
        clinic=clinic,
        invited_by=owner,
        doctor_name=data.get(name_key, ""),
        doctor_phone=normalized_phone,
        doctor_email=delivery_email,
        doctor_national_id=entered_nid,
        role=role,
        expires_at=expires_at,
    )
    # Set specialties only for doctors
    if role == "DOCTOR":
        specialties = data.get("specialties", [])
        if specialties:
            invitation.specialties.set(specialties)

    # 8. Create PendingDoctorIdentity lock for new unregistered phones
    if not user_exists and role == "DOCTOR":
        if not PendingDoctorIdentity.objects.filter(phone=normalized_phone).exists():
            try:
                PendingDoctorIdentity.objects.create(
                    phone=normalized_phone,
                    created_by_invitation=invitation,
                )
            except Exception:
                pass  # Race condition — another process created it first; safe to ignore

    # 9. Audit log
    _log_invitation_action(invitation, "CREATED", performed_by=owner)

    # 10. Send invitation via EMAIL (primary channel)
    if delivery_email:
        try:
            from django.urls import reverse
            import logging
            _email_logger = logging.getLogger("clinics.services")

            accept_path = reverse(
                "doctors:guest_accept_invitation",
                kwargs={"token": invitation.token},
            )
            if accept_base_url:
                accept_url = f"{accept_base_url.rstrip('/')}{accept_path}"
            elif request:
                accept_url = request.build_absolute_uri(accept_path)
            else:
                accept_url = accept_path

            from accounts.email_utils import send_doctor_invitation_email
            send_doctor_invitation_email(
                invitation=invitation,
                accept_url=accept_url,
            )
            _email_logger.info("[INVITATION EMAIL] Sent to %s", delivery_email)
        except Exception as e:
            import logging
            logging.getLogger("clinics.services").error(
                "[INVITATION EMAIL] Failed to send to %s: %s", delivery_email, e
            )
            # Don't fail the invitation creation if email fails

    return invitation

@transaction.atomic
def accept_invitation(invitation, user):
    """
    Accepts a pending invitation, linking the doctor to the clinic.

    Creates:
    - ClinicStaff membership (or reactivates revoked one)
    - DoctorProfile + DoctorSpecialty assignments
    - DoctorVerification (platform identity, if not exists)
    - ClinicDoctorCredential (per clinic-specialty)
    - Releases PendingDoctorIdentity lock
    """
    # 1. Lock the invitation row to prevent race conditions
    with transaction.atomic():
        locked_invitation = ClinicInvitation.objects.select_for_update().get(id=invitation.id)

        if locked_invitation.status != "PENDING":
            raise ValidationError("هذه الدعوة لم تعد صالحة (تمت معالجتها بالفعل).")

        if locked_invitation.is_expired:
            locked_invitation.status = "EXPIRED"
            locked_invitation.save()
            raise ValidationError("لقد انتهت صلاحية هذه الدعوة.")

        normalized_user_phone = _normalize_phone_for_invite(user.phone)
        if normalized_user_phone != locked_invitation.doctor_phone:
            raise ValidationError("لا تملك صلاحية قبول هذه الدعوة (رقم الهاتف غير متطابق).")

        # Update status immediately to prevent any other process from entering this block
        locked_invitation.status = "ACCEPTED"
        locked_invitation.save()

    # Now we continue with the locked_invitation object (or the original, attributes are in sync)
    # We will use locked_invitation exclusively for consistency:
    invitation = locked_invitation

    # Re-enforce subscription limit inside atomic transaction only for DOCTOR
    if invitation.role == "DOCTOR":
        try:
            subscription = invitation.clinic.subscription
        except ClinicSubscription.DoesNotExist:
            raise ValidationError("العيادة ليس لديها اشتراك نشط.")

        current_doctors = ClinicStaff.objects.select_for_update().filter(
            clinic=invitation.clinic, role="DOCTOR",
            revoked_at__isnull=True,
        ).count()

        if current_doctors >= subscription.max_doctors:
            raise ValidationError(f"لقد وصلت العيادة للحد الأقصى لعدد الأطباء ({subscription.max_doctors}).")

    # Handle membership — check for revoked membership (re-activation)
    existing_staff = ClinicStaff.objects.filter(
        clinic=invitation.clinic, user=user,
    ).first()

    if existing_staff:
        if existing_staff.revoked_at:
            # Re-activate revoked membership
            existing_staff.revoked_at = None
            existing_staff.is_active = True
            existing_staff.role = invitation.role
            existing_staff.save()
            staff = existing_staff
        else:
            # Already active — keep existing role
            staff = existing_staff
    else:
        staff = ClinicStaff.objects.create(
            clinic=invitation.clinic,
            user=user,
            role=invitation.role,
            added_by=invitation.invited_by,
        )

    # Handle DoctorProfile only if DOCTOR
    if invitation.role == "DOCTOR":
        profile, created_profile = DoctorProfile.objects.get_or_create(user=user)

        # Assign specialties
        invited_specialties = list(invitation.specialties.all())
        if invited_specialties:
            for i, specialty in enumerate(invited_specialties):
                is_primary = (i == 0)  # Make first one primary

                existing_ds = DoctorSpecialty.objects.filter(
                    doctor_profile=profile, specialty=specialty
                ).first()

                if not existing_ds:
                    if is_primary:
                        has_primary = DoctorSpecialty.objects.filter(
                            doctor_profile=profile, is_primary=True
                        ).exists()
                        if has_primary:
                            is_primary = False

                    DoctorSpecialty.objects.create(
                        doctor_profile=profile,
                        specialty=specialty,
                        is_primary=is_primary
                    )

        # --- Dual-layer verification ---
        # A) DoctorVerification (platform identity) — created once per doctor
        from doctors.models import DoctorVerification, ClinicDoctorCredential
        DoctorVerification.objects.get_or_create(
            user=user,
            defaults={"identity_status": "IDENTITY_UNVERIFIED"},
        )

        # B) ClinicDoctorCredential — per clinic-specialty
        for specialty in invited_specialties:
            ClinicDoctorCredential.objects.get_or_create(
                doctor=user,
                clinic=invitation.clinic,
                specialty=specialty,
                defaults={"credential_status": "CREDENTIALS_PENDING"},
            )
        # Also create a general credential record if no specialties
        if not invited_specialties:
            ClinicDoctorCredential.objects.get_or_create(
                doctor=user,
                clinic=invitation.clinic,
                specialty=None,
                defaults={"credential_status": "CREDENTIALS_PENDING"},
            )

    # Update user national_id if provided in invitation and user doesn't have one
    if invitation.doctor_national_id:
        assign_national_id(user, invitation.doctor_national_id)

    # Role hierarchy — higher index = higher privilege.
    _ROLE_RANK = {"PATIENT": 0, "SECRETARY": 1, "DOCTOR": 2, "MAIN_DOCTOR": 3}

    update_fields = []

    # Set email from invitation if user doesn't have one
    if invitation.doctor_email and not user.email:
        user.email = invitation.doctor_email
        update_fields.append("email")

    # Add to roles array if missing
    if invitation.role not in (user.roles or []):
        roles = list(user.roles or [])
        roles.append(invitation.role)
        user.roles = roles
        update_fields.append("roles")

    # Promote primary role if invitation role is higher in the hierarchy
    current_rank = _ROLE_RANK.get(user.role, 0)
    invited_rank = _ROLE_RANK.get(invitation.role, 0)
    if invited_rank > current_rank:
        user.role = invitation.role
        update_fields.append("role")

    if update_fields:
        user.save(update_fields=update_fields)

    # Note: Status was already set to ACCEPTED early in the transaction to prevent races.

    _log_invitation_action(invitation, "ACCEPTED", performed_by=user)

    # Release PendingDoctorIdentity lock
    from .models import PendingDoctorIdentity
    PendingDoctorIdentity.objects.filter(phone=normalized_user_phone).delete()

    return staff


def cancel_invitation(invitation, owner):
    """Clinic owner cancels a pending invitation."""
    if owner != invitation.clinic.main_doctor:
        raise ValidationError("ليس لديك صلاحية لإلغاء هذه الدعوة.")
    
    if invitation.status != "PENDING":
        raise ValidationError("لا يمكن إلغاء هذه الدعوة لأنها ليست معلقة.")
        
    invitation.status = "CANCELLED"
    invitation.save()

    _log_invitation_action(invitation, "CANCELLED", performed_by=owner)


def reject_invitation(invitation, user):
    """Invited user rejects a pending invitation."""
    if invitation.status != "PENDING":
        raise ValidationError("هذه الدعوة لم تعد صالحة.")
        
    normalized_user_phone = _normalize_phone_for_invite(user.phone)
    if normalized_user_phone != invitation.doctor_phone:
         raise ValidationError("لا تملك صلاحية لرفض هذه الدعوة.")
         
    invitation.status = "REJECTED"
    invitation.save()

    _log_invitation_action(invitation, "REJECTED", performed_by=user)
