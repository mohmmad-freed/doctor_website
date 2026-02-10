from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.http import JsonResponse
import re

from .forms import LoginForm, PatientRegistrationForm, MainDoctorRegistrationForm
from .otp_utils import request_otp, verify_otp, is_in_cooldown, get_remaining_resends
from clinics.models import Clinic
from patients.models import PatientProfile
from .email_utils import (
    send_verification_email,
    verify_email_token,
    send_change_email_verification,
)
from accounts.backends import PhoneNumberAuthBackend

User = get_user_model()


@login_required
def home_redirect(request):
    """Redirect users to their role-specific dashboard with welcome message"""
    user = request.user

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

            normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

            try:
                user_obj = User.objects.get(phone=normalized_phone)

                if settings.ENFORCE_PHONE_VERIFICATION and not user_obj.is_verified:
                    messages.error(
                        request,
                        "Your phone number is not verified. Please contact support.",
                    )
                    return render(request, "accounts/login.html", {"form": form})

                if not user_obj.check_password(password):
                    messages.error(request, "Incorrect phone number or password.")
                    return render(request, "accounts/login.html", {"form": form})

            except User.DoesNotExist:
                messages.error(request, "Incorrect phone number or password.")
                return render(request, "accounts/login.html", {"form": form})

            user = authenticate(request, username=phone, password=password)

            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {user.name}!")
                next_url = request.GET.get("next") or "accounts:home"
                return redirect(next_url)
            else:
                messages.error(request, "Authentication failed.")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


def register_view(request):
    """Show registration choice page"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    return render(request, "accounts/register_choice.html")


# ============================================
# STEP 1: Enter phone number and request OTP
# ============================================
def register_patient_phone(request):
    """First step: Enter phone number and send OTP"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()

        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        # Validate format
        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            messages.error(
                request,
                "Invalid phone number. Must start with 059 or 056 and be 10 digits.",
            )
            return render(request, "accounts/register_patient_phone.html")

        # Check if already registered
        if User.objects.filter(phone=phone).exists():
            messages.error(request, "This phone number is already registered.")
            return render(request, "accounts/register_patient_phone.html")

        # Request OTP
        success, message = request_otp(phone)
        if success:
            request.session["registration_phone"] = phone
            messages.success(request, message)
            return redirect("accounts:register_patient_verify")
        else:
            messages.error(request, message)

    return render(request, "accounts/register_patient_phone.html")


# ============================================
# STEP 2: Enter OTP to verify phone
# ============================================
def register_patient_verify(request):
    """Second step: Verify OTP"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    phone = request.session.get("registration_phone")
    if not phone:
        messages.error(request, "Session expired. Please start registration again.")
        return redirect("accounts:register_patient_phone")

    if request.method == "POST":
        action = request.POST.get("action")

        # Handle resend OTP
        if action == "resend":
            remaining = get_remaining_resends(phone)
            if remaining <= 0:
                messages.error(
                    request, "You have reached the maximum OTP requests for today."
                )
            else:
                success, message = request_otp(phone)
                if success:
                    messages.success(request, message)
                else:
                    messages.error(request, message)
            return redirect("accounts:register_patient_verify")

        # Handle OTP verification
        entered_otp = request.POST.get("otp", "").strip()

        if not entered_otp:
            messages.error(request, "Please enter the OTP code.")
            return render(
                request,
                "accounts/register_patient_verify.html",
                {
                    "phone": phone,
                    "remaining_resends": get_remaining_resends(phone),
                    "cooldown": is_in_cooldown(phone),
                },
            )

        success, message = verify_otp(phone, entered_otp)

        if success:
            request.session["phone_verified"] = True
            messages.success(request, message)

            # Redirect to registration details
            return redirect("accounts:register_patient_details")
        else:
            messages.error(request, message)

    remaining_resends = get_remaining_resends(phone)
    cooldown = is_in_cooldown(phone)

    return render(
        request,
        "accounts/register_patient_verify.html",
        {
            "phone": phone,
            "remaining_resends": remaining_resends,
            "cooldown": cooldown,
        },
    )


# ============================================
# EMAIL VERIFICATION (AJAX)
# ============================================
def send_email_verification(request):
    """Send email verification link via AJAX"""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid request method."})

    email = request.POST.get("email", "").strip()

    if not email:
        return JsonResponse({"success": False, "message": "Email is required."})

    # Basic email validation
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return JsonResponse({"success": False, "message": "Invalid email format."})

    # Send verification email
    success, message = send_verification_email(email, request)

    if success:
        # Store email in session to track which email was sent verification
        request.session["verification_email_sent"] = email

    return JsonResponse({"success": success, "message": message})


def verify_email(request, token):
    """Handle email verification link clicks"""
    success, email, message = verify_email_token(token)

    if not success:
        messages.error(request, message)
        if request.user.is_authenticated:
            return redirect("patients:profile")
        return redirect("accounts:login")

    # Verification successful - save the email to the user
    if request.user.is_authenticated:
        # Update user's email and mark as verified
        request.user.email = email
        request.user.email_verified = True
        request.user.save()
        messages.success(request, f"تم تأكيد البريد الإلكتروني {email} بنجاح!")
        return redirect("patients:profile")

    # User not logged in - store email in session for later
    request.session["verified_email"] = email
    messages.success(
        request, f"تم تأكيد البريد الإلكتروني {email} بنجاح! يرجى تسجيل الدخول."
    )
    return redirect("accounts:login")


# ============================================
# STEP 3: Fill in registration details
# ============================================
def register_patient_details(request):
    """Third step: Fill in registration details"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    phone = request.session.get("registration_phone")
    phone_verified = request.session.get("phone_verified", False)

    if not phone or not phone_verified:
        messages.error(request, "Please verify your phone number first.")
        return redirect("accounts:register_patient_phone")

    if request.method == "POST":
        form = PatientRegistrationForm(request.POST)
        form.data = form.data.copy()
        form.data["phone"] = phone
        form._phone_pre_verified = True

        if form.is_valid():
            try:
                user = form.save(commit=False)

                # Email is optional and will be verified later from profile settings
                user.email_verified = False
                user.is_verified = True  # Phone is verified
                user.save()

                PatientProfile.objects.create(user=user)

                # Clear registration session data
                if "registration_phone" in request.session:
                    del request.session["registration_phone"]
                if "phone_verified" in request.session:
                    del request.session["phone_verified"]

                login(request, user, backend="accounts.backends.PhoneNumberAuthBackend")
                request.session["just_registered"] = True

                # Redirect to optional email step
                return redirect("accounts:register_patient_email")

            except Exception as e:
                messages.error(
                    request, f"An error occurred during registration: {str(e)}"
                )
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PatientRegistrationForm(initial={"phone": phone})

    return render(
        request,
        "accounts/register_patient_details.html",
        {"form": form},
    )


