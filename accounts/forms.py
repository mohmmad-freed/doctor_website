from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import CustomUser
from clinics.models import ClinicActivationCode


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'your@email.com'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Enter your password'})
    )


class PatientRegistrationForm(UserCreationForm):
    name = forms.CharField(max_length=255, required=True)
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=20, required=False)
    
    class Meta:
        model = CustomUser
        fields = ['name', 'email', 'phone', 'password1', 'password2']
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'PATIENT'
        if commit:
            user.save()
        return user


class MainDoctorRegistrationForm(UserCreationForm):
    name = forms.CharField(max_length=255, required=True)
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=20, required=True)
    activation_code = forms.CharField(max_length=20, required=True)
    
    # Clinic information
    clinic_address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=True)
    clinic_phone = forms.CharField(max_length=20, required=True)
    clinic_email = forms.EmailField(required=True)
    clinic_description = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False)
    
    class Meta:
        model = CustomUser
        fields = ['name', 'email', 'phone', 'password1', 'password2']
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email
    
    def clean_activation_code(self):
        code = self.cleaned_data.get('activation_code', '').strip()  # Removed .upper()
    
        try:
            activation_code = ClinicActivationCode.objects.get(code=code, is_used=False)
            self.cleaned_data['activation_code_obj'] = activation_code
            return code
        except ClinicActivationCode.DoesNotExist:
        # More helpful error message
            if ClinicActivationCode.objects.filter(code=code).exists():
                raise forms.ValidationError("This activation code has already been used.")
            else:
                raise forms.ValidationError("Invalid activation code. Please check and try again.")
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'MAIN_DOCTOR'
        if commit:
            user.save()
        return user