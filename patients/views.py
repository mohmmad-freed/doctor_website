from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def dashboard(request):
    return HttpResponse("Patient Dashboard - Coming Soon!")


@login_required
def clinics_list(request):
    return HttpResponse("Available Clinics - Coming Soon!")


@login_required
def my_appointments(request):
    return HttpResponse("My Appointments - Coming Soon!")


@login_required
def book_appointment(request, clinic_id):
    return HttpResponse(f"Book Appointment at Clinic {clinic_id} - Coming Soon!")


@login_required
def profile(request):
    return HttpResponse("My Profile - Coming Soon!")