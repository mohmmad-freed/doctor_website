from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from accounts.models import CustomUser, City
from accounts.forms import PatientRegistrationForm, LoginForm, MainDoctorRegistrationForm
from clinics.models import Clinic, ClinicActivationCode, ClinicStaff, ClinicSubscription, ClinicVerification
from clinics.services import create_clinic_for_main_doctor
from doctors.models import Specialty
from patients.models import PatientProfile
from patients.services import ensure_patient_profile


class PhoneNumberValidationTest(TestCase):
    """Test phone number validation"""
    
    def setUp(self):
        self.city = City.objects.create(name="Nablus")
    
    def test_valid_phone_formats(self):
        """Test that both phone formats are accepted"""
        # Test format 1: 0594073157
        form_data = {
            'name': 'Test User',
            'phone': '0594073157',
            'national_id': '123456789',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        }
        form = PatientRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), f"Form should be valid but has errors: {form.errors}")
        
        # Test format 2: +970594073157
        form_data['phone'] = '+970594073158'  # Different number
        form_data['national_id'] = '987654321'  # Different ID
        form = PatientRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), f"Form should be valid but has errors: {form.errors}")
    
    def test_invalid_phone_formats(self):
        """Test that invalid phone formats are rejected"""
        invalid_phones = [
            '123456789',      # Too short
            '05940731578',    # Too long
            '0694073157',     # Wrong prefix (06 instead of 05)
            'abcd073157',     # Contains letters
            '1594073157',     # Wrong starting digit
        ]
        
        for idx, phone in enumerate(invalid_phones):
            form_data = {
                'name': 'Test User',
                'phone': phone,
                'national_id': f'12345678{idx}',
                'city': self.city.id,
                'password1': 'TestPass123!@#',
                'password2': 'TestPass123!@#',
            }
            form = PatientRegistrationForm(data=form_data)
            self.assertFalse(form.is_valid(), f"Phone {phone} should be invalid")
            self.assertIn('phone', form.errors)
    
    def test_duplicate_phone_number(self):
        """Test that duplicate phone numbers are rejected"""
        # Create first user
        CustomUser.objects.create_user(
            phone='0594073157',
            name='First User',
            national_id='123456789',
            password='TestPass123!@#'
        )
        
        # Try to register with same phone
        form_data = {
            'name': 'Second User',
            'phone': '0594073157',
            'national_id': '987654321',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        }
        form = PatientRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
    
    def test_phone_normalization(self):
        """Test that both phone formats normalize to the same value"""
        # Create user with 05 format
        CustomUser.objects.create_user(
            phone='0594073157',
            name='User One',
            national_id='123456789',
            password='TestPass123!@#'
        )
        
        # Try to register with +970 format of same number
        form_data = {
            'name': 'User Two',
            'phone': '+970594073157',  # Same number, different format
            'national_id': '987654321',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        }
        form = PatientRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)


class NationalIDValidationTest(TestCase):
    """Test national ID validation"""
    
    def setUp(self):
        self.city = City.objects.create(name="Nablus")
    
    def test_valid_national_id(self):
        """Test that valid 9-digit national ID is accepted"""
        form_data = {
            'name': 'Test User',
            'phone': '0594073157',
            'national_id': '123456789',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        }
        form = PatientRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), f"Form should be valid but has errors: {form.errors}")
    
    def test_invalid_national_id_formats(self):
        """Test that invalid national ID formats are rejected"""
        invalid_ids = [
            '12345678',       # Too short (8 digits)
            '1234567890',     # Too long (10 digits)
            '12345678a',      # Contains letter
            'abcdefghi',      # All letters
        ]
        
        for idx, national_id in enumerate(invalid_ids):
            form_data = {
                'name': 'Test User',
                'phone': f'059407315{idx}',
                'national_id': national_id,
                'city': self.city.id,
                'password1': 'TestPass123!@#',
                'password2': 'TestPass123!@#',
            }
            form = PatientRegistrationForm(data=form_data)
            self.assertFalse(form.is_valid(), f"National ID {national_id} should be invalid")
            self.assertIn('national_id', form.errors)
    
    def test_duplicate_national_id(self):
        """Test that duplicate national IDs are rejected"""
        # Create first user
        CustomUser.objects.create_user(
            phone='0594073157',
            name='First User',
            national_id='123456789',
            password='TestPass123!@#'
        )
        
        # Try to register with same national ID
        form_data = {
            'name': 'Second User',
            'phone': '0595555555',
            'national_id': '123456789',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        }
        form = PatientRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('national_id', form.errors)


