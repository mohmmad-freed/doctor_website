from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import CustomUser, City
from clinics.models import ClinicActivationCode
import re


class LoginForm(forms.Form):
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'placeholder': '059XXXXXXX or +97059XXXXXXX'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Enter your password'})
    )

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        return self.normalize_phone_number(phone)

    @staticmethod
    def normalize_phone_number(phone):
        phone = phone.strip().replace(' ', '').replace('-', '')
        if phone.startswith('+97059'):
            phone = '059' + phone[6:]
        elif phone.startswith('+97056'):
            phone = '056' + phone[6:]
        return phone


class PatientRegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput,
    )

    class Meta:
        model = CustomUser
        fields = ['name', 'phone', 'national_id', 'city', 'email']

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if len(name) < 3:
            raise ValidationError("Name must be at least 3 characters long.")
        if not re.search(r'[a-zA-Z\u0600-\u06FF]', name):
            raise ValidationError("Name must contain at least one letter.")
        return name

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        phone = self.normalize_phone_number(phone)

        if not self.is_valid_phone_number(phone):
            raise ValidationError(
                "Invalid phone number format. Phone must start with 059 or 056 and be 10 digits."
            )

        # Skip duplicate check if phone is already verified via OTP
        if not getattr(self, '_phone_pre_verified', False):
            if CustomUser.objects.filter(phone=phone).exists():
                raise ValidationError("This phone number is already registered.")

        return phone

    def clean_national_id(self):
        national_id = (self.cleaned_data.get('national_id') or '').strip()
        national_id = national_id.replace(' ', '').replace('-', '')

        if national_id and not re.match(r'^\d{9}$', national_id):
            raise ValidationError("National ID must be exactly 9 digits.")

        if national_id and CustomUser.objects.filter(national_id=national_id).exists():
            raise ValidationError("This national ID is already registered.")

        return national_id or None

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            return None

        if CustomUser.objects.filter(email=email).exists():
            raise ValidationError("This email is already registered.")

        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1 and len(password1) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        return password1

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')

        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match.")

        return password2

    @staticmethod
    def normalize_phone_number(phone):
        phone = phone.replace(' ', '').replace('-', '')
        if phone.startswith('+97059'):
            phone = '059' + phone[6:]
        elif phone.startswith('+97056'):
            phone = '056' + phone[6:]
        return phone

    @staticmethod
    def is_valid_phone_number(phone):
        return bool(re.match(r'^(059|056)\d{7}$', phone))

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.role = 'PATIENT'
        if commit:
            user.save()
        return user


class MainDoctorRegistrationForm(UserCreationForm):
    name = forms.CharField(max_length=255, required=True)
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=20, required=True)
    activation_code = forms.CharField(max_length=20, required=True)

    clinic_address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=True)
    clinic_phone = forms.CharField(max_length=20, required=True)
    clinic_email = forms.EmailField(required=True)
    clinic_description = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False)

    class Meta:
        model = CustomUser
        fields = ['name', 'email', 'phone', 'password1', 'password2']

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        phone = self.normalize_phone_number(phone)

        if not self.is_valid_phone_number(phone):
            raise ValidationError(
                "Invalid phone number format. Phone must start with 059 or 056 and be 10 digits."
            )

        if CustomUser.objects.filter(phone=phone).exists():
            raise ValidationError("This phone number is already registered.")

        return phone

    def clean_clinic_phone(self):
        phone = self.cleaned_data.get('clinic_phone', '').strip()
        phone = self.normalize_phone_number(phone)

        if not self.is_valid_phone_number(phone):
            raise ValidationError(
                "Invalid clinic phone number format. Phone must start with 059 or 056 and be 10 digits."
            )

        return phone

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if CustomUser.objects.filter(email=email).exists():
            raise ValidationError("This email is already registered.")
        return email

    def clean_activation_code(self):
        code = self.cleaned_data.get('activation_code', '').strip()

        try:
            activation_code = ClinicActivationCode.objects.get(code=code, is_used=False)
            self.cleaned_data['activation_code_obj'] = activation_code
            return code
        except ClinicActivationCode.DoesNotExist:
            if ClinicActivationCode.objects.filter(code=code).exists():
                raise ValidationError("This activation code has already been used.")
            else:
                raise ValidationError("Invalid activation code. Please check and try again.")

    @staticmethod
    def normalize_phone_number(phone):
        phone = phone.replace(' ', '').replace('-', '')
        if phone.startswith('+97059'):
            phone = '059' + phone[6:]
        elif phone.startswith('+97056'):
            phone = '056' + phone[6:]
        return phone

    @staticmethod
    def is_valid_phone_number(phone):
        return bool(re.match(r'^(059|056)\d{7}$', phone))

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'MAIN_DOCTOR'
        if commit:
            user.save()
        return user