# ============================================
# STEP 4: Optional email (after account creation)
# ============================================
@login_required
def register_patient_email(request):
    """Optional step: Add email after registration"""
    user = request.user

    # Only allow newly registered patients
    if user.role != "PATIENT":
        return redirect("accounts:home")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "skip":
            # Clear just_registered flag and go to dashboard
            if "just_registered" in request.session:
                del request.session["just_registered"]
            return redirect("accounts:home")

        # Handle email submission
        email = request.POST.get("email", "").strip()

        if email:
            if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
                messages.error(request, "تنسيق البريد الإلكتروني غير صحيح.")
                return render(request, "accounts/register_patient_email.html")

            # Check if email is already used by another user
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                messages.error(request, "هذا البريد الإلكتروني مسجل بالفعل.")
                return render(request, "accounts/register_patient_email.html")

            # DON'T save email yet - only save after verification link is clicked
            # Just send the verification email
            success, message = send_verification_email(email, request)

            if success:
                messages.success(
                    request,
                    "تم إرسال رابط التأكيد إلى بريدك الإلكتروني. افتح الرابط لتأكيد بريدك.",
                )
                # Clear just_registered flag
                if "just_registered" in request.session:
                    del request.session["just_registered"]
                return redirect("accounts:home")
            else:
                messages.error(request, message)

    return render(request, "accounts/register_patient_email.html")


def register_main_doctor(request):
    """Handle main doctor registration with clinic creation"""
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = MainDoctorRegistrationForm(request.POST)
        if form.is_valid():
            try:
                user = form.save()

                activation_code_obj = form.cleaned_data["activation_code_obj"]

                clinic = Clinic.objects.create(
                    name=form.cleaned_data["clinic_name"],
                    address=form.cleaned_data["clinic_address"],
                    city=form.cleaned_data["clinic_city"],
                    phone=form.cleaned_data["clinic_phone"],
                    email=form.cleaned_data["clinic_email"],
                    specialization=form.cleaned_data["specialization"],
                    description=form.cleaned_data.get("clinic_description", ""),
                    main_doctor=user,
                )

                activation_code_obj.is_used = True
                activation_code_obj.used_by = user
                activation_code_obj.clinic_name = (
                    clinic.name
                )  # Update activation code with actual name used
                activation_code_obj.used_at = timezone.now()
                activation_code_obj.save()

                login(request, user, backend="accounts.backends.PhoneNumberAuthBackend")
                request.session["just_registered"] = True

                messages.success(
                    request,
                    f'Welcome, Dr. {user.name}! Your clinic "{clinic.name}" has been successfully created.',
                )

                return redirect("accounts:home")

            except Exception as e:
                messages.error(
                    request,
                    f"An error occurred during registration. Please try again. Error: {str(e)}",
                )
        else:
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


