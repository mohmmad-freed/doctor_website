from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import CustomUser, City
from clinics.models import ClinicActivationCode
from doctors.models import Specialty
from .backends import PhoneNumberAuthBackend
import re


class LoginForm(forms.Form):
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "059XXXXXXX or +97059XXXXXXX"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Enter your password"})
    )

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        return PhoneNumberAuthBackend.normalize_phone_number(phone)


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
        fields = ["name", "phone", "national_id", "city"]

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) < 3:
            raise ValidationError("Name must be at least 3 characters long.")
        if not re.search(r"[a-zA-Z\u0600-\u06FF]", name):
            raise ValidationError("Name must contain at least one letter.")
        return name

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            raise ValidationError(
                "Invalid phone number format. Phone must start with 059 or 056 and be 10 digits."
            )

        # Skip duplicate check if phone is already verified via OTP
        if not getattr(self, "_phone_pre_verified", False):
            if CustomUser.objects.filter(phone=phone).exists():
                raise ValidationError("This phone number is already registered.")

        return phone

    def clean_national_id(self):
        national_id = (self.cleaned_data.get("national_id") or "").strip()
        national_id = national_id.replace(" ", "").replace("-", "")

        if national_id and not re.match(r"^\d{9}$", national_id):
            raise ValidationError("National ID must be exactly 9 digits.")

        if national_id and CustomUser.objects.filter(national_id=national_id).exists():
            raise ValidationError("This national ID is already registered.")

        return national_id or None

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            return None

        if CustomUser.objects.filter(email=email).exists():
            raise ValidationError("This email is already registered.")

        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        if password1 and len(password1) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        return password1

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match.")

        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.role = "PATIENT"
        if commit:
            user.save()
        return user


