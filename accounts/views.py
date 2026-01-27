from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .forms import LoginForm, PatientRegistrationForm, MainDoctorRegistrationForm
from clinics.models import Clinic
from patients.models import PatientProfile


@login_required
def home_redirect(request):
    """Redirect users to their role-specific dashboard"""
    user = request.user
    
    if user.role == 'PATIENT':
        return redirect('patients:dashboard')
    elif user.role == 'DOCTOR':
        return redirect('doctors:dashboard')
    elif user.role == 'SECRETARY':
        return redirect('secretary:dashboard')
    elif user.role == 'MAIN_DOCTOR':
        return redirect('clinics:my_clinic')
    else:
        return redirect('admin:index')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('accounts:home')
    
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            user = authenticate(request, username=email, password=password)
            
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {user.name}!')
                return redirect('accounts:home')
            else:
                messages.error(request, 'Invalid email or password.')
    else:
        form = LoginForm()
    
    return render(request, 'accounts/login.html', {'form': form})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('accounts:home')
    
    return render(request, 'accounts/register_choice.html')


def register_patient(request):
    if request.user.is_authenticated:
        return redirect('accounts:home')
    
    if request.method == 'POST':
        form = PatientRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            
            # Create patient profile
            PatientProfile.objects.create(user=user)
            
            login(request, user)
            messages.success(request, f'Welcome, {user.name}! Your account has been created.')
            return redirect('accounts:home')
    else:
        form = PatientRegistrationForm()
    
    return render(request, 'accounts/register_patient.html', {'form': form})


def register_main_doctor(request):
    if request.user.is_authenticated:
        return redirect('accounts:home')
    
    if request.method == 'POST':
        form = MainDoctorRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            
            # Get activation code and clinic name
            activation_code_obj = form.cleaned_data['activation_code_obj']
            
            # Create clinic
            clinic = Clinic.objects.create(
                name=activation_code_obj.clinic_name,
                address=form.cleaned_data['clinic_address'],
                phone=form.cleaned_data['clinic_phone'],
                email=form.cleaned_data['clinic_email'],
                description=form.cleaned_data.get('clinic_description', ''),
                main_doctor=user
            )
            
            # Mark activation code as used
            activation_code_obj.is_used = True
            activation_code_obj.used_by = user
            activation_code_obj.used_at = timezone.now()
            activation_code_obj.save()
            
            login(request, user)
            messages.success(request, f'Welcome, Dr. {user.name}! Your clinic "{clinic.name}" has been created.')
            return redirect('accounts:home')
    else:
        form = MainDoctorRegistrationForm()
    
    return render(request, 'accounts/register_main_doctor.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('accounts:login')