def landing_page(request):
    """Render the public landing page"""
    return render(request, "accounts/landing_page.html")


@login_required
def change_phone_request(request):
    """Initiate phone number change: Enter new phone and send OTP"""
    if request.method == "POST":
        new_phone = request.POST.get("phone", "").strip()

        new_phone = PhoneNumberAuthBackend.normalize_phone_number(new_phone)

        # 1. Validate format
        if not PhoneNumberAuthBackend.is_valid_phone_number(new_phone):
            messages.error(
                request,
                "رقم الهاتف غير صحيح. يجب أن يبدأ بـ 059 أو 056 ويتكون من 10 أرقام.",
            )
            return render(request, "accounts/change_phone_request.html")

        # 2. Check uniqueness
        if request.user.phone == new_phone:
            messages.error(request, "لقد أدخلت رقم هاتفك الحالي.")
            return render(request, "accounts/change_phone_request.html")

        if User.objects.filter(phone=new_phone).exists():
            messages.error(request, "رقم الهاتف هذا مسجل بالفعل.")
            return render(request, "accounts/change_phone_request.html")

        # 3. Request OTP
        success, message = request_otp(new_phone)
        if success:
            request.session["change_phone_new"] = new_phone
            messages.success(request, message)
            return redirect("accounts:change_phone_verify")
        else:
            messages.error(request, message)

    return render(request, "accounts/change_phone_request.html")


@login_required
def change_phone_verify(request):
    """Verify OTP and update phone number"""
    new_phone = request.session.get("change_phone_new")
    if not new_phone:
        messages.error(request, "انتهت الجلسة. يرجى المحاولة مرة أخرى.")
        return redirect("accounts:change_phone_request")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "resend":
            success, message = request_otp(new_phone)
            if success:
                messages.success(request, message)
            else:
                messages.error(request, message)
            return redirect("accounts:change_phone_verify")

        otp = request.POST.get("otp", "").strip()

        # Verify
        success, message = verify_otp(new_phone, otp)

        if success:
            # Update user
            request.user.phone = new_phone
            request.user.is_verified = True
            request.user.save()

            # Clear session
            if "change_phone_new" in request.session:
                del request.session["change_phone_new"]

            messages.success(request, "تم تحديث رقم الهاتف بنجاح.")
            return redirect("patients:profile")
        else:
            messages.error(request, message)

    return render(request, "accounts/change_phone_verify.html", {"phone": new_phone})


@login_required
def change_email_request(request):
    """Initiate email change: Enter new email and send verification"""

    # Get the current email to show in the form
    current_email = request.user.email or ""

    # Pre-fill with pending email if coming from edit profile
    pending_email = request.session.get("pending_email_change", "")

    if request.method == "POST":
        new_email = request.POST.get("email", "").strip()

        # 1. Basic validation
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", new_email):
            messages.error(request, "تنسيق البريد الإلكتروني غير صحيح.")
            return render(
                request,
                "accounts/change_email_request.html",
                {"current_email": current_email, "pending_email": pending_email},
            )

        # 2. Check uniqueness
        if request.user.email and request.user.email.lower() == new_email.lower():
            messages.error(request, "لقد أدخلت بريدك الإلكتروني الحالي.")
            return render(
                request,
                "accounts/change_email_request.html",
                {"current_email": current_email, "pending_email": pending_email},
            )

        if (
            User.objects.filter(email__iexact=new_email)
            .exclude(pk=request.user.pk)
            .exists()
        ):
            messages.error(request, "البريد الإلكتروني هذا مسجل بالفعل.")
            return render(
                request,
                "accounts/change_email_request.html",
                {"current_email": current_email, "pending_email": pending_email},
            )

        # 3. Send Verification Email
        success, message = send_change_email_verification(new_email, request)

        if success:
            # Store intended email in session
            request.session["change_email_pending"] = new_email
            # Clear the pending_email_change flag
            if "pending_email_change" in request.session:
                del request.session["pending_email_change"]
            return render(
                request, "accounts/change_email_sent.html", {"email": new_email}
            )
        else:
            messages.error(request, message)

    return render(
        request,
        "accounts/change_email_request.html",
        {"current_email": current_email, "pending_email": pending_email},
    )


@login_required
def verify_change_email(request, token):
    """Verify email change token and update user email"""
    success, email, message = verify_email_token(token)

    if success:
        # Update user's email
        request.user.email = email
        request.user.email_verified = True
        request.user.save()

        # Cleanup session
        if "change_email_pending" in request.session:
            del request.session["change_email_pending"]

        messages.success(request, f"تم تحديث البريد الإلكتروني بنجاح إلى {email}!")
        return redirect("patients:profile")
    else:
        messages.error(request, message)
        return redirect("accounts:change_email_request")