class MainDoctorRegistrationForm(forms.ModelForm):
    # — Activation —
    activation_code = forms.CharField(max_length=20, required=True, label="كود التفعيل")

    # — Owner info —
    first_name = forms.CharField(max_length=100, required=True, label="الاسم الأول")
    last_name = forms.CharField(max_length=100, required=True, label="الاسم الأخير")
    phone = forms.CharField(max_length=20, required=True, label="رقم الهاتف")
    national_id = forms.CharField(max_length=9, required=True, label="رقم الهوية الوطنية")
    email = forms.EmailField(required=True, label="البريد الإلكتروني")
    password = forms.CharField(
        widget=forms.PasswordInput, required=True, label="كلمة المرور"
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput, required=True, label="تأكيد كلمة المرور"
    )

    # — Clinic info —
    clinic_name = forms.CharField(max_length=255, required=True, label="اسم العيادة")
    clinic_phone = forms.CharField(max_length=20, required=True, label="هاتف العيادة")
    clinic_email = forms.EmailField(required=False, label="بريد العيادة الإلكتروني (اختياري)")
    clinic_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=True,
        label="العنوان التفصيلي",
    )
    clinic_city = forms.ModelChoiceField(
        queryset=City.objects.all(),
        required=True,
        label="المدينة",
        empty_label="اختر المدينة",
    )
    specialties = forms.ModelMultipleChoiceField(
        queryset=Specialty.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="التخصصات الطبية",
    )
    clinic_description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="وصف العيادة"
    )

    class Meta:
        model = CustomUser
        fields = [
            "first_name",
            "last_name",
            "phone",
            "national_id",
            "email",
            "password",
            "confirm_password",
        ]

    def clean_name_field(self, value, label):
        value = (value or "").strip()
        if len(value) < 2:
            raise ValidationError(f"{label} يجب أن يكون حرفين على الأقل.")
        if not re.search(r"[a-zA-Z\u0600-\u06FF]", value):
            raise ValidationError(f"{label} يجب أن يحتوي على حروف.")
        return value

    def clean_first_name(self):
        return self.clean_name_field(self.cleaned_data.get("first_name"), "الاسم الأول")

    def clean_last_name(self):
        return self.clean_name_field(self.cleaned_data.get("last_name"), "الاسم الأخير")

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            raise ValidationError(
                "رقم الهاتف غير صحيح. يجب أن يبدأ بـ 059 أو 056 ويتكون من 10 أرقام."
            )

        if CustomUser.objects.filter(phone=phone).exists():
            raise ValidationError("رقم الهاتف هذا مسجل بالفعل.")

        return phone

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        nid = nid.replace(" ", "").replace("-", "")

        if not re.match(r"^\d{9}$", nid):
            raise ValidationError("رقم الهوية يجب أن يتكون من 9 أرقام فقط.")

        if CustomUser.objects.filter(national_id=nid).exists():
            raise ValidationError("رقم الهوية الوطنية هذا مسجل بالفعل.")

        return nid

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if not email:
            return email
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError("البريد الإلكتروني هذا مسجل بالفعل.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if len(password) < 8:
            raise ValidationError("كلمة المرور يجب أن تكون 8 أحرف على الأقل.")
        return password

    def clean_confirm_password(self):
        password = self.cleaned_data.get("password")
        confirm_password = self.cleaned_data.get("confirm_password")

        if password and confirm_password and password != confirm_password:
            raise ValidationError("كلمتا المرور غير متطابقتين.")

        return confirm_password

    def clean_clinic_phone(self):
        phone = self.cleaned_data.get("clinic_phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            raise ValidationError(
                "هاتف العيادة غير صحيح. يجب أن يبدأ بـ 059 أو 056 ويتكون من 10 أرقام."
            )

        return phone

    def clean_activation_code(self):
        code = self.cleaned_data.get("activation_code", "").strip()

        try:
            activation_code = ClinicActivationCode.objects.get(code=code, is_used=False)
        except ClinicActivationCode.DoesNotExist:
            if ClinicActivationCode.objects.filter(code=code).exists():
                raise ValidationError("كود التفعيل هذا تم استخدامه مسبقاً.")
            raise ValidationError("كود التفعيل غير صحيح. يرجى التحقق والمحاولة مرة أخرى.")

        # Check expiry
        if activation_code.expires_at and activation_code.expires_at < timezone.now():
            raise ValidationError("انتهت صلاحية كود التفعيل.")

        # Validate phone matches (only if code has a phone assigned)
        phone = self.cleaned_data.get("phone")
        if phone and activation_code.phone and phone != activation_code.phone:
            raise ValidationError("رقم الهاتف لا يتطابق مع كود التفعيل.")

        # Validate national_id matches (only if code has a national_id assigned)
        nid = self.cleaned_data.get("national_id")
        if nid and activation_code.national_id and nid != activation_code.national_id:
            raise ValidationError("رقم الهوية لا يتطابق مع كود التفعيل.")

        self.cleaned_data["activation_code_obj"] = activation_code
        return code

    def save(self, commit=True):
        user = super().save(commit=False)
        user.name = f"{self.cleaned_data['first_name']} {self.cleaned_data['last_name']}".strip()
        user.phone = self.cleaned_data["phone"]
        user.national_id = self.cleaned_data["national_id"]
        user.email = self.cleaned_data["email"]
        user.role = "MAIN_DOCTOR"
        user.is_verified = True
        user.set_password(self.cleaned_data["password"])

        if commit:
            user.save()
        return user


class ForgotPasswordPhoneForm(forms.Form):
    """Form for entering phone number during password reset"""

    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "059XXXXXXX"}),
    )

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            raise ValidationError(
                "رقم الهاتف غير صحيح. يجب أن يبدأ بـ 059 أو 056 ويتكون من 10 أرقام."
            )

        if not CustomUser.objects.filter(phone=phone).exists():
            raise ValidationError("لا يوجد حساب مرتبط بهذا الرقم.")

        return phone


class ResetPasswordForm(forms.Form):
    """Form for setting a new password during password reset"""

    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "كلمة المرور الجديدة"}),
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "تأكيد كلمة المرور"}),
    )

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        if password1 and len(password1) < 8:
            raise ValidationError("يجب أن تتكون كلمة المرور من 8 أحرف على الأقل.")
        return password1

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            raise ValidationError("كلمتا المرور غير متطابقتين.")

        return password2
