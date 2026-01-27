from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def my_clinic(request):
    return HttpResponse("My Clinic Dashboard - Coming Soon!")


@login_required
def manage_staff(request):
    return HttpResponse("Manage Clinic Staff - Coming Soon!")


@login_required
def add_staff(request):
    return HttpResponse("Add Staff Member - Coming Soon!")


@login_required
def remove_staff(request, staff_id):
    return HttpResponse(f"Remove Staff {staff_id} - Coming Soon!")