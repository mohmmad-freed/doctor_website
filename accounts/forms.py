from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import CustomUser, City
from clinics.models import ClinicActivationCode
from doctors.models import Specialty
from .backends import PhoneNumberAuthBackend
import re


# ============================================================
# 3-STAGE CLINIC OWNER REGISTRATION FORMS
# ============================================================

class ClinicRegStep1Form(forms.Form):
    """Stage 1: Activation code + identity verification."""

    activation_code = forms.CharField(
        max_length=20,
        required=True,
        label="كود التفعيل",
        help_text="أدخل كود التفعيل المقدم من قِبل الإدارة",
        widget=forms.TextInput(attrs={"placeholder": "XXXX-XXXX-XXXX"}),
    )
    phone = forms.CharField(
        max_length=20,
        required=True,
        label="رقم الهاتف",
        widget=forms.TextInput(attrs={"placeholder": "059XXXXXXX"}),
    )
    national_id = forms.CharField(
        max_length=9,
        required=True,
        label="رقم الهوية الوطنية",
        widget=forms.TextInput(attrs={"placeholder": "9 أرقام"}),
    )

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        phone = PhoneNumberAuthBackend.normalize_phone_number(phone)
        if not PhoneNumberAuthBackend.is_valid_phone_number(phone):
            raise ValidationError(
                "رقم الهاتف غير صحيح. يجب أن يبدأ بـ 059 أو 056 ويتكون من 10 أرقام."
            )
        return phone

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        nid = nid.replace(" ", "").replace("-", "")
        if not re.match(r"^\d{9}$", nid):
            raise ValidationError("رقم الهوية يجب أن يتكون من 9 أرقام فقط.")
        return nid

    def clean_activation_code(self):
        code = (self.cleaned_data.get("activation_code") or "").strip()
        _GENERIC_CODE_ERROR = "رمز التفعيل غير صالح أو منتهي الصلاحية"

        try:
            ac = ClinicActivationCode.objects.get(code=code)
        except ClinicActivationCode.DoesNotExist:
            raise ValidationError(_GENERIC_CODE_ERROR)

        if ac.is_used:
            raise ValidationError(_GENERIC_CODE_ERROR)

        if ac.expires_at and ac.expires_at < timezone.now():
            raise ValidationError(_GENERIC_CODE_ERROR)

        self.cleaned_data["_activation_code_obj"] = ac
        return code

    def clean(self):
        cleaned_data = super().clean()

        # Skip cross-field checks if field-level errors already exist.
        if self.errors:
            return cleaned_data

        phone = cleaned_data.get("phone")
        nid = cleaned_data.get("national_id")
        ac = cleaned_data.get("_activation_code_obj")

        # Validate phone/NID match the activation code's assigned values.
        if ac and phone and ac.phone and ac.phone != phone:
            self.add_error("activation_code", "رمز التفعيل غير صالح أو منتهي الصلاحية")
        if ac and nid and ac.national_id and ac.national_id != nid:
            self.add_error("activation_code", "رمز التفعيل غير صالح أو منتهي الصلاحية")

        if self.errors:
            return cleaned_data

        # Cross-field: if the NID exists in DB but is linked to a DIFFERENT phone,
        # the user may have given the admin the wrong phone → reject without revealing why.
        if nid and phone:
            existing_by_nid = CustomUser.objects.filter(national_id=nid).first()
            if existing_by_nid and existing_by_nid.phone != phone:
                _msg = "تأكد من صحة المعلومات المُدخلة"
                self.add_error("phone", _msg)
                self.add_error("national_id", _msg)

        return cleaned_data