class LoginWithPhoneTest(TestCase):
    """Test login with different phone formats"""
    
    def setUp(self):
        self.client = Client()
        self.city = City.objects.create(name="Nablus")
        
        # Create a test user with normalized phone
        self.user = CustomUser.objects.create_user(
            phone='0594073157',
            name='Test User',
            national_id='123456789',
            password='TestPass123!@#'
        )
        self.user.city = self.city
        self.user.role = 'PATIENT'
        self.user.save()
        
        PatientProfile.objects.create(user=self.user)
    
    def test_login_with_05_format(self):
        """Test login with 05XXXXXXXX format"""
        form = LoginForm(data={
            'phone': '0594073157',
            'password': 'TestPass123!@#'
        })
        self.assertTrue(form.is_valid())
    
    def test_login_with_plus970_format(self):
        """Test login with +970XXXXXXXX format"""
        form = LoginForm(data={
            'phone': '+970594073157',
            'password': 'TestPass123!@#'
        })
        self.assertTrue(form.is_valid())
        # Verify phone is normalized
        self.assertEqual(form.cleaned_data['phone'], '0594073157')
    
    def test_successful_login_both_formats(self):
        """Test actual login with both phone formats"""
        # Test with 05 format
        response = self.client.post(reverse('accounts:login'), {
            'phone': '0594073157',
            'password': 'TestPass123!@#'
        })
        self.assertEqual(response.status_code, 302)  # Should redirect after login
        self.client.logout()
        
        # Test with +970 format
        response = self.client.post(reverse('accounts:login'), {
            'phone': '+970594073157',
            'password': 'TestPass123!@#'
        })
        self.assertEqual(response.status_code, 302)  # Should redirect after login


class PatientRegistrationFlowTest(TestCase):
    """Test complete patient registration flow"""
    
    def setUp(self):
        self.client = Client()
        self.city = City.objects.create(name="Nablus")
        self.register_url = reverse('accounts:register_patient')
    
    def test_successful_registration(self):
        """Test complete successful registration"""
        response = self.client.post(self.register_url, {
            'name': 'Ahmed Mohammed',
            'phone': '0594073157',
            'national_id': '123456789',
            'city': self.city.id,
            'email': 'ahmed@example.com',
            'password1': 'StrongPass123!@#',
            'password2': 'StrongPass123!@#',
        })
        
        # Should redirect to home after successful registration
        self.assertEqual(response.status_code, 302, 
                        f"Expected redirect (302) but got {response.status_code}. "
                        f"Context: {response.context.get('form').errors if response.context and 'form' in response.context else 'No form errors'}")
        
        # User should be created
        self.assertTrue(CustomUser.objects.filter(phone='0594073157').exists())
        user = CustomUser.objects.get(phone='0594073157')
        
        # Check user properties
        self.assertEqual(user.name, 'Ahmed Mohammed')
        self.assertEqual(user.national_id, '123456789')
        self.assertEqual(user.role, 'PATIENT')
        self.assertEqual(user.city, self.city)
        
        # Patient profile should be created
        self.assertTrue(PatientProfile.objects.filter(user=user).exists())
    
    def test_registration_with_plus970_phone(self):
        """Test registration with +970 format phone"""
        response = self.client.post(self.register_url, {
            'name': 'Sara Ali',
            'phone': '+970594073158',
            'national_id': '987654321',
            'city': self.city.id,
            'password1': 'StrongPass123!@#',
            'password2': 'StrongPass123!@#',
        })
        
        # Should succeed
        self.assertEqual(response.status_code, 302,
                        f"Expected redirect (302) but got {response.status_code}. "
                        f"Context: {response.context.get('form').errors if response.context and 'form' in response.context else 'No form errors'}")
        
        # Phone should be normalized in database
        user = CustomUser.objects.get(national_id='987654321')
        self.assertEqual(user.phone, '0594073158')
    
    def test_registration_form_errors(self):
        """Test that form errors are displayed properly"""
        # Test with invalid phone
        response = self.client.post(self.register_url, {
            'name': 'Test User',
            'phone': '123456789',  # Invalid
            'national_id': '123456789',
            'city': self.city.id,
            'password1': 'TestPass123!@#',
            'password2': 'TestPass123!@#',
        })
        
        # Should stay on same page with errors
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'phone', 
                           'Invalid phone number format. Please enter a valid Palestinian phone number (e.g., 0594073157 or +970594073157).')


class NameValidationTest(TestCase):
    """Test name field validation"""
    
    def setUp(self):
        self.city = City.objects.create(name="Nablus")
    
    def test_valid_names(self):
        """Test that valid names are accepted"""
        valid_names = [
            'Ahmed Mohammed',
            'John Doe',
        ]
        
        for idx, name in enumerate(valid_names):
            form_data = {
                'name': name,
                'phone': f'059407315{idx}',
                'national_id': f'12345678{idx}',
                'city': self.city.id,
                'password1': 'TestPass123!@#',
                'password2': 'TestPass123!@#',
            }
            form = PatientRegistrationForm(data=form_data)
            self.assertTrue(form.is_valid(), f"Name '{name}' should be valid but got errors: {form.errors}")
    
    def test_invalid_names(self):
        """Test that invalid names are rejected"""
        invalid_names = [
            ('AB', 'name'),        # Too short
            ('123', 'name'),       # No letters
        ]

        for idx, (name, expected_field) in enumerate(invalid_names):
            form_data = {
                'name': name,
                'phone': f'059407316{idx}',
                'national_id': f'12345679{idx}',
                'city': self.city.id,
                'password1': 'TestPass123!@#',
                'password2': 'TestPass123!@#',
            }
            form = PatientRegistrationForm(data=form_data)
            self.assertFalse(form.is_valid(), f"Name '{name}' should be invalid")
            self.assertIn(expected_field, form.errors)


