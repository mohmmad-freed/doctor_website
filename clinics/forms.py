from django import forms
from django.utils import timezone

from accounts.models import City, CustomUser
from accounts.services.identity_claim_service import get_verified_claim_for_user
from doctors.models import Specialty
from .models import ClinicActivationCode


class AddClinicCodeForm(forms.Form):
    """Step 1: Logged-in clinic owner enters a new activation code."""

    activation_code = forms.CharField(
        max_length=20,
        label="رمز التفعيل",
        widget=forms.TextInput(attrs={"placeholder": "أدخل رمز التفعيل الجديد"}),
    )

    def __init__(self, *args, user_phone=None, user_national_id=None, **kwargs):
        self.user_phone = user_phone
        self.user_national_id = user_national_id
        super().__init__(*args, **kwargs)

    def clean_activation_code(self):
        code = (self.cleaned_data.get("activation_code") or "").strip()
        _GENERIC_ERROR = "رمز التفعيل غير صالح أو منتهي الصلاحية"

        try:
            ac = ClinicActivationCode.objects.get(code=code)
        except ClinicActivationCode.DoesNotExist:
            raise forms.ValidationError(_GENERIC_ERROR)

        if ac.is_used:
            raise forms.ValidationError(_GENERIC_ERROR)

        if ac.expires_at and ac.expires_at < timezone.now():
            raise forms.ValidationError(_GENERIC_ERROR)

        if ac.phone and self.user_phone and ac.phone != self.user_phone:
            raise forms.ValidationError(_GENERIC_ERROR)

        verified_claim = None
        if self.user_phone:
            user = CustomUser.objects.filter(phone=self.user_phone).first()
            if user:
                verified_claim = get_verified_claim_for_user(user)

        if ac.national_id and verified_claim and ac.national_id != verified_claim.national_id:
            raise forms.ValidationError(_GENERIC_ERROR)

        self.cleaned_data["_activation_code_obj"] = ac
        return code


class AddClinicDetailsForm(forms.Form):
    """Step 2: Logged-in clinic owner fills in clinic details."""

    clinic_name = forms.CharField(max_length=255, label="اسم العيادة")
    clinic_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        label="العنوان التفصيلي",
    )
    clinic_city = forms.ModelChoiceField(
        queryset=City.objects.all(),
        required=False,
        label="المدينة",
        empty_label="اختر المدينة",
    )
    specialties = forms.ModelMultipleChoiceField(
        queryset=Specialty.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        label="التخصصات الطبية",
    )
    clinic_phone = forms.CharField(
        max_length=20,
        required=False,
        label="هاتف العيادة",
        widget=forms.TextInput(attrs={"placeholder": "05XXXXXXXX"}),
    )
    clinic_email = forms.EmailField(
        required=False,
        label="البريد الإلكتروني للعيادة",
        widget=forms.EmailInput(attrs={"placeholder": "clinic@example.com"}),
    )
    clinic_description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="وصف العيادة",
    )

from accounts.backends import PhoneNumberAuthBackend

class ClinicInvitationForm(forms.Form):
    doctor_name = forms.CharField(
        max_length=255, 
        label="اسم الطبيب",
        widget=forms.TextInput(attrs={"placeholder": "ادخل اسم الطبيب", "class": "form-control"})
    )
    doctor_phone = forms.CharField(
        max_length=20, 
        label="رقم هاتف الطبيب",
        widget=forms.TextInput(attrs={"placeholder": "05XXXXXXXX", "class": "form-control"})
    )
    doctor_email = forms.EmailField(
        label="البريد الإلكتروني للطبيب",
        widget=forms.EmailInput(attrs={"placeholder": "doctor@example.com", "class": "form-control"})
    )
    doctor_national_id = forms.CharField(
        max_length=20,
        label="رقم الهوية الوطنية",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "أدخل رقم الهوية الوطنية", "class": "form-control"})
    )
    specialties = forms.ModelMultipleChoiceField(
        queryset=Specialty.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        label="التخصصات الطبية",
        required=False
    )
    
    def clean_doctor_phone(self):
        phone = self.cleaned_data.get("doctor_phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)
        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
             raise forms.ValidationError("رقم الهاتف غير صحيح. يجب أن يتكون من 10 أرقام ويبدأ بـ 05.")
        return phone


class SecretaryInvitationForm(forms.Form):
    secretary_name = forms.CharField(
        max_length=255, 
        label="اسم السكرتير/ة",
        widget=forms.TextInput(attrs={"placeholder": "ادخل اسم السكرتير أو الاستقبال", "class": "form-control"})
    )
    secretary_phone = forms.CharField(
        max_length=20, 
        label="رقم هاتف السكرتير/ة",
        widget=forms.TextInput(attrs={"placeholder": "05XXXXXXXX", "class": "form-control"})
    )
    secretary_email = forms.EmailField(
        label="البريد الإلكتروني للسكرتير/ة",
        widget=forms.EmailInput(attrs={"placeholder": "secretary@example.com", "class": "form-control"})
    )
    secretary_national_id = forms.CharField(
        max_length=20,
        label="رقم الهوية الوطنية",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "أدخل رقم الهوية الوطنية", "class": "form-control"})
    )
    
    def clean_secretary_phone(self):
        phone = self.cleaned_data.get("secretary_phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)
        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
             raise forms.ValidationError("رقم الهاتف غير صحيح. يجب أن يتكون من 10 أرقام ويبدأ بـ 05.")
        return phone