class ClinicRegStep2NewUserForm(forms.Form):
    """Stage 2 (new user path): personal info + password."""

    first_name = forms.CharField(max_length=100, required=True, label="الاسم الأول")
    last_name = forms.CharField(max_length=100, required=True, label="الاسم الأخير")
    email = forms.EmailField(required=True, label="البريد الإلكتروني")
    password = forms.CharField(
        widget=forms.PasswordInput, required=True, label="كلمة المرور"
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput, required=True, label="تأكيد كلمة المرور"
    )

    def __init__(self, *args, phone=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._phone = phone  # used to exclude this phone's user from email uniqueness check

    def _clean_name_field(self, value, label):
        value = (value or "").strip()
        if len(value) < 2:
            raise ValidationError(f"{label} يجب أن يكون حرفين على الأقل.")
        if not re.search(r"[a-zA-Z\u0600-\u06FF]", value):
            raise ValidationError(f"{label} يجب أن يحتوي على حروف.")
        return value

    def clean_first_name(self):
        return self._clean_name_field(self.cleaned_data.get("first_name"), "الاسم الأول")

    def clean_last_name(self):
        return self._clean_name_field(self.cleaned_data.get("last_name"), "الاسم الأخير")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self._phone:
            qs = qs.exclude(phone=self._phone)
        if qs.exists():
            raise ValidationError("البريد الإلكتروني هذا مسجل بالفعل.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        errors = []
        if len(password) < 8:
            errors.append("8 أحرف على الأقل")
        if not re.search(r"[A-Z]", password):
            errors.append("حرف كبير واحد على الأقل")
        if not re.search(r"[a-z]", password):
            errors.append("حرف صغير واحد على الأقل")
        if not re.search(r"\d", password):
            errors.append("رقم واحد على الأقل")
        if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>?/\\|`~]", password):
            errors.append("رمز خاص واحد على الأقل")
        if errors:
            raise ValidationError(f"كلمة المرور يجب أن تحتوي على: {' • '.join(errors)}.")
        return password

    def clean_confirm_password(self):
        password = self.cleaned_data.get("password")
        confirm = self.cleaned_data.get("confirm_password")
        if password and confirm and password != confirm:
            raise ValidationError("كلمتا المرور غير متطابقتين.")
        return confirm


class ClinicRegStep2EmailOnlyForm(forms.Form):
    """Stage 2 (existing user without email path): collect email only."""

    email = forms.EmailField(required=True, label="البريد الإلكتروني")

    def __init__(self, *args, phone=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._phone = phone

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self._phone:
            qs = qs.exclude(phone=self._phone)
        if qs.exists():
            raise ValidationError("البريد الإلكتروني هذا مسجل بالفعل.")
        return email


class ClinicRegStep3Form(forms.Form):
    """Stage 3: Clinic information (no phone/email — added from dashboard)."""

    clinic_name = forms.CharField(max_length=255, required=True, label="اسم العيادة")
    clinic_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=True,
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
        required=True,
        label="التخصصات الطبية",
    )
    clinic_description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="وصف العيادة",
    )


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
        user.roles = ["PATIENT"]
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
        required=False,
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

        # No uniqueness check here — existing users are reused in save()
        return phone

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        nid = nid.replace(" ", "").replace("-", "")

        if not re.match(r"^\d{9}$", nid):
            raise ValidationError("رقم الهوية يجب أن يتكون من 9 أرقام فقط.")

        return nid

    def clean_email(self):
        return self.cleaned_data.get("email", "").strip()

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        errors = []
        if len(password) < 8:
            errors.append("8 أحرف على الأقل")
        if not re.search(r"[A-Z]", password):
            errors.append("حرف كبير واحد على الأقل")
        if not re.search(r"[a-z]", password):
            errors.append("حرف صغير واحد على الأقل")
        if not re.search(r"\d", password):
            errors.append("رقم واحد على الأقل")
        if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>?/\\|`~]", password):
            errors.append("رمز خاص واحد على الأقل")
        if errors:
            raise ValidationError(f"كلمة المرور يجب أن تحتوي على: {' • '.join(errors)}.")
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

    def clean(self):
        """
        Cross-field uniqueness checks for national_id and email.

        These run here (after clean_activation_code) so we can suppress them
        entirely when the activation code itself is invalid — showing NID/email
        "already registered" errors while the real problem is a phone mismatch
        or a wrong code would be misleading noise.
        """
        cleaned_data = super().clean()

        # Skip uniqueness checks when activation_code already has an error.
        if "activation_code" in self.errors:
            return cleaned_data

        phone = cleaned_data.get("phone")
        existing_user = CustomUser.objects.filter(phone=phone).first() if phone else None

        # ── NID uniqueness ────────────────────────────────────────────────
        nid = cleaned_data.get("national_id")
        if nid:
            if existing_user:
                if existing_user.national_id and existing_user.national_id != nid:
                    self.add_error(
                        "national_id", "رقم الهوية لا يتطابق مع بيانات حسابك الحالي."
                    )
                elif CustomUser.objects.filter(national_id=nid).exclude(
                    pk=existing_user.pk
                ).exists():
                    self.add_error("national_id", "رقم الهوية الوطنية هذا مسجل بالفعل.")
            else:
                if CustomUser.objects.filter(national_id=nid).exists():
                    self.add_error("national_id", "رقم الهوية الوطنية هذا مسجل بالفعل.")

        # ── Email uniqueness ──────────────────────────────────────────────
        email = cleaned_data.get("email", "")
        if email:
            qs = CustomUser.objects.filter(email__iexact=email)
            if phone:
                qs = qs.exclude(phone=phone)
            if qs.exists():
                self.add_error("email", "البريد الإلكتروني هذا مسجل بالفعل.")

        return cleaned_data

    def _post_clean(self):
        """
        If a user with the submitted phone already exists, swap self.instance to that
        user BEFORE Django's construct_instance / validate_unique run.  This ensures
        the DB-level unique check on `phone` (and `national_id`) is evaluated
        against the existing row — not against a phantom new record — so validation
        passes correctly for the reuse path.

        We also snapshot the original email / name / national_id values here so that
        save() can honour the "fill missing fields only" rule even after
        construct_instance has overwritten those attributes in memory.
        """
        phone = self.cleaned_data.get("phone")
        if phone:
            existing = CustomUser.objects.filter(phone=phone).first()
            if existing:
                self._existing_original = {
                    "email": existing.email,
                    "name": existing.name,
                    "national_id": existing.national_id,
                }
                self.instance = existing

        # Snapshot NID errors before model-level validate_unique() adds its own.
        nid_errors_before = list(self._errors.get("national_id", []))

        super()._post_clean()

        # If the activation code itself has an error (e.g. phone mismatch), discard
        # any national_id uniqueness error added by validate_unique() — showing
        # "NID already exists" when the real problem is a bad activation code is noise.
        if "activation_code" in self.errors:
            if nid_errors_before:
                self._errors["national_id"] = self.error_class(nid_errors_before)
            elif "national_id" in self._errors:
                del self._errors["national_id"]

    def save(self, commit=True):
        user = self.instance  # either the existing user (set in _post_clean) or a new instance
        password = self.cleaned_data["password"]
        original = getattr(self, "_existing_original", None)

        if original is not None:
            # ── Existing user: add MAIN_DOCTOR role while keeping all prior roles ──
            user.role = "MAIN_DOCTOR"
            user.set_password(password)
            user.is_verified = True
            # Preserve existing roles and ensure both PATIENT and MAIN_DOCTOR are present
            existing_roles = list(user.roles or [])
            for r in ("PATIENT", "MAIN_DOCTOR"):
                if r not in existing_roles:
                    existing_roles.append(r)
            user.roles = existing_roles
            # construct_instance already set fields from form data; selectively
            # restore values that must NOT be overwritten.
            if original["email"]:
                user.email = original["email"]        # keep existing email
            if original["national_id"]:
                user.national_id = original["national_id"]  # keep existing national_id
            if original["name"]:
                user.name = original["name"]          # keep existing name
            else:
                # No name was ever set — fill it from the form
                user.name = (
                    f"{self.cleaned_data['first_name']} {self.cleaned_data['last_name']}".strip()
                )
        else:
            # ── New user: set all required fields ──────────────────────────────
            user.name = (
                f"{self.cleaned_data['first_name']} {self.cleaned_data['last_name']}".strip()
            )
            user.role = "MAIN_DOCTOR"
            user.is_verified = True
            user.set_password(password)
            # New clinic owners always hold both PATIENT and MAIN_DOCTOR roles
            user.roles = ["PATIENT", "MAIN_DOCTOR"]

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