# ─────────────────────────────────────────────────────────────────────────────
# Main Doctor / Clinic Owner Signup Tests
# ─────────────────────────────────────────────────────────────────────────────

class MainDoctorSignupTest(TestCase):
    """Tests for the clinic owner single-page signup form and view."""

    OWNER_PHONE = "0594073100"
    OWNER_NID   = "123456789"
    CODE        = "TESTCODE123"

    def setUp(self):
        self.client = Client()
        self.city = City.objects.create(name="Ramallah")
        self.specialty = Specialty.objects.create(
            name="General Practice",
            name_ar="الممارسة العامة",
        )
        self.activation_code = ClinicActivationCode.objects.create(
            code=self.CODE,
            clinic_name="عيادة الاختبار",
            phone=self.OWNER_PHONE,
            national_id=self.OWNER_NID,
            plan_type="MONTHLY",
            subscription_expires_at=timezone.now() + timedelta(days=30),
            max_doctors=3,
        )
        self.url = reverse("accounts:register_main_doctor")

    def _valid_data(self, **overrides):
        data = {
            "activation_code": self.CODE,
            "first_name": "محمد",
            "last_name": "أحمد",
            "phone": self.OWNER_PHONE,
            "national_id": self.OWNER_NID,
            "email": "doctor@test.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
            "clinic_name": "عيادة الشفاء",
            "clinic_phone": "0569001234",
            "clinic_email": "",
            "clinic_address": "شارع النصر، مبنى 5",
            "clinic_city": self.city.id,
            "specialties": [self.specialty.id],
        }
        data.update(overrides)
        return data

    # ── 1. Happy path ─────────────────────────────────────────────────────
    def test_valid_signup_creates_clinic_with_pending_status(self):
        """Full valid form submission creates user + clinic with status=PENDING."""
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302, f"Expected redirect but got errors: {response.context['form'].errors if response.context and 'form' in response.context else ''}")

        user = CustomUser.objects.get(phone=self.OWNER_PHONE)
        self.assertEqual(user.role, "MAIN_DOCTOR")
        self.assertEqual(user.national_id, self.OWNER_NID)
        self.assertTrue(user.is_verified)

        clinic = Clinic.objects.get(main_doctor=user)
        self.assertEqual(clinic.status, "PENDING")
        self.assertIn(self.specialty, clinic.specialties.all())

        # Activation code marked as used
        self.activation_code.refresh_from_db()
        self.assertTrue(self.activation_code.is_used)
        self.assertEqual(self.activation_code.used_by, user)

    # ── 2. Activation code checks ─────────────────────────────────────────
    def test_invalid_code_blocked(self):
        """Wrong activation code is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(activation_code="WRONGCODE"))
        self.assertFalse(form.is_valid())
        self.assertIn("activation_code", form.errors)

    def test_used_code_blocked(self):
        """Already-used activation code is rejected."""
        self.activation_code.is_used = True
        self.activation_code.save()
        form = MainDoctorRegistrationForm(data=self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("activation_code", form.errors)

    def test_expired_code_blocked(self):
        """Expired activation code is rejected."""
        self.activation_code.expires_at = timezone.now() - timedelta(hours=1)
        self.activation_code.save()
        form = MainDoctorRegistrationForm(data=self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("activation_code", form.errors)

    def test_phone_mismatch_blocked(self):
        """Correct code but mismatched owner phone is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(phone="0591111111"))
        self.assertFalse(form.is_valid())
        self.assertIn("activation_code", form.errors)

    def test_national_id_mismatch_blocked(self):
        """Correct code but mismatched national ID is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(national_id="999999999"))
        self.assertFalse(form.is_valid())
        self.assertIn("activation_code", form.errors)

    # ── 3. Field validations ──────────────────────────────────────────────
    def test_specialties_required(self):
        """Form is invalid when no specialty is selected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(specialties=[]))
        self.assertFalse(form.is_valid())
        self.assertIn("specialties", form.errors)

    def test_password_mismatch(self):
        """Mismatched passwords are rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(confirm_password="DifferentPass!"))
        self.assertFalse(form.is_valid())
        self.assertIn("confirm_password", form.errors)

    def test_password_too_short(self):
        """Password shorter than 8 characters is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="short", confirm_password="short"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_password_no_uppercase(self):
        """Password without an uppercase letter is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="weakpass1!", confirm_password="weakpass1!"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_password_no_lowercase(self):
        """Password without a lowercase letter is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="WEAKPASS1!", confirm_password="WEAKPASS1!"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_password_no_digit(self):
        """Password without a digit is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="WeakPass!", confirm_password="WeakPass!"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_password_no_special_char(self):
        """Password without a special character is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="WeakPass1", confirm_password="WeakPass1"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_strong_password_accepted(self):
        """Password meeting all strength rules is accepted."""
        form = MainDoctorRegistrationForm(data=self._valid_data(password="StrongPass1!", confirm_password="StrongPass1!"))
        self.assertTrue(form.is_valid(), f"Expected valid but got: {form.errors}")

    def test_invalid_owner_phone_format(self):
        """Bad phone format for owner is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(phone="123456789"))
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_invalid_clinic_phone_format(self):
        """Bad phone format for clinic is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(clinic_phone="123456789"))
        self.assertFalse(form.is_valid())
        self.assertIn("clinic_phone", form.errors)

    def test_existing_patient_without_nid_is_reused(self):
        """Existing user with same phone (no national_id set) is reused — not duplicated."""
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE, name="Existing Patient", password="pass12345"
        )
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(
            response.status_code,
            302,
            f"Expected redirect but got errors: "
            f"{response.context['form'].errors if response.context and 'form' in response.context else ''}",
        )
        # Exactly one user with this phone — no duplicate created
        self.assertEqual(CustomUser.objects.filter(phone=self.OWNER_PHONE).count(), 1)
        patient.refresh_from_db()
        self.assertEqual(patient.role, "MAIN_DOCTOR")
        self.assertEqual(patient.national_id, self.OWNER_NID)
        # Both roles present
        self.assertIn("PATIENT", patient.roles)
        self.assertIn("MAIN_DOCTOR", patient.roles)

    # ── 4. Existing user identity scenarios ──────────────────────────────

    def test_existing_user_blocked_if_national_id_conflicts(self):
        """Existing user with same phone but a different national_id is blocked."""
        CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Other Person",
            national_id="999999999",  # different from OWNER_NID
            password="pass12345",
        )
        form = MainDoctorRegistrationForm(data=self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("national_id", form.errors)

    def test_existing_patient_role_updated_to_main_doctor(self):
        """Existing PATIENT primary role is set to MAIN_DOCTOR after clinic signup."""
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Patient User",
            national_id=self.OWNER_NID,
            password="pass12345",
            role="PATIENT",
        )
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.role, "MAIN_DOCTOR")
        # PATIENT role is preserved in the roles list
        self.assertIn("PATIENT", patient.roles)
        self.assertIn("MAIN_DOCTOR", patient.roles)

    def test_new_clinic_owner_has_both_roles(self):
        """A brand-new clinic owner gets both PATIENT and MAIN_DOCTOR in their roles list."""
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(phone=self.OWNER_PHONE)
        self.assertIn("PATIENT", user.roles)
        self.assertIn("MAIN_DOCTOR", user.roles)

    def test_existing_patient_password_updated(self):
        """Existing user's password is replaced by the one submitted in the signup form."""
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Patient User",
            national_id=self.OWNER_NID,
            password="OldPassword123!",
        )
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertTrue(patient.check_password("StrongPass123!"))

    def test_existing_patient_email_preserved_when_already_set(self):
        """Existing user's email is NOT overwritten even if a different email is submitted."""
        original_email = "original@patient.com"
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Patient User",
            national_id=self.OWNER_NID,
            email=original_email,
            password="pass12345",
        )
        response = self.client.post(self.url, self._valid_data(email="new@doctor.com"))
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.email, original_email)  # original preserved

    def test_existing_patient_email_filled_when_missing(self):
        """Form email is written to existing user when they have no email yet."""
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Patient User",
            national_id=self.OWNER_NID,
            email=None,
            password="pass12345",
        )
        response = self.client.post(self.url, self._valid_data(email="doctor@test.com"))
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.email, "doctor@test.com")

    def test_national_id_invalid_format(self):
        """National ID that is not 9 digits is rejected."""
        form = MainDoctorRegistrationForm(data=self._valid_data(national_id="12345", phone="0591234567"))
        self.assertFalse(form.is_valid())
        self.assertIn("national_id", form.errors)

    def test_clinic_email_optional(self):
        """Clinic email can be left blank."""
        form = MainDoctorRegistrationForm(data=self._valid_data(clinic_email=""))
        self.assertTrue(form.is_valid(), f"Form should be valid but has errors: {form.errors}")

    def test_clinic_email_validated_when_provided(self):
        """When clinic email is provided, it must be a valid email."""
        form = MainDoctorRegistrationForm(data=self._valid_data(clinic_email="not-an-email"))
        self.assertFalse(form.is_valid())
        self.assertIn("clinic_email", form.errors)

    def test_phone_plus970_format_accepted(self):
        """Owner phone in +970 format is normalized and accepted."""
        # Update activation code to normalized format so it matches
        self.activation_code.phone = "0594073100"
        self.activation_code.save()
        form = MainDoctorRegistrationForm(data=self._valid_data(phone="+970594073100"))
        self.assertTrue(form.is_valid(), f"Form should be valid: {form.errors}")

    # ── 5. PatientProfile auto-creation ──────────────────────────────────

    def test_new_clinic_owner_gets_patient_profile(self):
        """A brand-new clinic owner user automatically gets a PatientProfile."""
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(phone=self.OWNER_PHONE)
        self.assertTrue(
            PatientProfile.objects.filter(user=user).exists(),
            "PatientProfile must be created for a new clinic owner.",
        )

    def test_existing_patient_profile_reused_not_duplicated(self):
        """
        Existing patient who registers as clinic owner keeps their PatientProfile
        — no duplicate is created and no error is raised.
        """
        patient = CustomUser.objects.create_user(
            phone=self.OWNER_PHONE,
            name="Patient User",
            national_id=self.OWNER_NID,
            password="pass12345",
        )
        original_profile = PatientProfile.objects.create(user=patient)

        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)

        # Still exactly one PatientProfile for this user
        self.assertEqual(PatientProfile.objects.filter(user=patient).count(), 1)
        # Same row — pk unchanged
        patient.refresh_from_db()
        self.assertEqual(patient.patient_profile.pk, original_profile.pk)


