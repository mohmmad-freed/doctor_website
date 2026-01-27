from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def dashboard(request):
    return HttpResponse("Secretary Dashboard - Coming Soon!")


@login_required
def appointments_list(request):
    return HttpResponse("Secretary Appointments List - Coming Soon!")


@login_required
def create_appointment(request):
    return HttpResponse("Create Appointment - Coming Soon!")


@login_required
def edit_appointment(request, appointment_id):
    return HttpResponse(f"Edit Appointment {appointment_id} - Coming Soon!")