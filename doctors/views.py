from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def dashboard(request):
    return HttpResponse("Doctor Dashboard - Coming Soon!")


@login_required
def appointments_list(request):
    return HttpResponse("Doctor Appointments List - Coming Soon!")


@login_required
def appointment_detail(request, appointment_id):
    return HttpResponse(f"Appointment {appointment_id} Detail - Coming Soon!")


@login_required
def patients_list(request):
    return HttpResponse("Doctor's Patients List - Coming Soon!")