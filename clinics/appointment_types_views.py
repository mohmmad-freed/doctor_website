from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden

from appointments.models import AppointmentType
from appointments.services.appointment_type_service import (
    get_appointment_types_for_clinic,
    create_appointment_type,
    update_appointment_type,
    toggle_appointment_type_status,
)
from django.core.exceptions import ValidationError

def _is_owner(request):
    return getattr(request.user, "role", None) == "MAIN_DOCTOR"

@login_required
def appointment_types_list(request):
    """List all appointment types for the clinic."""
    if not _is_owner(request):
        return HttpResponseForbidden("Access denied. Only Clinic Owners can manage appointment types.")
        
    clinic = request.user.owned_clinic.first()
    if not clinic:
        return HttpResponseForbidden("No clinic found.")

    appointment_types = get_appointment_types_for_clinic(clinic.id)
    return render(
        request, 
        'clinics/appointment_types/list.html', 
        {'appointment_types': appointment_types, 'clinic': clinic}
    )

@login_required
def appointment_type_create(request):
    """Create a new appointment type."""
    if not _is_owner(request):
        return HttpResponseForbidden("Access denied. Only Clinic Owners can manage appointment types.")
        
    clinic = request.user.owned_clinic.first()
    if not clinic:
        return HttpResponseForbidden("No clinic found.")

    if request.method == "POST":
        try:
            create_appointment_type(clinic, request.POST)
            messages.success(request, "تمت إضافة نوع الموعد بنجاح.")
            return redirect('clinics:appointment_types_list')
        except ValidationError as e:
            for message in e.messages:
                messages.error(request, message)

    return render(request, 'clinics/appointment_types/form.html', {'clinic': clinic})

@login_required
def appointment_type_update(request, type_id):
    """Update an existing appointment type."""
    if not _is_owner(request):
        return HttpResponseForbidden("Access denied. Only Clinic Owners can manage appointment types.")
        
    clinic = request.user.owned_clinic.first()
    if not clinic:
        return HttpResponseForbidden("No clinic found.")

    appointment_type = get_object_or_404(AppointmentType, id=type_id, clinic=clinic)

    if request.method == "POST":
        try:
            update_appointment_type(clinic, type_id, request.POST)
            messages.success(request, "تم تحديث نوع الموعد بنجاح.")
            return redirect('clinics:appointment_types_list')
        except ValidationError as e:
            for message in e.messages:
                messages.error(request, message)

    return render(
        request, 
        'clinics/appointment_types/form.html', 
        {'appointment_type': appointment_type, 'clinic': clinic}
    )

@login_required
def appointment_type_toggle(request, type_id):
    """Toggle the active status of an appointment type."""
    if not _is_owner(request):
        return HttpResponseForbidden("Access denied. Only Clinic Owners can manage appointment types.")
        
    clinic = request.user.owned_clinic.first()
    if not clinic:
        return HttpResponseForbidden("No clinic found.")

    if request.method == "POST":
        toggle_appointment_type_status(clinic, type_id)
        messages.success(request, "تم تغيير حالة نوع الموعد.")
        
    return redirect('clinics:appointment_types_list')
