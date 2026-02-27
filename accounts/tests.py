from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from accounts.models import CustomUser, City
from accounts.forms import PatientRegistrationForm, LoginForm, MainDoctorRegistrationForm
from clinics.models import Clinic, ClinicActivationCode
from doctors.models import Specialty
from patients.models import PatientProfile


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

    def test_duplicate_owner_phone_blocked(self):
        """Phone already in use by another user is rejected."""
        CustomUser.objects.create_user(phone=self.OWNER_PHONE, name="Existing", password="pass12345")
        form = MainDoctorRegistrationForm(data=self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

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