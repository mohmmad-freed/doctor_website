from django import forms
from django.contrib.auth import get_user_model
from .models import PatientProfile
from accounts.models import City
from accounts.services.identity_claim_service import normalize_national_id, validate_national_id

User = get_user_model()


class UserUpdateForm(forms.ModelForm):
    def clean_national_id(self):
        national_id = self.cleaned_data.get("national_id")
        if not national_id:
            return ""
        return validate_national_id(normalize_national_id(national_id))

    class Meta:
        model = User
        fields = ["name",  "national_id", "city"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
           
            "national_id": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
            "city": forms.Select(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
        }
        labels = {
            "name": "الاسم الكامل",
            
            "national_id": "رقم الهوية",
            "city": "المدينة",
        }


class PatientProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = PatientProfile
        fields = [
            "avatar",
            "date_of_birth",
            "gender",
            "blood_type",
            "allergies",
            "medical_history",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]
        widgets = {
            "avatar": forms.FileInput(attrs={"class": "hidden", "id": "id_avatar"}),
            "date_of_birth": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white datepicker",
                    "placeholder": "يوم/شهر/سنة",
                }
            ),
            "gender": forms.Select(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
            "blood_type": forms.Select(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
            "allergies": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white",
                }
            ),
            "medical_history": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white",
                }
            ),
            "emergency_contact_name": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
            "emergency_contact_phone": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
                }
            ),
        }
        labels = {
            "date_of_birth": "تاريخ الميلاد",
            "gender": "الجنس",
            "blood_type": "فصيلة الدم",
            "allergies": "الحساسية",
            "medical_history": "التاريخ الطبي (قائمة التشخيصات، العمليات الجراحية، إلخ)",
            "emergency_contact_name": "اسم جهة اتصال الطوارئ",
            "emergency_contact_phone": "رقم جهة اتصال الطوارئ",
        }
