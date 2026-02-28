from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from .models import Clinic, ClinicSubscription


@login_required
def my_clinic(request):
    clinic = get_object_or_404(Clinic, main_doctor=request.user, is_active=True)
    subscription = getattr(clinic, "subscription", None)
    return render(request, "clinics/my_clinic.html", {
        "clinic": clinic,
        "subscription": subscription,
    })


@login_required
def manage_staff(request):
    return HttpResponse("Manage Clinic Staff - Coming Soon!")


@login_required
def add_staff(request):
    return HttpResponse("Add Staff Member - Coming Soon!")


@login_required
def remove_staff(request, staff_id):
    return HttpResponse(f"Remove Staff {staff_id} - Coming Soon!")
