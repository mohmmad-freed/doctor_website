from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from .forms import LoginForm, PatientRegistrationForm, MainDoctorRegistrationForm
from clinics.models import Clinic
from patients.models import PatientProfile


User = get_user_model()


@login_required
def home_redirect(request):
    """Redirect users to their role-specific dashboard with welcome message"""
    user = request.user

    # Check if this is a new registration (you can use session flag)
    if request.session.get("just_registered", False):
        messages.success(
            request,
            f"Welcome, {user.name}! Your account has been successfully created.",
        )
        del request.session["just_registered"]

    if user.role == "PATIENT":
        return redirect("patients:dashboard")
    elif user.role == "DOCTOR":
        return redirect("doctors:dashboard")
    elif user.role == "SECRETARY":
        return redirect("secretary:dashboard")
    elif user.role == "MAIN_DOCTOR":
        return redirect("clinics:my_clinic")
    else:
        return redirect("admin:index")


def login_view(request):
    """Handle user login with phone number"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data["phone"]
            password = form.cleaned_data["password"]

            # 1. Check if user exists & Role & Verification Logic
            # Note: We do this before authenticate() for granular error messages,
            # OR we inspect why authenticate returned None.

            # Normalize phone (form already does it, but let's be sure)
            from accounts.backends import PhoneNumberAuthBackend

            normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

            user_exists = False
            try:
                user_obj = User.objects.get(phone=normalized_phone)
                user_exists = True

                # 2. Check Verification (if enforced)
                if settings.ENFORCE_PHONE_VERIFICATION and not getattr(
                    user_obj, "is_verified", True
                ):
                    messages.error(
                        request,
                        "Your phone number is not verified. Please contact support.",
                    )
                    return render(request, "accounts/login.html", {"form": form})

                # 3. Check Password (if we want safely explicit error)
                if not user_obj.check_password(password):
                    messages.error(request, "Incorrect phone number or password.")
                    return render(request, "accounts/login.html", {"form": form})

            except User.DoesNotExist:
                # 4. Handle non-existent user
                messages.error(request, "Incorrect phone number or password.")
                return render(request, "accounts/login.html", {"form": form})

            # If we reached here, manual checks passed. Now standard authenticate/login
            user = authenticate(request, username=phone, password=password)

            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {user.name}!")
                next_url = request.GET.get("next") or "accounts:home"
                return redirect(next_url)
            else:
                # Should technically not verify reach here if logic above is sound
                messages.error(request, "Authentication failed.")
        else:
            # Form invalid
            messages.error(request, "Please correct the errors below.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


def register_view(request):
    """Show registration choice page"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    return render(request, "accounts/register_choice.html")


def register_patient(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = PatientRegistrationForm(request.POST)
        if form.is_valid():
            try:
                # Save user
                user = form.save()

                # Create patient profile
                PatientProfile.objects.create(user=user)

                # Log the user in with your custom backend
                login(request, user, backend="accounts.backends.PhoneNumberAuthBackend")

                # Set session flag for welcome message
                request.session["just_registered"] = True

                return redirect("accounts:home")

            except Exception as e:
                messages.error(
                    request, f"An error occurred during registration: {str(e)}"
                )
        else:
            # Print errors to console for debugging
            print("FORM FIELD ERRORS:", form.errors)
            print("FORM NON-FIELD ERRORS:", form.non_field_errors())
            messages.error(request, "Please correct the errors below.")
    else:
        form = PatientRegistrationForm()

    return render(request, "accounts/register_patient.html", {"form": form})


def register_main_doctor(request):
    """Handle main doctor registration with clinic creation"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = MainDoctorRegistrationForm(request.POST)
        if form.is_valid():
            try:
                # Save user
                user = form.save()

                # Get activation code and clinic name
                activation_code_obj = form.cleaned_data["activation_code_obj"]

                # Create clinic
                clinic = Clinic.objects.create(
                    name=activation_code_obj.clinic_name,
                    address=form.cleaned_data["clinic_address"],
                    phone=form.cleaned_data["clinic_phone"],
                    email=form.cleaned_data["clinic_email"],
                    description=form.cleaned_data.get("clinic_description", ""),
                    main_doctor=user,
                )

                # Mark activation code as used
                activation_code_obj.is_used = True
                activation_code_obj.used_by = user
                activation_code_obj.used_at = timezone.now()
                activation_code_obj.save()

                # Log the user in
                login(request, user, backend="accounts.backends.PhoneNumberAuthBackend")

                # Set session flag for welcome message
                request.session["just_registered"] = True

                # Additional success message for clinic creation
                messages.success(
                    request,
                    f'Welcome, Dr. {user.name}! Your clinic "{clinic.name}" has been successfully created.',
                )

                return redirect("accounts:home")

            except Exception as e:
                # Handle any unexpected errors during save
                messages.error(
                    request,
                    f"An error occurred during registration. Please try again. Error: {str(e)}",
                )
        else:
            # Form validation failed
            messages.error(request, "Please correct the errors below.")
    else:
        form = MainDoctorRegistrationForm()

    return render(request, "accounts/register_main_doctor.html", {"form": form})


def logout_view(request):
    """Handle user logout"""
    user_name = request.user.name if request.user.is_authenticated else None
    logout(request)

    if user_name:
        messages.info(
            request, f"Goodbye, {user_name}! You have been logged out successfully."
        )
    else:
        messages.info(request, "You have been logged out successfully.")

    return redirect("accounts:login")