# ─────────────────────────────────────────────────────────────────────────────
# ensure_patient_profile service tests
# ─────────────────────────────────────────────────────────────────────────────

class EnsurePatientProfileTest(TestCase):
    """Unit tests for patients.services.ensure_patient_profile."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            phone="0591234567",
            name="Test User",
            password="TestPass1!",
        )

    def test_creates_profile_when_none_exists(self):
        """First call creates a PatientProfile and returns created=True."""
        profile, created = ensure_patient_profile(self.user)
        self.assertTrue(created)
        self.assertEqual(profile.user, self.user)
        self.assertTrue(PatientProfile.objects.filter(user=self.user).exists())

    def test_reuses_existing_profile(self):
        """Second call returns the same profile with created=False."""
        profile1, _ = ensure_patient_profile(self.user)
        profile2, created = ensure_patient_profile(self.user)
        self.assertFalse(created)
        self.assertEqual(profile1.pk, profile2.pk)

    def test_idempotent_no_duplicate(self):
        """Calling multiple times never creates more than one row."""
        ensure_patient_profile(self.user)
        ensure_patient_profile(self.user)
        ensure_patient_profile(self.user)
        self.assertEqual(PatientProfile.objects.filter(user=self.user).count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# create_clinic_for_main_doctor service tests
# ─────────────────────────────────────────────────────────────────────────────

class CreateClinicServiceTest(TestCase):
    """
    Unit tests for clinics.services.create_clinic_for_main_doctor.

    Covers:
    - Happy path: all records created correctly in one call
    - ClinicStaff(role=MAIN_DOCTOR) is created
    - Activation code is fully marked as used
    - Atomic rollback: if any step raises, no partial records remain
    """

    def setUp(self):
        self.city = City.objects.create(name="Hebron")
        self.specialty = Specialty.objects.create(
            name="Cardiology",
            name_ar="أمراض القلب",
        )
        self.user = CustomUser.objects.create_user(
            phone="0591111111",
            name="Dr. Test",
            national_id="987654321",
            password="Pass1234!",
            role="MAIN_DOCTOR",
        )
        self.activation_code = ClinicActivationCode.objects.create(
            code="SVC001",
            clinic_name="عيادة الخدمة",
            phone="0591111111",
            national_id="987654321",
            plan_type="YEARLY",
            subscription_expires_at=timezone.now() + timedelta(days=365),
            max_doctors=5,
        )
        self.cleaned_data = {
            "clinic_name": "عيادة الاختبار",
            "clinic_address": "شارع الرئيسي 1",
            "clinic_city": self.city,
            "clinic_phone": "0569001111",
            "clinic_email": "clinic@test.com",
            "clinic_description": "وصف",
            "specialties": [self.specialty],
        }

    # ── Happy path ────────────────────────────────────────────────────────

    def test_creates_clinic_record(self):
        """Service creates a Clinic row with correct field values."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertIsNotNone(clinic.pk)
        self.assertEqual(clinic.name, "عيادة الاختبار")
        self.assertEqual(clinic.main_doctor, self.user)
        self.assertEqual(clinic.status, "PENDING")
        self.assertEqual(clinic.city, self.city)

    def test_sets_specialties(self):
        """Service wires up specialties M2M correctly."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertIn(self.specialty, clinic.specialties.all())

    def test_creates_clinic_staff_main_doctor(self):
        """Service creates a ClinicStaff row with role=MAIN_DOCTOR."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        staff = ClinicStaff.objects.get(clinic=clinic, user=self.user)
        self.assertEqual(staff.role, "MAIN_DOCTOR")
        self.assertEqual(staff.added_by, self.user)

    def test_marks_activation_code_used(self):
        """Service marks the activation code as used with all audit fields."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.activation_code.refresh_from_db()
        self.assertTrue(self.activation_code.is_used)
        self.assertEqual(self.activation_code.used_by, self.user)
        self.assertEqual(self.activation_code.used_by_clinic, clinic)
        self.assertIsNotNone(self.activation_code.used_at)

    # ── Atomic rollback ───────────────────────────────────────────────────

    def test_rollback_on_staff_creation_failure(self):
        """
        If ClinicStaff creation raises, the whole transaction rolls back:
        no Clinic row, no ClinicStaff row, activation code stays unused.
        """
        with patch(
            "clinics.services.ClinicStaff.objects.create",
            side_effect=Exception("simulated DB error"),
        ):
            with self.assertRaises(Exception):
                create_clinic_for_main_doctor(
                    self.user, self.cleaned_data, self.activation_code
                )

        self.assertEqual(Clinic.objects.count(), 0)
        self.assertEqual(ClinicStaff.objects.count(), 0)
        self.activation_code.refresh_from_db()
        self.assertFalse(self.activation_code.is_used)

    def test_rollback_on_activation_code_save_failure(self):
        """
        If saving the activation code raises, the whole transaction rolls back:
        no Clinic row, no ClinicStaff row.
        """
        with patch.object(
            self.activation_code.__class__,
            "save",
            side_effect=Exception("simulated save error"),
        ):
            with self.assertRaises(Exception):
                create_clinic_for_main_doctor(
                    self.user, self.cleaned_data, self.activation_code
                )

        self.assertEqual(Clinic.objects.count(), 0)
        self.assertEqual(ClinicStaff.objects.count(), 0)

    # ── Integration: view still creates ClinicStaff ───────────────────────

    def test_signup_view_creates_clinic_staff(self):
        """
        End-to-end: POST to register_main_doctor creates a ClinicStaff row
        with role=MAIN_DOCTOR for the new owner.
        """
        client = Client()
        city = City.objects.create(name="Nablus")
        specialty = Specialty.objects.create(name="Dermatology", name_ar="الجلدية")
        activation_code = ClinicActivationCode.objects.create(
            code="VIEW001",
            clinic_name="عيادة المشهد",
            phone="0592222222",
            plan_type="MONTHLY",
            subscription_expires_at=timezone.now() + timedelta(days=30),
            max_doctors=2,
            national_id="111222333",
        )
        data = {
            "activation_code": "VIEW001",
            "first_name": "سامي",
            "last_name": "خالد",
            "phone": "0592222222",
            "national_id": "111222333",
            "email": "sami@test.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
            "clinic_name": "عيادة المشهد",
            "clinic_phone": "0569009999",
            "clinic_email": "",
            "clinic_address": "شارع الحرية",
            "clinic_city": city.id,
            "specialties": [specialty.id],
        }
        response = client.post(reverse("accounts:register_main_doctor"), data)
        self.assertEqual(response.status_code, 302)

        user = CustomUser.objects.get(phone="0592222222")
        clinic = Clinic.objects.get(main_doctor=user)
        self.assertTrue(
            ClinicStaff.objects.filter(
                clinic=clinic, user=user, role="MAIN_DOCTOR"
            ).exists()
        )


class ClinicSubscriptionTest(TestCase):
    """
    Tests for subscription binding from ClinicActivationCode → ClinicSubscription.

    Covers:
    - Subscription record is created with correct field values
    - plan_type, expires_at, max_doctors are copied from the activation code
    - Subscription status defaults to ACTIVE
    - OneToOne constraint: clinic has exactly one subscription
    - Atomic rollback: if subscription creation fails, no Clinic is saved
    - Dashboard view exposes subscription context
    """

    def setUp(self):
        self.city = City.objects.create(name="Jenin")
        self.specialty = Specialty.objects.create(name="Neurology", name_ar="الأعصاب")
        self.user = CustomUser.objects.create_user(
            phone="0593333333",
            name="Dr. Sub",
            national_id="111333555",
            password="Pass1234!",
            role="MAIN_DOCTOR",
        )
        self.sub_expires = timezone.now() + timedelta(days=365)
        self.activation_code = ClinicActivationCode.objects.create(
            code="SUB001",
            clinic_name="عيادة الاشتراك",
            phone="0593333333",
            national_id="111333555",
            plan_type="YEARLY",
            subscription_expires_at=self.sub_expires,
            max_doctors=7,
        )
        self.cleaned_data = {
            "clinic_name": "عيادة الاشتراك",
            "clinic_address": "شارع النور",
            "clinic_city": self.city,
            "clinic_phone": "0569007777",
            "clinic_email": "",
            "clinic_description": "",
            "specialties": [self.specialty],
        }

    # ── Happy path ────────────────────────────────────────────────────────

    def test_subscription_is_created(self):
        """Service creates a ClinicSubscription row for the new clinic."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertTrue(ClinicSubscription.objects.filter(clinic=clinic).exists())

    def test_subscription_plan_type_copied(self):
        """plan_type is copied from the activation code."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertEqual(clinic.subscription.plan_type, "YEARLY")

    def test_subscription_expires_at_copied(self):
        """expires_at is copied from activation_code.subscription_expires_at."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        # Compare at second precision to avoid microsecond drift
        self.assertEqual(
            clinic.subscription.expires_at.replace(microsecond=0),
            self.sub_expires.replace(microsecond=0),
        )

    def test_subscription_max_doctors_copied(self):
        """max_doctors is copied from the activation code."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertEqual(clinic.subscription.max_doctors, 7)

    def test_subscription_status_defaults_to_active(self):
        """Newly created subscription status is ACTIVE."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertEqual(clinic.subscription.status, "ACTIVE")

    def test_subscription_one_to_one(self):
        """Each clinic has exactly one subscription (OneToOne enforced)."""
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.assertEqual(ClinicSubscription.objects.filter(clinic=clinic).count(), 1)

    def test_rollback_on_subscription_creation_failure(self):
        """
        If ClinicSubscription.objects.create raises, the whole transaction
        rolls back: no Clinic row, no ClinicStaff, activation code stays unused.
        """
        with patch(
            "clinics.services.ClinicSubscription.objects.create",
            side_effect=Exception("simulated subscription error"),
        ):
            with self.assertRaises(Exception):
                create_clinic_for_main_doctor(
                    self.user, self.cleaned_data, self.activation_code
                )

        self.assertEqual(Clinic.objects.count(), 0)
        self.assertEqual(ClinicStaff.objects.count(), 0)
        self.assertEqual(ClinicSubscription.objects.count(), 0)
        self.activation_code.refresh_from_db()
        self.assertFalse(self.activation_code.is_used)

    # ── Dashboard view ────────────────────────────────────────────────────

    def test_my_clinic_view_exposes_subscription(self):
        """my_clinic view passes subscription to template context."""
        from django.test import Client as TestClient
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        client = TestClient()
        client.force_login(self.user)
        response = client.get(reverse("clinics:my_clinic"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("subscription", response.context)
        self.assertEqual(response.context["subscription"], clinic.subscription)

    def test_my_clinic_view_exposes_clinic(self):
        """my_clinic view passes clinic to template context."""
        from django.test import Client as TestClient
        clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        client = TestClient()
        client.force_login(self.user)
        response = client.get(reverse("clinics:my_clinic"))
        self.assertEqual(response.context["clinic"], clinic)


class ClinicVerificationTest(TestCase):
    """
    Tests for the 4-step sequential clinic channel verification flow.

    Steps:
        1. verify_owner_phone   — SMS OTP to owner's phone
        2. verify_owner_email   — email OTP to owner's email
        3. verify_clinic_phone  — SMS OTP to clinic's phone
        4. verify_clinic_email  — email OTP to clinic's email (optional)
    """

    def _make_activation_code(self, code, phone, national_id):
        return ClinicActivationCode.objects.create(
            code=code,
            clinic_name="عيادة التحقق",
            phone=phone,
            national_id=national_id,
            plan_type="MONTHLY",
            subscription_expires_at=timezone.now() + timedelta(days=30),
            max_doctors=2,
        )

    def setUp(self):
        self.city = City.objects.create(name="Ramallah")
        self.specialty = Specialty.objects.create(name="Cardiology", name_ar="أمراض القلب")
        self.user = CustomUser.objects.create_user(
            phone="0591234510",
            name="Dr. Verify",
            national_id="123400010",
            email="doctor.verify@example.com",
            password="Pass1234!",
            role="MAIN_DOCTOR",
        )
        self.activation_code = self._make_activation_code("VER001", "0591234510", "123400010")
        self.cleaned_data = {
            "clinic_name": "عيادة التحقق",
            "clinic_address": "شارع التحقق",
            "clinic_city": self.city,
            "clinic_phone": "0569001234",
            "clinic_email": "clinic.verify@example.com",
            "clinic_description": "",
            "specialties": [self.specialty],
        }
        self.clinic = create_clinic_for_main_doctor(
            self.user, self.cleaned_data, self.activation_code
        )
        self.client = Client()
        self.client.force_login(self.user)

    # ------------------------------------------------------------------
    # 1. ClinicVerification record created atomically with clinic
    # ------------------------------------------------------------------
    def test_verification_record_created_with_clinic(self):
        """ClinicVerification is created atomically inside create_clinic_for_main_doctor."""
        self.assertTrue(ClinicVerification.objects.filter(clinic=self.clinic).exists())
        v = self.clinic.verification
        self.assertIsNone(v.owner_phone_verified_at)
        self.assertIsNone(v.owner_email_verified_at)
        self.assertIsNone(v.clinic_phone_verified_at)
        self.assertIsNone(v.clinic_email_verified_at)

    # ------------------------------------------------------------------
    # 2. verify_owner_phone: correct OTP marks timestamp, redirects
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_otp", return_value=(True, "verified"))
    @patch("clinics.views.send_email_otp", return_value=(True, "sent"))
    def test_verify_owner_phone_success(self, mock_send_email, mock_verify_otp):
        response = self.client.post(
            reverse("clinics:verify_owner_phone"),
            {"otp": "123456"},
        )
        self.assertRedirects(response, reverse("clinics:verify_owner_email"))
        self.clinic.verification.refresh_from_db()
        self.assertIsNotNone(self.clinic.verification.owner_phone_verified_at)
        # Next step pre-send was called
        mock_send_email.assert_called_once()

    # ------------------------------------------------------------------
    # 3. verify_owner_phone: expired OTP (nothing in cache) returns error
    # ------------------------------------------------------------------
    def test_verify_owner_phone_expired_otp(self):
        """With no OTP in cache, submission stays on the page with an error."""
        response = self.client.post(
            reverse("clinics:verify_owner_phone"),
            {"otp": "000000"},
        )
        # Re-renders with 200 (errors shown via messages)
        self.assertEqual(response.status_code, 200)
        self.clinic.verification.refresh_from_db()
        self.assertIsNone(self.clinic.verification.owner_phone_verified_at)

    # ------------------------------------------------------------------
    # 4. verify_owner_phone: too many wrong attempts
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_otp", return_value=(False, "Too many incorrect attempts. Please request a new OTP."))
    def test_verify_owner_phone_max_attempts(self, mock_verify_otp):
        response = self.client.post(
            reverse("clinics:verify_owner_phone"),
            {"otp": "000000"},
        )
        self.assertEqual(response.status_code, 200)
        self.clinic.verification.refresh_from_db()
        self.assertIsNone(self.clinic.verification.owner_phone_verified_at)

    # ------------------------------------------------------------------
    # 5. verify_owner_email: correct OTP marks timestamp, redirects
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_email_otp", return_value=(True, "verified"))
    @patch("clinics.views.request_otp", return_value=(True, "sent"))
    def test_verify_owner_email_success(self, mock_request_otp, mock_verify_email_otp):
        v = self.clinic.verification
        v.owner_phone_verified_at = timezone.now()
        v.save()

        response = self.client.post(
            reverse("clinics:verify_owner_email"),
            {"otp": "123456"},
        )
        self.assertRedirects(response, reverse("clinics:verify_clinic_phone"))
        v.refresh_from_db()
        self.assertIsNotNone(v.owner_email_verified_at)
        # Clinic phone OTP pre-send was called
        mock_request_otp.assert_called_once_with(self.clinic.phone)

    # ------------------------------------------------------------------
    # 6. verify_email_otp unit: expired OTP returns (False, message)
    # ------------------------------------------------------------------
    def test_verify_email_otp_unit_expired(self):
        from accounts.email_utils import verify_email_otp as _verify
        success, msg = _verify("nobody@example.com", "123456")
        self.assertFalse(success)
        self.assertIn("انتهت", msg)

    # ------------------------------------------------------------------
    # 7. verify_clinic_phone: correct OTP marks timestamp, redirects
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_otp", return_value=(True, "verified"))
    @patch("clinics.views.send_email_otp", return_value=(True, "sent"))
    def test_verify_clinic_phone_success(self, mock_send_email, mock_verify_otp):
        v = self.clinic.verification
        v.owner_phone_verified_at = timezone.now()
        v.owner_email_verified_at = timezone.now()
        v.save()

        response = self.client.post(
            reverse("clinics:verify_clinic_phone"),
            {"otp": "123456"},
        )
        # Clinic has email → redirect to step 4
        self.assertRedirects(response, reverse("clinics:verify_clinic_email"))
        v.refresh_from_db()
        self.assertIsNotNone(v.clinic_phone_verified_at)

    # ------------------------------------------------------------------
    # 8. verify_clinic_email: correct OTP marks timestamp, activates clinic
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_email_otp", return_value=(True, "verified"))
    def test_verify_clinic_email_success_activates_clinic(self, mock_verify_email_otp):
        v = self.clinic.verification
        v.owner_phone_verified_at = timezone.now()
        v.owner_email_verified_at = timezone.now()
        v.clinic_phone_verified_at = timezone.now()
        v.save()

        response = self.client.post(
            reverse("clinics:verify_clinic_email"),
            {"otp": "123456"},
        )
        self.assertRedirects(response, reverse("clinics:my_clinic"))
        v.refresh_from_db()
        self.assertIsNotNone(v.clinic_email_verified_at)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.status, "ACTIVE")

    # ------------------------------------------------------------------
    # 9. Clinic without email activates after 3 steps
    # ------------------------------------------------------------------
    @patch("clinics.views.verify_otp", return_value=(True, "verified"))
    def test_activation_without_clinic_email(self, mock_verify_otp):
        """Clinic becomes ACTIVE after step 3 when no clinic email is provided."""
        no_email_code = self._make_activation_code("VER002", "0591234511", "123400011")
        user2 = CustomUser.objects.create_user(
            phone="0591234511",
            name="Dr. NoEmail",
            national_id="123400011",
            email="noemail.dr@example.com",
            password="Pass1234!",
            role="MAIN_DOCTOR",
        )
        clinic2 = create_clinic_for_main_doctor(
            user2,
            {**self.cleaned_data, "clinic_email": "", "clinic_city": self.city},
            no_email_code,
        )
        v = clinic2.verification
        v.owner_phone_verified_at = timezone.now()
        v.owner_email_verified_at = timezone.now()
        v.save()

        client2 = Client()
        client2.force_login(user2)
        response = client2.post(
            reverse("clinics:verify_clinic_phone"),
            {"otp": "123456"},
        )
        self.assertRedirects(response, reverse("clinics:my_clinic"))
        clinic2.refresh_from_db()
        self.assertEqual(clinic2.status, "ACTIVE")

    # ------------------------------------------------------------------
    # 10. Sequential guard: visiting step 2 before step 1 done redirects back
    # ------------------------------------------------------------------
    def test_sequential_guard_redirects_to_pending_step(self):
        """Visiting verify_owner_email before step 1 is done → redirect to step 1."""
        response = self.client.get(reverse("clinics:verify_owner_email"))
        self.assertRedirects(response, reverse("clinics:verify_owner_phone"))

    # ------------------------------------------------------------------
    # 11. home_redirect routes PENDING clinic owner to first pending step
    # ------------------------------------------------------------------
    def test_home_redirect_routes_owner_to_first_pending_step(self):
        """A clinic owner with unverified channels is routed to verify_owner_phone."""
        response = self.client.get(reverse("accounts:home"))
        self.assertRedirects(response, reverse("clinics:verify_owner_phone"))