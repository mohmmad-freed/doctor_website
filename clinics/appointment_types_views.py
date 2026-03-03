from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.core.exceptions import ValidationError

from appointments.models import AppointmentType
from appointments.services.appointment_type_service import (
    get_appointment_types_for_clinic,
    create_appointment_type,
    update_appointment_type,
    toggle_appointment_type_status,
)
from clinics.models import Clinic


def _get_clinic_for_owner(request, clinic_id):
    return get_object_or_404(Clinic, id=clinic_id, main_doctor=request.user, is_active=True)


@login_required
def appointment_types_list(request, clinic_id):
    """List all appointment types for the clinic."""
    clinic = _get_clinic_for_owner(request, clinic_id)
    appointment_types = get_appointment_types_for_clinic(clinic.id)
    return render(
        request,
        'clinics/appointment_types/list.html',
        {'appointment_types': appointment_types, 'clinic': clinic}
    )


@login_required
def appointment_type_create(request, clinic_id):
    """Create a new appointment type."""
    clinic = _get_clinic_for_owner(request, clinic_id)

    if request.method == "POST":
        try:
            create_appointment_type(clinic, request.POST)
            messages.success(request, "تمت إضافة نوع الموعد بنجاح.")
            return redirect(reverse('clinics:appointment_types_list', kwargs={'clinic_id': clinic_id}))
        except ValidationError as e:
            for message in e.messages:
                messages.error(request, message)

    return render(request, 'clinics/appointment_types/form.html', {'clinic': clinic})


@login_required
def appointment_type_update(request, clinic_id, type_id):
    """Update an existing appointment type."""
    clinic = _get_clinic_for_owner(request, clinic_id)
    appointment_type = get_object_or_404(AppointmentType, id=type_id, clinic=clinic)

    if request.method == "POST":
        try:
            update_appointment_type(clinic, type_id, request.POST)
            messages.success(request, "تم تحديث نوع الموعد بنجاح.")
            return redirect(reverse('clinics:appointment_types_list', kwargs={'clinic_id': clinic_id}))
        except ValidationError as e:
            for message in e.messages:
                messages.error(request, message)

    return render(
        request,
        'clinics/appointment_types/form.html',
        {'appointment_type': appointment_type, 'clinic': clinic}
    )


@login_required
def appointment_type_toggle(request, clinic_id, type_id):
    """Toggle the active status of an appointment type."""
    clinic = _get_clinic_for_owner(request, clinic_id)

    if request.method == "POST":
        toggle_appointment_type_status(clinic, type_id)
        messages.success(request, "تم تغيير حالة نوع الموعد.")

    return redirect(reverse('clinics:appointment_types_list', kwargs={'clinic_id': clinic_id}))
